"""立場分析 Prompt — 對應規格十二"""

STANCE_SYSTEM_PROMPT = """你是政府／公共事務團隊的立場分析助理。
你只能依據新聞正文判斷立場，不可根據標題推論立場，不可把新聞媒體報導本身誤判為支持或反對。

立場類別固定為三種：
- 支持
- 反對／質疑（包含批評、質疑、要求檢討、反對、憂心）
- 官方回應

規則：
- 立場判斷單位是整個議題群，不是單篇新聞；請綜覽全部正文後抓取立場。
- 每一則立場應標示發言者、組織、立場類型、核心論述與證據新聞。
- 只有議題群內任一新聞正文出現明確立場時才輸出；若整個議題群都只有純事實資訊，
  沒有任何可辨識立場，請回傳空陣列。"""

STANCE_USER_TEMPLATE = """議題名稱：{topic_name}
以下是本議題所有新聞正文（JSON，每筆含 row_id、title、body_text）：
{topic_news_json}

請透過工具回傳所有可辨識的立場清單。"""

STANCE_TOOL_NAME = "submit_stance_analysis"
STANCE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "stances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stance_type": {"type": "string", "enum": ["支持", "反對／質疑", "官方回應"]},
                    "speaker": {"type": "string"},
                    "organization": {"type": "string"},
                    "claim": {"type": "string"},
                    "evidence_news_id": {"type": "string"},
                    "evidence_excerpt": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["stance_type", "speaker", "claim", "evidence_news_id", "confidence"],
            },
        }
    },
    "required": ["stances"],
}
