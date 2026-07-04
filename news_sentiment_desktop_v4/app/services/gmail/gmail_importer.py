"""
Gmail 匯入服務 — 串接 OAuth 憑證、Gmail API 搜尋/擷取、報告內文解析，
產出與 excel_importer.import_file() 相同形狀的 ImportResult，
供 GmailImportWorker 沿用既有的匯入資料流程（NewsRepository.upsert_many 等）。
"""
from __future__ import annotations

import datetime
from typing import Callable, Optional

from app.models.settings import GmailSettings
from app.services.importer.excel_importer import ImportResult, _finalize_result
from app.services.gmail.gmail_auth import get_valid_credentials
from app.services.gmail.gmail_client import build_service, search_messages_in_range, fetch_message_html
from app.services.gmail.gmail_report_parser import parse_report_html
from app.utils.logging_setup import get_logger

logger = get_logger("gmail_importer")


class GmailImportError(Exception):
    pass


def import_from_gmail(settings: GmailSettings,
                       start_dt: datetime.datetime,
                       end_dt: datetime.datetime,
                       progress_cb: Optional[Callable[[str], None]] = None) -> ImportResult:
    """擷取 [start_dt, end_dt]（含端點）區間內，來自同一寄件者、符合主旨關鍵字的
    所有信件，逐封解析後合併成同一批匯入結果（同一 import_batch_id，
    跨信件的標題/網址重複由 _finalize_result 統一偵測）。"""
    def _progress(msg: str):
        if progress_cb:
            progress_cb(msg)

    if not settings.sender_email_filter:
        raise GmailImportError("尚未設定寄件者信箱篩選條件")
    if start_dt > end_dt:
        raise GmailImportError("起始時間不可晚於結束時間")

    creds = get_valid_credentials()
    if creds is None:
        raise GmailImportError("Gmail 尚未連接或授權已失效，請至設定頁重新連接")

    service = build_service(creds)

    _progress("搜尋符合條件的信件中...")
    messages = search_messages_in_range(
        service, settings.sender_email_filter, settings.subject_keyword, start_dt, end_dt)
    if not messages:
        raise GmailImportError(
            f"{start_dt.strftime('%Y-%m-%d %H:%M')} ~ {end_dt.strftime('%Y-%m-%d %H:%M')} "
            f"區間內找不到來自「{settings.sender_email_filter}」的信件")

    result = ImportResult()
    result.file_name = (f"Gmail 擷取 {len(messages)} 封信件"
                         f"（{start_dt.strftime('%Y-%m-%d %H:%M')} ~ {end_dt.strftime('%Y-%m-%d %H:%M')}）")
    result.sheet_count = len(messages)

    subjects = []
    for i, msg_stub in enumerate(messages, start=1):
        _progress(f"讀取第 {i}/{len(messages)} 封信件中...")
        subject, date_header, html = fetch_message_html(service, msg_stub["id"])
        subjects.append(subject)
        if not html:
            logger.warning(f"第 {i} 封信件（{subject}）沒有可解析的 HTML 內文，略過")
            continue
        items = parse_report_html(html, import_batch_id=result.import_batch_id)
        if not items:
            logger.warning(f"第 {i} 封信件（{subject}）解析不到任何新聞項目，略過")
        result.items.extend(items)

    if not result.items:
        raise GmailImportError(
            f"共讀到 {len(messages)} 封信件，但都解析不到新聞項目，可能是版型有變，需檢查解析規則")

    result.column_mapping_summary = {f"信件 {i+1}": {"主旨": s} for i, s in enumerate(subjects)}

    _finalize_result(result)
    logger.info(f"Gmail 匯入完成：{result.file_name}，共 {result.total_rows} 筆")
    return result
