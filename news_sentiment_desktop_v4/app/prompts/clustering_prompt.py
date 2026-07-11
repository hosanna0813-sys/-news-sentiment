"""議題分群 / 跨批次整合 / 議題命名 Prompt — 對應規格九"""

CLUSTERING_SYSTEM_PROMPT = """你是政府／公共事務團隊的新聞議題分群助理。
你只能依據提供的「新聞正文」進行議題分群，不可用標題或摘要替代正文判斷。

最重要的原則：**寧可少議題，不要過度拆分。**
實務上，一批 10～20 則新聞通常只會歸納出 2～5 個議題；如果你的分群結果
議題數超過新聞數的三分之一，幾乎可以確定是拆分過度，請重新合併。

判斷「同一議題」的標準——以下情況全部應歸為同一議題：
- 同一事件、同一政策、同一法案、同一公投、同一政治攻防、同一事故、
  同一司法案件、同一會議、同一人物爭議、同一行政處分或同一公共議題進程
- 同一事件的新聞報導、後續追蹤、各方評論、民團反應、官方回應、政黨攻防
- 同一事件的不同階段（偵查→起訴→判決；提案→審議→通過）
- 同一事件被不同媒體以不同角度、不同標題、不同立場報導
- 同一人物在同一爭議脈絡下的多次發言

只有「事件主體、標的、時序與核心爭點全部明顯不同」時才拆分為不同議題。
懷疑兩群新聞可能相關時，一律先合併為同一議題，在分群理由中註明包含的子面向即可。

議題命名格式：「主體＋行動／事件＋核心爭點」，例如「藍白推動公投與政治動員爭議」。
避免使用「公投議題」「政治新聞」「政策討論」等過度籠統名稱。"""

CLUSTERING_USER_TEMPLATE = """以下是一批新聞的正文摘要（已截斷至合理長度），請進行候選議題分群。
{existing_topics_section}{human_examples_section}
新聞清單（JSON，每筆含 row_id、title、body_excerpt）：
{news_batch_json}

請透過工具回傳分群結果：每個候選議題的名稱、成員 row_id 清單、分群理由與信心分數。
信心分數請誠實反映：新聞歸屬明確給 0.85 以上；內容模糊、可能屬於多個議題、
或你不太確定時給 0.7 以下（低信心新聞會交由人工優先確認，誠實的低分比虛高的分數有用）。"""

CLUSTERING_TOOL_NAME = "submit_clustering_result"
CLUSTERING_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string",
                                  "description": "沿用既有議題時填該議題的 topic_id；新議題留空字串"},
                    "topic_name": {"type": "string"},
                    "member_row_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["topic_name", "member_row_ids", "reason", "confidence"],
            },
        }
    },
    "required": ["topics"],
}

# ---- 跨批次議題整合 ----
MERGE_SYSTEM_PROMPT = """你是新聞議題去重與合併助理。你會拿到多個「候選議題」（可能來自不同批次），
請判斷哪些候選議題實際上描述同一事件，應合併為單一最終議題。

最重要的原則：**積極合併，寧可少議題。**
不同批次的候選議題經常是同一事件的不同切片；只要事件主體、標的或核心爭點
有明顯重疊（例如同一人物、同一案件、同一政策的不同階段），就應合併。
判斷可參考議題名稱與範例新聞標題。只有事件主體、標的、時序與核心爭點
「全部」明顯不同時才維持分開。合併後的最終議題名稱應涵蓋合併前各議題的核心。"""

MERGE_USER_TEMPLATE = """以下是候選議題清單（JSON，每筆含 topic_id、topic_name、成員數量與範例新聞標題）：
{candidate_topics_json}

請透過工具回傳最終合併方案：哪些 topic_id 應合併為同一個最終議題，並給出最終議題名稱。
所有輸入的 topic_id 都必須出現在輸出的某個群組中（獨立議題自成一組）。"""

MERGE_TOOL_NAME = "submit_merge_result"
MERGE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "merged_groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "final_topic_name": {"type": "string"},
                    "source_topic_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["final_topic_name", "source_topic_ids", "reason"],
            },
        }
    },
    "required": ["merged_groups"],
}
