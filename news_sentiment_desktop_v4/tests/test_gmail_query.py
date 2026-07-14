"""測試：Gmail 搜尋查詢組裝——多組主旨關鍵字（V4.5.3）

使用者同時訂閱「網路新聞監測」與「報紙新聞監測」兩種報告，原本每次匯入前
要切換主旨關鍵字。現在支援逗號分隔多組關鍵字（任一符合即匯入）。
"""
from __future__ import annotations

import datetime

from app.services.gmail.gmail_client import _build_query

_START = datetime.datetime(2026, 7, 13, 6, 0)
_END = datetime.datetime(2026, 7, 13, 12, 0)


def test_single_keyword_unchanged():
    q = _build_query("a@b.tw", "新聞監測報告", _START, _END)
    assert 'subject:"新聞監測報告"' in q
    assert "OR" not in q
    assert "from:a@b.tw" in q
    assert "after:2026/07/13" in q and "before:2026/07/14" in q


def test_multiple_keywords_joined_with_or():
    q = _build_query("a@b.tw", "網路新聞監測, 報紙新聞監測", _START, _END)
    assert '(subject:"網路新聞監測" OR subject:"報紙新聞監測")' in q


def test_fullwidth_comma_and_blank_segments():
    q = _build_query("a@b.tw", "網路新聞監測，， 報紙新聞監測 ,", _START, _END)
    assert '(subject:"網路新聞監測" OR subject:"報紙新聞監測")' in q


def test_empty_keyword_omits_subject_filter():
    q = _build_query("a@b.tw", "", _START, _END)
    assert "subject" not in q
