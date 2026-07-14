"""
Gmail API 薄封裝：搜尋符合條件的最新一封信、取出其 HTML 內文。

刻意不在頂層 import googleapiclient，理由同 gmail_auth.py。
"""
from __future__ import annotations

import base64
import datetime
from typing import List

from app.utils.logging_setup import get_logger

logger = get_logger("gmail_client")

_MAX_PAGES = 5  # 防呆上限：單次擷取最多翻 5 頁（500 封候選信）


def build_service(credentials):
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _build_query(sender: str, subject_keyword: str,
                  start_dt: datetime.datetime, end_dt: datetime.datetime) -> str:
    """Gmail 的 after:/before: 只到日期粒度，這裡先用日期範圍粗篩，
    精確到分鐘的篩選在 search_messages_in_range() 用 internalDate 二次過濾。"""
    after_date = start_dt.date()
    before_date = end_dt.date() + datetime.timedelta(days=1)  # before: 不含當天，故 +1
    parts = []
    if sender:
        parts.append(f"from:{sender}")
    if subject_keyword:
        # 多組主旨關鍵字（V4.5.3）：以逗號（半形/全形）分隔，任一符合即匯入。
        # 使用者同時訂閱「網路新聞監測」與「報紙新聞監測」兩種報告時，
        # 不必每次匯入前切換關鍵字——兩封一起撈，版型由解析器自動判別。
        import re as _re
        keywords = [k.strip() for k in _re.split(r"[,，]", subject_keyword) if k.strip()]
        if len(keywords) == 1:
            parts.append(f'subject:"{keywords[0]}"')
        elif keywords:
            parts.append("(" + " OR ".join(f'subject:"{k}"' for k in keywords) + ")")
    parts.append(f"after:{after_date.strftime('%Y/%m/%d')}")
    parts.append(f"before:{before_date.strftime('%Y/%m/%d')}")
    return " ".join(parts)


def search_messages_in_range(service, sender: str, subject_keyword: str,
                              start_dt: datetime.datetime,
                              end_dt: datetime.datetime) -> List[dict]:
    """回傳指定起訖時間內（含端點）符合條件的所有信件 metadata，依時間由舊到新排序"""
    query = _build_query(sender, subject_keyword, start_dt, end_dt)
    start_ts_ms = int(start_dt.timestamp() * 1000)
    end_ts_ms = int(end_dt.timestamp() * 1000)

    candidates = []
    page_token = None
    for _ in range(_MAX_PAGES):
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page_token).execute()
        candidates.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    else:
        logger.warning(f"Gmail 搜尋候選信件超過 {_MAX_PAGES * 100} 封，已截斷，"
                        f"可能有信件未被納入（query={query!r}）")

    matched = []
    for m in candidates:
        meta = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["Subject", "Date"]).execute()
        ts = int(meta.get("internalDate", 0))
        if start_ts_ms <= ts <= end_ts_ms:
            matched.append(meta)

    matched.sort(key=lambda meta: int(meta.get("internalDate", 0)))
    logger.info(f"Gmail 搜尋區間 {start_dt} ~ {end_dt}，候選 {len(candidates)} 封，"
                f"精確落在區間內 {len(matched)} 封")
    return matched


def _find_html_part(payload: dict) -> Optional[str]:
    """遞迴走訪 payload.parts，找出 mimeType=text/html 的內容並 base64url decode"""
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime_type == "text/html" and body.get("data"):
        return _decode_body_data(body["data"])

    for part in payload.get("parts", []) or []:
        html = _find_html_part(part)
        if html:
            return html

    # 找不到 text/html 時，退而求其次接受 text/plain（極少數信件無 HTML 版本）
    if mime_type == "text/plain" and body.get("data"):
        return _decode_body_data(body["data"])
    return None


def _decode_body_data(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
    return raw.decode("utf-8", errors="replace")


def _get_header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def fetch_message_html(service, msg_id: str) -> Tuple[str, str, str]:
    """回傳 (subject, date_header, html_body)"""
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = msg.get("payload", {}).get("headers", [])
    subject = _get_header(headers, "Subject")
    date_header = _get_header(headers, "Date")
    html = _find_html_part(msg.get("payload", {})) or ""
    return subject, date_header, html
