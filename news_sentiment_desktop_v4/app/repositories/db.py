"""
SQLite 資料庫連線管理 + Schema Migration

規格要求（十七）：
    - 不可覆蓋使用者既有 data、案例庫、規則庫與 log
    - 若需資料格式升級，提供 migration

設計：
    - 單一 db.py 提供 get_connection()，所有 repository 共用連線設定
      （WAL mode 以支援背景 worker 執行緒同時讀寫、外鍵開啟）。
    - schema_version 表記錄目前版本，啟動時依序套用尚未執行的 migration，
      不會 DROP 既有 table，只會用 CREATE TABLE IF NOT EXISTS / ALTER TABLE。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from app.utils.paths import get_db_path
from app.utils.logging_setup import get_logger

logger = get_logger("db")

_local = threading.local()

CURRENT_SCHEMA_VERSION = 4


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """取得 thread-local 的 SQLite 連線（每個 QThread worker 需各自呼叫一次）"""
    path = str(db_path or get_db_path())
    key = f"conn_{path}"
    conn = getattr(_local, key, None)
    if conn is None:
        conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=30000;")
        setattr(_local, key, conn)
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
    import_batch_id TEXT PRIMARY KEY,
    file_name TEXT,
    imported_at REAL,
    sheet_count INTEGER,
    total_rows INTEGER,
    duplicate_rows INTEGER,
    missing_url_rows INTEGER,
    has_body_rows INTEGER,
    summary_only_rows INTEGER
);

CREATE TABLE IF NOT EXISTS news (
    row_id TEXT PRIMARY KEY,
    news_id TEXT,
    import_batch_id TEXT,
    source_sheet TEXT,
    title TEXT,
    summary TEXT,
    source TEXT,
    published_at TEXT,
    author TEXT,
    url TEXT,
    channel TEXT,
    tags TEXT,
    excel_body TEXT,
    retained INTEGER DEFAULT 1,
    retention_status TEXT DEFAULT '待確認',
    retention_reason TEXT,
    ai_retention_confidence REAL DEFAULT 0,
    duplicate_group_id TEXT,
    manual_note TEXT,
    retention_judged_at REAL,
    retention_judged_by TEXT,
    score_business_relevance REAL DEFAULT 0,
    score_response_requirement REAL DEFAULT 0,
    score_political_sensitivity REAL DEFAULT 0,
    score_media_attention REAL DEFAULT 0,
    score_public_impact REAL DEFAULT 0,
    score_executive_bonus REAL DEFAULT 0,
    score_final REAL DEFAULT 0,
    priority_stars INTEGER DEFAULT 0,
    should_respond INTEGER DEFAULT 0,
    is_moi_core_business INTEGER DEFAULT 0,
    recommended_action TEXT DEFAULT '',
    action_reasoning TEXT DEFAULT '',
    body_text TEXT,
    body_source TEXT DEFAULT '無正文',
    body_fetch_status TEXT DEFAULT '未抓取',
    body_fetch_detail TEXT,
    body_fetched_at REAL,
    body_quality_score REAL DEFAULT 0,
    body_word_count INTEGER DEFAULT 0,
    initial_topic_id TEXT,
    initial_topic_name TEXT,
    final_topic_id TEXT,
    final_topic_name TEXT,
    clustering_reason TEXT,
    clustering_confidence REAL DEFAULT 0,
    evidence_news_ids TEXT,
    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_news_batch ON news(import_batch_id);
CREATE INDEX IF NOT EXISTS idx_news_retention ON news(retention_status);
CREATE INDEX IF NOT EXISTS idx_news_topic ON news(final_topic_id);
CREATE INDEX IF NOT EXISTS idx_news_url ON news(url);
CREATE INDEX IF NOT EXISTS idx_news_title ON news(title);

CREATE TABLE IF NOT EXISTS topics (
    topic_id TEXT PRIMARY KEY,
    topic_name TEXT,
    status TEXT DEFAULT 'active',
    merged_into TEXT,
    summary_150 TEXT,
    summary_300 TEXT,
    summary_full TEXT,
    development_progress TEXT,
    core_disputes TEXT,
    key_actors TEXT,
    possible_impact TEXT,
    cited_news_count INTEGER DEFAULT 0,
    has_identifiable_stance INTEGER DEFAULT 0,
    summarized_at REAL,
    summarized_by_model TEXT,
    display_order INTEGER DEFAULT 0,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS stances (
    stance_id TEXT PRIMARY KEY,
    topic_id TEXT,
    stance_type TEXT,
    speaker TEXT,
    organization TEXT,
    claim TEXT,
    evidence_news_id TEXT,
    evidence_excerpt TEXT,
    confidence REAL DEFAULT 0,
    created_at REAL,
    human_modified INTEGER DEFAULT 0,
    human_modified_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_stances_topic ON stances(topic_id);

CREATE TABLE IF NOT EXISTS feedback_log (
    feedback_id TEXT PRIMARY KEY,
    batch_id TEXT,
    entity_type TEXT,
    entity_id TEXT,
    ai_original_value TEXT,
    human_final_value TEXT,
    action TEXT,
    reason TEXT,
    operator TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS prompt_tuning_drafts (
    draft_id TEXT PRIMARY KEY,
    task TEXT DEFAULT 'retention_judgement',
    based_on_version INTEGER DEFAULT 0,
    proposed_system_prompt TEXT,
    proposed_user_template TEXT,
    rationale TEXT,
    status TEXT DEFAULT '待驗證',
    validation_metrics_json TEXT DEFAULT '{}',
    generated_by_model TEXT,
    correction_count_used INTEGER DEFAULT 0,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS case_records (
    case_id TEXT PRIMARY KEY,
    case_type TEXT,
    description TEXT,
    source_feedback_ids TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS rule_drafts (
    rule_id TEXT PRIMARY KEY,
    name TEXT,
    scope TEXT,
    rule_text TEXT,
    supporting_case_count INTEGER DEFAULT 0,
    representative_cases TEXT,
    risk_notes TEXT,
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT 'draft',
    version INTEGER DEFAULT 1,
    generated_by_model TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT,
    status TEXT DEFAULT 'pending',
    total_items INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    progress_current INTEGER DEFAULT 0,
    started_at REAL,
    finished_at REAL,
    cancel_requested INTEGER DEFAULT 0,
    created_at REAL,
    params_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    job_id TEXT,
    batch_index INTEGER,
    status TEXT DEFAULT 'pending',
    item_ids_json TEXT DEFAULT '[]',
    error_type TEXT,
    error_detail TEXT,
    retry_count INTEGER DEFAULT 0,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_batches_job ON batches(job_id);

CREATE TABLE IF NOT EXISTS prompts (
    task TEXT,
    version INTEGER,
    system_prompt TEXT,
    user_template TEXT,
    tool_schema_json TEXT,
    enabled INTEGER DEFAULT 1,
    is_default INTEGER DEFAULT 0,
    last_modified_at REAL,
    PRIMARY KEY (task, version)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT
);

CREATE TABLE IF NOT EXISTS scrape_stats (
    domain TEXT PRIMARY KEY,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    skip_count INTEGER DEFAULT 0,
    total_elapsed_sec REAL DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    last_status TEXT,
    last_detail TEXT,
    last_success_at REAL,
    last_attempt_at REAL
);
"""


def init_db(db_path: Optional[Path] = None) -> None:
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    cur = conn.execute("SELECT version FROM schema_version")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (CURRENT_SCHEMA_VERSION,))
        conn.commit()
        logger.info(f"初始化資料庫，schema version={CURRENT_SCHEMA_VERSION}")
    else:
        current = row["version"]
        if current < CURRENT_SCHEMA_VERSION:
            _run_migrations(conn, current, CURRENT_SCHEMA_VERSION)


def _run_migrations(conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
    """未來版本升級時，在此新增 migration 步驟（僅新增欄位/表，絕不 DROP 既有資料）"""
    logger.info(f"執行資料庫 migration: {from_version} -> {to_version}")

    if from_version < 2:
        _add_columns_if_missing(conn, "news", [
            ("score_business_relevance", "REAL DEFAULT 0"),
            ("score_response_requirement", "REAL DEFAULT 0"),
            ("score_political_sensitivity", "REAL DEFAULT 0"),
            ("score_media_attention", "REAL DEFAULT 0"),
            ("score_public_impact", "REAL DEFAULT 0"),
            ("score_executive_bonus", "REAL DEFAULT 0"),
            ("score_final", "REAL DEFAULT 0"),
            ("priority_stars", "INTEGER DEFAULT 0"),
            ("should_respond", "INTEGER DEFAULT 0"),
            ("recommended_action", "TEXT DEFAULT ''"),
            ("action_reasoning", "TEXT DEFAULT ''"),
        ])

    if from_version < 3:
        _add_columns_if_missing(conn, "news", [
            ("is_moi_core_business", "INTEGER DEFAULT 0"),
        ])

    if from_version < 4:
        # V4.6.0：議題可人工拖曳排序（0=尚未手動排序，依 created_at 排在最後）
        _add_columns_if_missing(conn, "topics", [
            ("display_order", "INTEGER DEFAULT 0"),
        ])

    conn.execute("UPDATE schema_version SET version=?", (to_version,))
    conn.commit()


def _add_columns_if_missing(conn: sqlite3.Connection, table: str, columns) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns:
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    conn.commit()
