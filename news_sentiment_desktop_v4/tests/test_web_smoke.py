"""網頁版（app/web）煙霧測試——沿用 conftest.py 的隔離資料庫慣例（每個測試用
獨立的 NEWS_SENTIMENT_DATA_DIR，不共用真實 %APPDATA%/~/.news_sentiment_desktop_v4），
不呼叫真實 Anthropic API 或 Gmail 網路（gateway.call_with_tool 一律 mock）。
"""
from __future__ import annotations

import json
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
        if status["status"] in ("completed", "cancelled"):
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


def test_retention_page_shows_body_preview_and_checkbox_first_column(logged_in_client, web_app):
    ctx = web_app.config["APP_CONTEXT"]
    ctx.news_repo.upsert_one(NewsItem(
        row_id="r1", title="新聞一", source="來源A", body_text="這是完整正文內容。" * 20,
    ))
    ctx.news_repo.upsert_one(NewsItem(row_id="r2", title="新聞二", source="來源B", body_text=""))

    resp = logged_in_client.get("/retention")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "body-preview" in html
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
    assert b"/retention/run" not in resp.data


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
