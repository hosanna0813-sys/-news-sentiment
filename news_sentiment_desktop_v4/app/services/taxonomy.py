"""議題／關鍵字彙整表 → prompt 參考文字（桌面版與網頁版共用）

使用者在設定頁貼的業務關注議題與關鍵字清單（可含 KEYPO 慣用的 | & ~N 布林
語法），轉成一段可直接注入留用初判／議題分群 prompt 的參考文字。
刻意不在程式端解析布林語法——來源常有不平衡括號、不一致分隔符號等人工謄寫
雜訊，硬解析容易悄悄出錯；原文交給 AI 理解語意即可。
"""
from __future__ import annotations


def build_keyword_context(taxonomy: str) -> str:
    taxonomy = (taxonomy or "").strip()
    if not taxonomy:
        return ""
    return (
        "【業務關注議題與關鍵字對照表】以下是本單位各業務關注的議題分類與相關關鍵字"
        "（可能包含 | 代表或、& 代表且的檢索語法），請作為判斷新聞是否相關、"
        "以及應歸入哪個議題類別的重要參考：\n" + taxonomy
    )


def prepend_keyword_context(taxonomy: str, human_examples: str) -> str:
    """把對照表接在 few-shot 範例前面（兩者都可能為空）"""
    context = build_keyword_context(taxonomy)
    if not context:
        return human_examples
    return f"{context}\n\n{human_examples}" if human_examples else context
