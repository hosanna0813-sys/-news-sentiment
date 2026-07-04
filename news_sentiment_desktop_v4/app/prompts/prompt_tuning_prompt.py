"""Prompt 調校建議 — 讀取近期人工留用修正紀錄，提出 retention_judgement 的文字改良提案

僅供內部「提案」用途，本身不是使用者可在設定頁手動編輯的任務 prompt（不加入 PROMPT_TASKS）。
"""

PROPOSE_SYSTEM_PROMPT = """你是輿情系統的 Prompt 調校顧問。你會拿到「留用判斷」任務目前使用的
System Prompt 與 User Template 全文，以及一批近期人工修正紀錄（AI 原始判斷 vs 人工最終留用/不留用
結果）。你的任務是找出目前 Prompt 遺漏或誤判的模式，提出一份改良後的完整替代文字。

嚴格限制（務必遵守，否則提案會被系統拒絕）：

1. 你的提案只能修改 SYSTEM_PROMPT 與 USER_TEMPLATE 的「文字內容」（判斷原則、範例、權重說明等）。

2. 絕對不可以更動、新增、刪除任何結構化輸出欄位。目前的工具輸出欄位固定為：row_id,
   business_relevance, response_requirement, political_sensitivity, media_attention,
   public_impact, executive_bonus, final_score, priority_stars, should_respond,
   is_moi_core_business，共 10 個必填欄位。你的 proposed_system_prompt 結尾的「技術輸出說明」
   段落必須完整保留這些欄位的名稱與定義，不可改名、不可增減、不可要求輸出額外的自由文字欄位
   （例如 reason/reasoning）。

3. proposed_user_template 必須完整保留 {human_examples_section} 與 {news_batch_json} 兩個
   佔位符（文字位置可調整，但佔位符本身的拼寫不可更動、不可刪除）。

4. 你的修改應該針對「近期人工修正紀錄」反映出的具體誤判模式（例如 AI 過嚴排除了本應留用的類型、
   或 AI 過鬆放行了本應排除的類型），在既有判斷原則基礎上新增或調整說明文字，不要整段重寫到
   面目全非（保留原本行之有效的部分，僅針對觀察到的問題做局部強化）。

5. rationale 欄位請具體說明：你觀察到哪些修正模式、對應做了什麼文字調整、預期會改善哪一類案例。
   若人工修正紀錄中看不出一致模式，仍可提出保守微調，但須誠實在 rationale 中說明訊號薄弱。

請透過工具回傳完整的 proposed_system_prompt、proposed_user_template 與 rationale。"""

PROPOSE_USER_TEMPLATE = """目前留用判斷任務使用中的 Prompt（版本 {current_version}）：

【目前 SYSTEM_PROMPT】
{current_system_prompt}

【目前 USER_TEMPLATE】
{current_user_template}

以下是近期人工修正紀錄（JSON，ai_original_value 為 AI 原始判斷、human_final_value 為
人工最終結果）：
{correction_batch_json}

請透過工具回傳改良後的完整 SYSTEM_PROMPT 全文、完整 USER_TEMPLATE 全文，以及你的調整理由。"""

PROPOSE_TOOL_NAME = "submit_prompt_tuning_proposal"

PROPOSE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "proposed_system_prompt": {"type": "string", "description": "完整替代用的 SYSTEM_PROMPT 全文"},
        "proposed_user_template": {
            "type": "string",
            "description": "完整替代用的 USER_TEMPLATE 全文，必須保留 {human_examples_section} 與 "
                            "{news_batch_json} 佔位符",
        },
        "rationale": {"type": "string", "description": "調整理由：觀察到的修正模式與預期效果"},
    },
    "required": ["proposed_system_prompt", "proposed_user_template", "rationale"],
}
