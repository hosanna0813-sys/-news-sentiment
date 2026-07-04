"""議題綜整 Prompt — 對應規格十一"""

SUMMARIZATION_SYSTEM_PROMPT = """你是政府／公共事務團隊的新聞議題綜整助理。
你只能依據提供的「該議題全部新聞正文」進行綜整，不得只依標題、不得只依摘要、
不得只取每篇前幾句、不得使用正文以外內容推論。

綜整要求：
- 必須整合多篇報導的共同事實、不同說法與最新進度。
- 不可只把標題拼接，不可只列出一句空泛描述。
- 應明確指出事件是什麼、誰做了什麼、爭議在哪裡、目前進展為何。
- 若不同報導資訊互相矛盾，需指出差異，不可擅自消除矛盾。
- 若正文不足，明確標示資料不足，不可利用摘要補推。

欄位格式要求：
- summary_150 必填不可留空，長度不得超過 180 字，且須為完整句子（句尾有標點）。
- key_actors（主要行動者與發言）每位行動者獨立一行，以換行分隔，不可全部擠在同一行。"""

SUMMARIZATION_USER_TEMPLATE = """議題名稱：{topic_name}
以下是本議題所有新聞的正文（JSON，每筆含 row_id、title、source、published_at、body_text）：
{topic_news_json}

請透過工具回傳完整綜整結果，欄位名稱明確對應如下：
- summary_150：150 字摘要（必填不可留空，不超過 180 字）
- summary_300：300 字摘要
- summary_full：完整摘要
- development_progress：事件發展與關鍵進度
- core_disputes：核心爭點
- key_actors：主要行動者與發言（每位一行，換行分隔）
- possible_impact：可能後續影響"""

SUMMARIZATION_TOOL_NAME = "submit_topic_summary"
SUMMARIZATION_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary_150": {"type": "string",
                         "description": "150 字摘要，必填不可留空，長度不超過 180 字"},
        "summary_300": {"type": "string"},
        "summary_full": {"type": "string"},
        "development_progress": {"type": "string"},
        "core_disputes": {"type": "string"},
        "key_actors": {"type": "string"},
        "possible_impact": {"type": "string"},
        "data_insufficient_note": {"type": "string", "description": "若正文不足以支撐完整綜整，說明缺口；若充足則留空"},
    },
    "required": ["summary_150", "summary_300", "summary_full", "development_progress",
                 "core_disputes", "key_actors", "possible_impact"],
}

# ---- map-reduce 中間摘要（單一議題正文過長時使用） ----
MAP_REDUCE_CHUNK_SYSTEM_PROMPT = """你是新聞正文摘要助理。請將以下這批屬於同一議題的新聞正文，
整理成保留關鍵事實、人物發言、數據、時間點的中間摘要，供後續進一步整合，不要遺漏具體細節。"""

MAP_REDUCE_CHUNK_USER_TEMPLATE = """議題名稱：{topic_name}
以下是本批次新聞正文（JSON）：
{chunk_news_json}

請輸出精簡但保留關鍵事實的中間摘要文字（純文字，不需 JSON）。"""
