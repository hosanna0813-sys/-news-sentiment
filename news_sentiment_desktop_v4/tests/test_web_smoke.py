"""網頁版（app/web）煙霧測試——沿用 conftest.py 的隔離資料庫慣例（每個測試用
獨立的 NEWS_SENTIMENT_DATA_DIR，不共用真實 %APPDATA%/~/.news_sentiment_desktop_v4），
不呼叫真實 Anthropic API 或 Gmail 網路（gateway.call_with_tool 一律 mock）。
"""
from __future__ import annotations

import json
import re
import time

import pytest

from app.models.news import NewsItem
from app.models.topic import Topic

WEB_PASSWORD = "test-password"


@pytest.fixture()
def web_app(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_SENTIMENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEB_SHARED_PASSWORD", WEB_PASSWORD)
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from app.web.server import create_app
    app = create_app()
    app.testing = True
    return app


@pytest.fixture()
def client(web_app):
    return web_app.test_client()


@pytest.fixture()
def logged_in_client(client):
    client.post("/login", data={"password": WEB_PASSWORD})
    return client


class FakeResult:
    def __init__(self, data):
        self.data = data
        self.raw_text = ""
        self.model_used = "fake"
        self.stop_reason = "tool_use"
        self.usage = {}


def _wait_job(client, job_url_location, timeout=5.0):
    job_id = job_url_location.split("job_id=")[1]
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        status = client.get(f"/jobs/{job_id}/status").get_json()
        if status["status"] in ("completed", "cancelled", "failed"):
            break
        time.sleep(0.05)
    return status


def test_requires_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_wrong_password_rejected(client):
    resp = client.post("/login", data={"password": "nope"})
    assert resp.status_code == 200
    assert "密碼錯誤".encode("utf-8") in resp.data


def test_login_then_dashboard(logged_in_client):
    resp = logged_in_client.get("/")
    assert resp.status_code == 200


def test_settings_save_and_reload(logged_in_client, web_app):
    resp = logged_in_client.post(
        "/settings",
        data={"sender_email_filter": "news@example.com", "subject_keyword": "監測報告"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    ctx = web_app.config["APP_CONTEXT"]
    ctx.reload_settings()
    assert ctx.settings.gmail.sender_email_filter == "news@example.com"
    assert ctx.settings.gmail.subject_keyword == "監測報告"


def test_gmail_oauth_start_then_callback_passes_same_code_verifier(client, monkeypatch):
    """迴歸測試：/start 產生的 PKCE code_verifier 必須經由 session 原封不動地
    傳到 /callback 重建的 Flow 物件上，否則 Google 會回
    'invalid_grant: Missing code verifier.'（這是這次修的正式 bug）。"""
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "csecret")
    client.post("/login", data={"password": WEB_PASSWORD})

    class _FakeStartFlow:
        code_verifier = None
        autogenerate_code_verifier = False

        def authorization_url(self, **kw):
            self.code_verifier = "fake-verifier-abc"
            return "https://accounts.google.com/fake-auth-url", "fake-state-xyz"

    captured = {}

    class _FakeCallbackFlow:
        def __init__(self, *a, **kw):
            self.code_verifier = None
        credentials = None

    import app.web.routes.settings as settings_module

    def fake_build_web_flow(client_id, client_secret, redirect_uri, state=None):
        if "start_called" not in captured:
            captured["start_called"] = True
            return _FakeStartFlow()
        captured["callback_state"] = state
        return _FakeCallbackFlow()

    def fake_complete_web_flow(flow, authorization_response):
        captured["callback_code_verifier"] = flow.code_verifier

    monkeypatch.setattr(settings_module, "build_web_flow", fake_build_web_flow)
    monkeypatch.setattr(settings_module, "complete_web_flow", fake_complete_web_flow)

    resp = client.get("/gmail/oauth/start", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://accounts.google.com/fake-auth-url"

    resp = client.get("/gmail/oauth/callback?code=abc&state=fake-state-xyz", follow_redirects=True)
    assert resp.status_code == 200
    assert captured["callback_code_verifier"] == "fake-verifier-abc"
    assert captured["callback_state"] == "fake-state-xyz"


def test_settings_shows_exact_oauth_redirect_uri(client, monkeypatch):
    # redirect_uri_mismatch（Google 400 錯誤）多半是使用者手動猜測/謄寫網址時
    # 打錯，設定頁改成直接顯示程式實際會用的 redirect_uri 供逐字複製，
    # 這裡驗證顯示的字串跟請求時的 scheme/host 完全一致。
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "csecret")
    base_url = "https://example.onrender.com"
    client.post("/login", data={"password": WEB_PASSWORD}, base_url=base_url)
    resp = client.get("/settings", base_url=base_url)
    assert resp.status_code == 200
    assert b"https://example.onrender.com/gmail/oauth/callback" in resp.data


def test_retention_override_persists(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    ctx.news_repo.upsert_one(NewsItem(row_id="r1", title="新聞一", source="來源A"))

    resp = logged_in_client.post(
        "/retention/override", data={"row_id": "r1", "retained": "on"}, follow_redirects=True
    )
    assert resp.status_code == 200
    item = ctx.news_repo.get("r1")
    assert item.retained == 1
    assert item.retention_status == "留用"
    assert item.retention_judged_by == "human"


def test_retention_override_via_fetch_returns_204_without_redirect(logged_in_client, web_app):
    # 勾選留用用背景 fetch 送出（見 retention.html toggleRetained()），不應該整頁
    # 重新導向——不然使用者每點一次勾選，畫面就會跳回頁面最上方，捲動位置全毀。
    ctx = web_app.config["APP_CONTEXT"]
    ctx.news_repo.upsert_one(NewsItem(row_id="r1", title="新聞一", source="來源A"))

    resp = logged_in_client.post(
        "/retention/override",
        data={"row_id": "r1", "retained": "on"},
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 204
    assert ctx.news_repo.get("r1").retained == 1


def test_retention_page_shows_collapsed_full_body_and_checkbox_first_column(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    full_body = "這是完整正文內容。" * 20
    ctx.news_repo.upsert_one(NewsItem(row_id="r1", title="新聞一", source="來源A", body_text=full_body))
    ctx.news_repo.upsert_one(NewsItem(row_id="r2", title="新聞二", source="來源B", body_text=""))

    resp = logged_in_client.get("/retention")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    # 正文放在每則新聞下方的 <details>（預設收合），且是完整全文，不截斷
    assert "<details>" in html
    assert full_body in html
    assert "尚無正文" in html
    # 留用 checkbox 欄位要在標題欄位「之前」出現（最左欄）
    assert html.index('name="retained"') < html.index("新聞一")


def test_retention_page_auto_shows_progress_for_inflight_job_without_query_param(
    logged_in_client, web_app,
):
    # 使用者重新整理頁面、網址上沒有 ?job_id=... 時，若資料庫裡還有一個
    # 未完成的留用初判工作，頁面仍要主動顯示進度條，不能讓人誤以為卡住了。
    from app.repositories.job_repository import JobRepository
    from app.models.job import JobRecord

    job_repo = JobRepository()
    job = JobRecord.new("retention", 10)
    job_repo.create(job)
    job_repo.update(job.job_id, {"status": "running"})

    resp = logged_in_client.get("/retention")
    assert resp.status_code == 200
    assert b"progress-wrap" in resp.data
    # 「執行」按鈕一律照樣顯示——不能讓一筆因部署重啟而永遠停在 running 的殘留
    # 工作紀錄，永久擋掉使用者重新觸發的能力（這是先前版本的真實 bug）。
    assert b"/retention/run" in resp.data


def test_retention_run_background_job(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    for i in range(1, 4):
        ctx.news_repo.upsert_one(NewsItem(row_id=f"r{i}", title=f"新聞{i}", source="來源"))

    def fake_call_with_tool(task, system_prompt, user_content, tool_name, tool_schema, **kw):
        if task == "retention_prefilter":
            return FakeResult({"judgements": [
                {"row_id": f"r{i}", "is_relevant": True} for i in range(1, 4)]})
        if task == "retention_judgement":
            return FakeResult({"judgements": [
                {"row_id": f"r{i}", "business_relevance": 30, "response_requirement": 10,
                 "political_sensitivity": 5, "media_attention": 5, "public_impact": 5,
                 "executive_bonus": 0, "final_score": 55, "priority_stars": 4,
                 "should_respond": True, "is_moi_core_business": False} for i in range(1, 4)]})
        raise AssertionError(f"unexpected task {task}")

    ctx.gateway.call_with_tool = fake_call_with_tool

    resp = logged_in_client.post("/retention/run", follow_redirects=False)
    assert resp.status_code == 302
    status = _wait_job(logged_in_client, resp.headers["Location"])
    assert status["status"] == "completed"
    assert ctx.news_repo.get("r1").retention_status == "留用"


def test_clustering_manual_move_persists_and_logs_feedback(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    ctx.news_repo.upsert_one(NewsItem(row_id="r1", title="新聞一", source="來源A"))
    ctx.topic_repo.upsert_one(Topic(topic_id="t1", topic_name="議題A"))
    ctx.news_repo.update_fields("r1", {"final_topic_id": "t1", "final_topic_name": "議題A"})

    resp = logged_in_client.post(
        "/clustering/move",
        data={"row_id": "r1", "target": "__new__", "new_topic_name": "議題B"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    item = ctx.news_repo.get("r1")
    assert item.final_topic_name == "議題B"
    assert item.final_topic_id != "t1"

    feedback_entries = ctx.feedback_repo.list_all(entity_type="clustering")
    assert any(e.entity_id == "r1" and e.action == "human_move" for e in feedback_entries)


def test_clustering_move_via_fetch_returns_204_without_redirect(logged_in_client, web_app):
    # 拖曳看板用背景 fetch 送出移動請求（見 clustering.html handleDrop()），
    # 前端會直接搬動 DOM 節點，不應該整頁重新導向重繪。
    ctx = web_app.config["APP_CONTEXT"]
    ctx.topic_repo.upsert_one(Topic(topic_id="t1", topic_name="議題A"))
    ctx.news_repo.upsert_one(NewsItem(row_id="r1", title="新聞一", source="來源A"))

    resp = logged_in_client.post(
        "/clustering/move",
        data={"row_id": "r1", "target": "t1"},
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 204
    assert ctx.news_repo.get("r1").final_topic_id == "t1"


def test_clustering_create_topic(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    resp = logged_in_client.post("/clustering/create_topic", data={"name": "手動建立的議題"},
                                  follow_redirects=True)
    assert resp.status_code == 200
    topics = ctx.topic_repo.list_active()
    assert any(t.topic_name == "手動建立的議題" for t in topics)


def test_clustering_page_board_layout_and_preview_data(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    ctx.topic_repo.upsert_one(Topic(topic_id="t1", topic_name="議題A"))
    ctx.news_repo.upsert_one(NewsItem(
        row_id="r1", title="新聞一", source="來源A", body_text="完整正文內容",
        retained=True, final_topic_id="t1", final_topic_name="議題A",
    ))
    ctx.news_repo.upsert_one(NewsItem(row_id="r2", title="新聞二", source="來源B", retained=True))

    resp = logged_in_client.get("/clustering")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "cluster-board" in html
    assert "news-card" in html
    # 議題選單改用下拉選單而非一排按鈕，避免議題一多時按鈕換行撐高，跟左欄
    # 未分類新聞的格子對不齊（曾是實際回報的排版問題）。
    assert 'id="topic-select"' in html
    assert "topic-chip" not in html

    match = re.search(r"const NEWS_DATA = (\{.*?\});", html)
    assert match is not None
    news_data = json.loads(match.group(1))
    assert news_data["r1"]["body_text"] == "完整正文內容"
    assert news_data["r2"]["body_text"] == ""


def test_clustering_page_shows_run_button_even_with_stale_running_job(logged_in_client, web_app):
    # 迴歸測試：先前若偵測到（可能是部署重啟遺留的）running 工作就整個藏起
    # 「執行」按鈕，一旦那筆工作紀錄卡死在 running（Render 重新部署會直接砍掉
    # 背景執行緒，資料庫裡的狀態永遠不會被改成 completed），使用者就再也無法
    # 觸發新的分群工作——這是實際回報的 bug，按鈕必須一律顯示。
    from app.repositories.job_repository import JobRepository
    from app.models.job import JobRecord

    job_repo = JobRepository()
    job = JobRecord.new("clustering", 10)
    job_repo.create(job)
    job_repo.update(job.job_id, {"status": "running"})

    resp = logged_in_client.get("/clustering")
    assert resp.status_code == 200
    assert b"progress-wrap" in resp.data
    assert b"/clustering/run" in resp.data


def test_clustering_run_background_job(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    for i in range(1, 5):
        ctx.news_repo.upsert_one(NewsItem(
            row_id=f"r{i}", title=f"新聞{i}", source="來源", retained=True,
            body_text="這是足夠長的測試正文內容。" * 10, body_word_count=200,
        ))

    def fake_call_with_tool(task, system_prompt, user_content, tool_name, tool_schema, **kw):
        if task == "topic_clustering":
            return FakeResult({"topics": [
                {"topic_id": "cand_1", "topic_name": "議題X",
                 "member_row_ids": ["r1", "r2"], "reason": "", "confidence": 0.9},
                {"topic_id": "cand_2", "topic_name": "議題Y",
                 "member_row_ids": ["r3", "r4"], "reason": "", "confidence": 0.9},
            ]})
        if task == "topic_merge":
            return FakeResult({"merged_groups": [
                {"source_topic_ids": ["cand_1"], "final_topic_name": "議題X", "reason": ""},
                {"source_topic_ids": ["cand_2"], "final_topic_name": "議題Y", "reason": ""},
            ]})
        raise AssertionError(f"unexpected task {task}")

    ctx.gateway.call_with_tool = fake_call_with_tool

    resp = logged_in_client.post("/clustering/run", data={"incremental": ""}, follow_redirects=False)
    assert resp.status_code == 302
    status = _wait_job(logged_in_client, resp.headers["Location"])
    assert status["status"] == "completed"

    topics = {t.topic_name for t in ctx.topic_repo.list_active()}
    assert topics == {"議題X", "議題Y"}
    assert ctx.news_repo.get("r1").final_topic_name == "議題X"
    assert ctx.news_repo.get("r3").final_topic_name == "議題Y"


def test_export_download_returns_docx(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    ctx.topic_repo.upsert_one(Topic(topic_id="t1", topic_name="議題A"))
    ctx.news_repo.upsert_one(NewsItem(
        row_id="r1", title="新聞一", source="來源A", url="http://example.com/1",
        final_topic_id="t1", final_topic_name="議題A",
    ))

    resp = logged_in_client.get("/export/download")
    assert resp.status_code == 200
    assert resp.content_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert len(resp.data) > 0


def test_keyword_taxonomy_saved_and_injected_into_context(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    resp = logged_in_client.post(
        "/settings",
        data={"sender_email_filter": "", "subject_keyword": "",
              "keyword_taxonomy": "警政/治安\t槍擊|酒駕|掃黑"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    ctx.reload_settings()
    assert "槍擊" in ctx.settings.keyword_taxonomy

    from app.web.routes.retention import build_keyword_context
    context_text = build_keyword_context(ctx)
    assert "槍擊" in context_text
    assert "業務關注議題" in context_text


def test_clustering_move_to_not_retained_and_rescue_back(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    ctx.news_repo.upsert_one(NewsItem(row_id="r1", title="新聞一", source="來源A", retained=True))

    # 標記為未留用
    resp = logged_in_client.post(
        "/clustering/move", data={"row_id": "r1", "target": "__not_retained__"}, follow_redirects=True,
    )
    assert resp.status_code == 200
    item = ctx.news_repo.get("r1")
    assert item.retained == 0
    assert item.retention_judged_by == "human"

    # 從未留用清單拖到未分類，應自動搶救回留用
    resp = logged_in_client.post(
        "/clustering/move", data={"row_id": "r1", "target": "__unassign__"}, follow_redirects=True,
    )
    assert resp.status_code == 200
    item = ctx.news_repo.get("r1")
    assert item.retained == 1
    assert item.retention_status == "留用"


def test_clustering_page_shows_not_retained_column(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    ctx.news_repo.upsert_one(NewsItem(row_id="r1", title="被排除的新聞", source="來源A", retained=False))

    resp = logged_in_client.get("/clustering")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "not-retained-list" in html
    assert "未留用新聞" in html


def test_pipeline_run_end_to_end(logged_in_client, web_app, monkeypatch):
    ctx = web_app.config["APP_CONTEXT"]
    ctx.settings.gmail.sender_email_filter = "news@example.com"
    ctx.save_settings()

    fake_items = [
        NewsItem(row_id=f"r{i}", title=f"新聞{i}", source="來源",
                 body_text=("正文內容" * 20), body_word_count=200)
        for i in range(1, 5)
    ]

    class FakeImportResult:
        items = fake_items

    import app.web.routes.pipeline as pipeline_module
    monkeypatch.setattr(
        pipeline_module, "import_from_gmail",
        lambda gmail_settings, start_dt, end_dt: FakeImportResult(),
    )

    def fake_call_with_tool(task, system_prompt, user_content, tool_name, tool_schema, **kw):
        if task == "retention_prefilter":
            return FakeResult({"judgements": [
                {"row_id": it.row_id, "is_relevant": True} for it in fake_items]})
        if task == "retention_judgement":
            return FakeResult({"judgements": [
                {"row_id": it.row_id, "business_relevance": 30, "response_requirement": 10,
                 "political_sensitivity": 5, "media_attention": 5, "public_impact": 5,
                 "executive_bonus": 0, "final_score": 55, "priority_stars": 4,
                 "should_respond": True, "is_moi_core_business": False} for it in fake_items]})
        if task == "topic_clustering":
            return FakeResult({"topics": [
                {"topic_id": "cand_1", "topic_name": "議題X",
                 "member_row_ids": [it.row_id for it in fake_items], "reason": "", "confidence": 0.9},
            ]})
        if task == "topic_merge":
            return FakeResult({"merged_groups": [
                {"source_topic_ids": ["cand_1"], "final_topic_name": "議題X", "reason": ""},
            ]})
        raise AssertionError(f"unexpected task {task}")

    ctx.gateway.call_with_tool = fake_call_with_tool

    resp = logged_in_client.post(
        "/pipeline/run", data={"start_dt": "2026-01-01T00:00", "end_dt": "2026-01-02T00:00"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/clustering?job_id=")

    status = _wait_job(logged_in_client, resp.headers["Location"], timeout=10)
    assert status["status"] == "completed"
    assert status["params"]["stage_label"] == "完成"

    assert len(ctx.news_repo.list_all()) == 4
    for it in fake_items:
        assert ctx.news_repo.get(it.row_id).retained == 1
    topics = ctx.topic_repo.list_active()
    assert any(t.topic_name == "議題X" for t in topics)


def test_pipeline_run_reports_gmail_import_failure(logged_in_client, web_app, monkeypatch):
    import app.web.routes.pipeline as pipeline_module
    from app.services.gmail.gmail_importer import GmailImportError

    def fake_import(gmail_settings, start_dt, end_dt):
        raise GmailImportError("找不到符合條件的信件")

    monkeypatch.setattr(pipeline_module, "import_from_gmail", fake_import)

    resp = logged_in_client.post(
        "/pipeline/run", data={"start_dt": "2026-01-01T00:00", "end_dt": "2026-01-02T00:00"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    status = _wait_job(logged_in_client, resp.headers["Location"], timeout=10)
    assert status["status"] == "failed"
    assert "找不到符合條件的信件" in status["params"]["stage_label"]


def test_clear_data_removes_news_and_topics_but_keeps_feedback_log(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    ctx.news_repo.upsert_one(NewsItem(row_id="r1", title="新聞一", source="來源A"))
    ctx.topic_repo.upsert_one(Topic(topic_id="t1", topic_name="議題A"))
    logged_in_client.post("/retention/override", data={"row_id": "r1", "retained": "on"},
                           follow_redirects=True)
    assert len(ctx.feedback_repo.list_all()) == 1

    resp = logged_in_client.post("/clear_data", follow_redirects=True)
    assert resp.status_code == 200

    assert ctx.news_repo.list_all() == []
    assert ctx.topic_repo.list_active() == []
    # 人工修正回饋紀錄要保留，供之後訓練 AI 判斷用
    assert len(ctx.feedback_repo.list_all()) == 1


def test_retention_few_shot_examples_survive_clear_data(logged_in_client, web_app):
    """迴歸測試：override() 把新聞標題存進 feedback 的 reason 快照，
    「清除資料」把 news 表清空後，_build_human_examples() 仍要能組出範例
    （不能因為 news_repo.get() 找不到對應新聞就整筆默默跳過）。"""
    ctx = web_app.config["APP_CONTEXT"]
    ctx.news_repo.upsert_one(NewsItem(
        row_id="r1", title="某某部門重大政策新聞", source="來源A",
        retention_status="AI建議不留用",
    ))
    logged_in_client.post("/retention/override", data={"row_id": "r1", "retained": "on"},
                           follow_redirects=True)

    logged_in_client.post("/clear_data", follow_redirects=True)
    assert ctx.news_repo.get("r1") is None

    from app.web.routes.retention import _build_human_examples
    examples = _build_human_examples(ctx.feedback_repo, ctx.news_repo)
    assert "某某部門重大政策新聞" in examples


def test_pipeline_survives_concurrent_page_requests(logged_in_client, web_app, monkeypatch):
    """迴歸測試：一鍵完成的背景執行緒過去直接沿用 ctx.news_repo/ctx.topic_repo/
    ctx.prompt_repo（跟主執行緒共用同一個 SQLite connection 物件），使用者若在
    流程跑的同時瀏覽 /clustering 等頁面（主執行緒也會用到同一批 ctx repo），
    兩個執行緒真的並行存取同一個 connection，曾在實際部署中造成流程悄悄跑不完、
    畫面卻沒有任何錯誤訊息。現在背景執行緒改用 _ThreadLocalCtx 各自重建 repo，
    這裡在流程跑的同時反覆打其他頁面確認不會出錯、流程仍能正常跑完。
    """
    ctx = web_app.config["APP_CONTEXT"]
    ctx.settings.gmail.sender_email_filter = "news@example.com"
    ctx.save_settings()

    fake_items = [
        NewsItem(row_id=f"r{i}", title=f"新聞{i}", source="來源",
                 body_text=("正文內容" * 20), body_word_count=200)
        for i in range(1, 6)
    ]

    class FakeImportResult:
        items = fake_items

    import app.web.routes.pipeline as pipeline_module
    monkeypatch.setattr(
        pipeline_module, "import_from_gmail",
        lambda gmail_settings, start_dt, end_dt: FakeImportResult(),
    )

    def fake_call_with_tool(task, system_prompt, user_content, tool_name, tool_schema, **kw):
        time.sleep(0.02)  # 模擬真實 API 呼叫的延遲，讓背景執行緒有時間跟主執行緒重疊
        if task == "retention_prefilter":
            return FakeResult({"judgements": [
                {"row_id": it.row_id, "is_relevant": True} for it in fake_items]})
        if task == "retention_judgement":
            return FakeResult({"judgements": [
                {"row_id": it.row_id, "business_relevance": 30, "response_requirement": 10,
                 "political_sensitivity": 5, "media_attention": 5, "public_impact": 5,
                 "executive_bonus": 0, "final_score": 55, "priority_stars": 4,
                 "should_respond": True, "is_moi_core_business": False} for it in fake_items]})
        if task == "topic_clustering":
            return FakeResult({"topics": [
                {"topic_id": "cand_1", "topic_name": "議題X",
                 "member_row_ids": [it.row_id for it in fake_items], "reason": "", "confidence": 0.9},
            ]})
        if task == "topic_merge":
            return FakeResult({"merged_groups": [
                {"source_topic_ids": ["cand_1"], "final_topic_name": "議題X", "reason": ""},
            ]})
        raise AssertionError(f"unexpected task {task}")

    ctx.gateway.call_with_tool = fake_call_with_tool

    resp = logged_in_client.post(
        "/pipeline/run", data={"start_dt": "2026-01-01T00:00", "end_dt": "2026-01-02T00:00"},
        follow_redirects=False,
    )
    job_id = resp.headers["Location"].split("job_id=")[1]

    # 流程還在跑的時候，反覆打會用到 ctx.news_repo/ctx.topic_repo/ctx.prompt_repo
    # 的頁面，確認不會跟背景執行緒的 repo 存取互相干擾出錯。
    deadline = time.time() + 10
    request_errors = []
    while time.time() < deadline:
        status = logged_in_client.get(f"/jobs/{job_id}/status").get_json()
        for path in ("/clustering", "/retention", "/"):
            r = logged_in_client.get(path)
            if r.status_code != 200:
                request_errors.append((path, r.status_code))
        if status["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.05)

    assert request_errors == []
    assert status["status"] == "completed"
    assert len(ctx.news_repo.list_all()) == 5
    assert any(t.topic_name == "議題X" for t in ctx.topic_repo.list_active())
