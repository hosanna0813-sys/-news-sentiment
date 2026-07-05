"""議題分群頁 — 對應桌面版 app/workers/clustering_worker.py，改寫成背景 Thread
版本；核心 AI 呼叫完全重用 app/services/clustering/clustering_service.py。

人工調整（規格十的操作項目，網頁版以下拉選單/按鈕取代桌面版的拖曳）：
    搬移新聞到既有議題／新議題／標記不納入、議題改名、合併議題、刪除空議題。
"""
from __future__ import annotations

import json

from flask import Blueprint, redirect, render_template, request, url_for

from app.web.server import get_context
from app.web.job_runner import start_batch_job, BatchOutcome
from app.models.topic import Topic
from app.repositories.news_repository import NewsRepository
from app.repositories.topic_repository import TopicRepository
from app.repositories.feedback_repository import FeedbackRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.services.clustering.clustering_service import (
    split_insufficient_body, bucket_candidates, cluster_batch, merge_candidate_topics,
)
from app.services.feedback.feedback_service import log_feedback
from app.prompts.registry import get_active_prompt
from app.utils.text_utils import new_id

clustering_bp = Blueprint("clustering", __name__)

MAX_FEWSHOT_EXAMPLES = 10
MAX_EXISTING_TOPICS_IN_PROMPT = 30


@clustering_bp.route("/clustering")
def index():
    ctx = get_context()
    topics = ctx.topic_repo.list_active()
    news_by_topic = {t.topic_id: ctx.news_repo.list_by_topic(t.topic_id) for t in topics}
    unclustered = [it for it in ctx.news_repo.list_all()
                   if it.retained and not it.final_topic_id]
    return render_template("clustering.html", topics=topics, news_by_topic=news_by_topic,
                            unclustered=unclustered, job_id=request.args.get("job_id"))


def _build_human_examples(feedback_repo, news_repo) -> str:
    entries = feedback_repo.list_all()
    lines = []
    for e in entries:
        if e.entity_type != "clustering" or not (e.action or "").startswith("human_"):
            continue
        if not (e.human_final_value or "").strip():
            continue
        it = news_repo.get(e.entity_id)
        title = it.title if it else e.entity_id
        old = (e.ai_original_value or "").strip() or "（未分類）"
        lines.append(f"- 新聞《{title[:40]}》：AI 原歸「{old[:30]}」→ 人工改為「{e.human_final_value[:30]}」")
        if len(lines) >= MAX_FEWSHOT_EXAMPLES:
            break
    return "\n".join(lines)


@clustering_bp.route("/clustering/run", methods=["POST"])
def run():
    ctx = get_context()
    incremental = request.form.get("incremental") == "on"

    all_items = ctx.news_repo.list_retained_with_body()
    existing_topics = []
    existing_topic_ids = set()
    existing_name_lookup = {}
    if incremental:
        for t in ctx.topic_repo.list_active():
            members = ctx.news_repo.list_by_topic(t.topic_id)
            if not members:
                continue
            existing_topics.append({"topic_id": t.topic_id, "topic_name": t.topic_name,
                                     "sample_titles": [m.title for m in members[:3]]})
            if len(existing_topics) >= MAX_EXISTING_TOPICS_IN_PROMPT:
                break
        existing_topic_ids = {t["topic_id"] for t in existing_topics}
        existing_name_lookup = {t["topic_id"]: t["topic_name"] for t in existing_topics}
        all_items = [it for it in all_items
                     if not it.final_topic_id or it.final_topic_name == "正文不足待人工確認"]

    clusterable, insufficient = split_insufficient_body(all_items)
    ctx.news_repo.update_fields_bulk([
        {"row_id": it.row_id, "final_topic_id": "", "final_topic_name": "正文不足待人工確認"}
        for it in insufficient
    ])

    if not clusterable:
        return redirect(url_for("clustering.index"))

    human_examples = _build_human_examples(FeedbackRepository(), NewsRepository())
    buckets = bucket_candidates(clusterable, ctx.settings.api.batch_size_clustering)
    clustering_cfg = get_active_prompt(ctx.prompt_repo, "topic_clustering")
    clustering_schema = json.loads(clustering_cfg.tool_schema_json)
    merge_cfg = get_active_prompt(ctx.prompt_repo, "topic_merge")
    merge_schema = json.loads(merge_cfg.tool_schema_json)
    title_lookup = {it.row_id: it.title for it in clusterable}

    def process(bucket):
        topics = cluster_batch(
            ctx.gateway, bucket, clustering_cfg.system_prompt, clustering_cfg.user_template,
            clustering_schema["name"], clustering_schema["schema"],
            existing_topics=existing_topics, human_examples=human_examples,
        )
        candidate_topics = []
        existing_updates = []
        for t in topics:
            if t["topic_id"] in existing_topic_ids:
                for rid in t.get("member_row_ids", []):
                    existing_updates.append({
                        "row_id": rid, "final_topic_id": t["topic_id"],
                        "final_topic_name": existing_name_lookup.get(t["topic_id"], ""),
                        "clustering_reason": t.get("reason", ""), "clustering_confidence": t.get("confidence", 0.5),
                    })
            else:
                t["sample_titles"] = [title_lookup.get(rid, "") for rid in t.get("member_row_ids", [])[:3]]
                candidate_topics.append(t)

        thread_news_repo = NewsRepository()
        if existing_updates:
            thread_news_repo.update_fields_bulk(existing_updates)

        final_topics = []
        news_updates = []
        if candidate_topics:
            merged_groups = merge_candidate_topics(
                ctx.gateway, candidate_topics, merge_cfg.system_prompt, merge_cfg.user_template,
                merge_schema["name"], merge_schema["schema"],
            )
            candidate_by_id = {t["topic_id"]: t for t in candidate_topics}
            merged_source_ids = set()
            for group in merged_groups:
                src_ids = [s for s in group.get("source_topic_ids", []) if s in candidate_by_id]
                if not src_ids:
                    continue
                final_id = new_id("ftopic_")
                final_name = group["final_topic_name"]
                final_topics.append(Topic(topic_id=final_id, topic_name=final_name))
                for src_id in src_ids:
                    merged_source_ids.add(src_id)
                    src = candidate_by_id[src_id]
                    for rid in src.get("member_row_ids", []):
                        news_updates.append({"row_id": rid, "final_topic_id": final_id,
                                              "final_topic_name": final_name,
                                              "clustering_reason": src.get("reason", ""),
                                              "clustering_confidence": src.get("confidence", 0.5)})
            for t in candidate_topics:
                if t["topic_id"] in merged_source_ids:
                    continue
                final_id = new_id("ftopic_")
                final_topics.append(Topic(topic_id=final_id, topic_name=t["topic_name"]))
                for rid in t.get("member_row_ids", []):
                    news_updates.append({"row_id": rid, "final_topic_id": final_id,
                                          "final_topic_name": t["topic_name"],
                                          "clustering_reason": t.get("reason", ""),
                                          "clustering_confidence": t.get("confidence", 0.5)})

        thread_topic_repo = TopicRepository()
        if final_topics:
            thread_topic_repo.upsert_many(final_topics)
        if news_updates:
            thread_news_repo.update_fields_bulk(news_updates)
        return BatchOutcome(success=True, success_count=len(bucket))

    job_id = start_batch_job("clustering", buckets, process, JobRepository(), BatchRepository())
    return redirect(url_for("clustering.index", job_id=job_id))


@clustering_bp.route("/clustering/move", methods=["POST"])
def move():
    ctx = get_context()
    row_id = request.form["row_id"]
    target = request.form["target"]  # 既有 topic_id / "__new__" / "__unassign__"
    new_topic_name = request.form.get("new_topic_name", "").strip()
    item = ctx.news_repo.get(row_id)
    if item is None:
        return redirect(url_for("clustering.index"))

    old_topic_name = item.final_topic_name or "（未分類）"

    if target == "__unassign__":
        ctx.news_repo.update_fields(row_id, {"final_topic_id": "", "final_topic_name": ""})
        new_label = "（未分類）"
    elif target == "__new__":
        new_topic = Topic(topic_id=new_id("ftopic_"), topic_name=new_topic_name or "新議題")
        ctx.topic_repo.upsert_one(new_topic)
        ctx.news_repo.update_fields(row_id, {"final_topic_id": new_topic.topic_id,
                                              "final_topic_name": new_topic.topic_name})
        new_label = new_topic.topic_name
    else:
        target_topic = ctx.topic_repo.get(target)
        if target_topic is None:
            return redirect(url_for("clustering.index"))
        ctx.news_repo.update_fields(row_id, {"final_topic_id": target_topic.topic_id,
                                              "final_topic_name": target_topic.topic_name})
        new_label = target_topic.topic_name

    log_feedback(FeedbackRepository(), batch_id="", entity_type="clustering", entity_id=row_id,
                 ai_original_value=old_topic_name, human_final_value=new_label,
                 action="human_move", operator="web")
    return redirect(url_for("clustering.index"))


@clustering_bp.route("/clustering/rename", methods=["POST"])
def rename():
    ctx = get_context()
    topic_id = request.form["topic_id"]
    new_name = request.form.get("new_name", "").strip()
    if new_name:
        ctx.topic_repo.update_fields(topic_id, {"topic_name": new_name})
        for it in ctx.news_repo.list_by_topic(topic_id):
            ctx.news_repo.update_fields(it.row_id, {"final_topic_name": new_name})
    return redirect(url_for("clustering.index"))


@clustering_bp.route("/clustering/merge", methods=["POST"])
def merge():
    ctx = get_context()
    source_id = request.form["source_topic_id"]
    target_id = request.form["target_topic_id"]
    if source_id == target_id:
        return redirect(url_for("clustering.index"))
    target_topic = ctx.topic_repo.get(target_id)
    if target_topic is None:
        return redirect(url_for("clustering.index"))
    for it in ctx.news_repo.list_by_topic(source_id):
        ctx.news_repo.update_fields(it.row_id, {"final_topic_id": target_id,
                                                 "final_topic_name": target_topic.topic_name})
    ctx.topic_repo.mark_merged(source_id, target_id)
    return redirect(url_for("clustering.index"))


@clustering_bp.route("/clustering/delete", methods=["POST"])
def delete():
    ctx = get_context()
    topic_id = request.form["topic_id"]
    if not ctx.news_repo.list_by_topic(topic_id):
        ctx.topic_repo.delete(topic_id)
    return redirect(url_for("clustering.index"))
