"""測試：extract_keywords_from_taxonomy() / highlight_keywords()

留用初判／議題分群頁的新聞正文預覽維持完整原文、不截斷，只把設定頁「議題／
關鍵字彙整表」裡出現過的詞加粗提示，純視覺輔助，不影響 AI 判斷邏輯。
"""
from __future__ import annotations

from app.utils.text_utils import (
    extract_keywords_from_taxonomy, highlight_keywords, clean_body_for_preview,
)


def test_extract_keywords_splits_topic_column_and_boolean_syntax():
    taxonomy = "內政部相關\t內政部|內政部長|劉世芳\n戶政新制　戶政|戶籍謄本"
    keywords = extract_keywords_from_taxonomy(taxonomy)
    assert "內政部" in keywords
    assert "內政部長" in keywords
    assert "劉世芳" in keywords
    assert "戶政" in keywords
    assert "戶籍謄本" in keywords


def test_extract_keywords_handles_line_with_no_topic_column():
    """格式不工整、沒有議題欄分隔時，整行當關鍵字欄處理"""
    keywords = extract_keywords_from_taxonomy("內政部|警政署")
    assert "內政部" in keywords
    assert "警政署" in keywords


def test_extract_keywords_drops_single_character_tokens():
    keywords = extract_keywords_from_taxonomy("議題\tA|部|內政部")
    assert "部" not in keywords
    assert "內政部" in keywords


def test_extract_keywords_empty_taxonomy_returns_empty_list():
    assert extract_keywords_from_taxonomy("") == []
    assert extract_keywords_from_taxonomy(None) == []


def test_extract_keywords_sorted_longest_first():
    keywords = extract_keywords_from_taxonomy("議題\t內政部|內政部長")
    assert keywords.index("內政部長") < keywords.index("內政部")


def test_highlight_keywords_wraps_match_in_strong():
    result = highlight_keywords("內政部長今日召開記者會", ["內政部長"])
    assert result == "<strong>內政部長</strong>今日召開記者會"


def test_highlight_keywords_preserves_full_untruncated_text():
    body = "第一段內容。" * 50 + "內政部長宣布新政策。" + "第二段內容。" * 50
    result = highlight_keywords(body, ["內政部長"])
    assert "第一段內容。" * 50 in result
    assert "第二段內容。" * 50 in result
    assert "<strong>內政部長</strong>" in result


def test_highlight_keywords_prefers_longer_overlapping_keyword():
    result = highlight_keywords("內政部長出席", ["內政部長", "內政部"])
    assert result == "<strong>內政部長</strong>出席"


def test_highlight_keywords_escapes_html_special_characters():
    result = highlight_keywords("A<script>alert(1)</script>內政部長", ["內政部長"])
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "<strong>內政部長</strong>" in result


def test_highlight_keywords_no_keywords_still_escapes_and_returns_full_text():
    result = highlight_keywords("純文字內容，沒有關鍵字", [])
    assert result == "純文字內容，沒有關鍵字"


def test_highlight_keywords_empty_text_returns_empty_string():
    assert highlight_keywords("", ["內政部"]) == ""


# ---------- clean_body_for_preview() ----------
# 來源網頁常見同一段文字被拆成好幾行（CMS 編輯器換行、或每個 <p> 對應一句話
# 而非一個完整段落），normalize_whitespace() 只收斂 3 個以上的換行、刻意保留
# 單一換行；但預覽區塊 CSS 是 white-space: pre-wrap，會把每個換行都畫成一次
# 真正換行，讓文字看起來被切成一截一截。clean_body_for_preview() 只在「畫面
# 顯示」這層把段落內部零星的單一換行攤平成空白，保留真正的段落分隔。

def test_clean_body_for_preview_flattens_single_embedded_newlines():
    text = "第一句話。\n第二句話，本來跟第一句同一段。\n第三句話。"
    result = clean_body_for_preview(text)
    assert result == "第一句話。 第二句話，本來跟第一句同一段。 第三句話。"


def test_clean_body_for_preview_preserves_real_paragraph_breaks():
    text = "第一段第一句。\n第一段第二句。\n\n第二段第一句。\n第二段第二句。"
    result = clean_body_for_preview(text)
    assert result == "第一段第一句。 第一段第二句。\n\n第二段第一句。 第二段第二句。"


def test_clean_body_for_preview_collapses_excessive_blank_lines_between_paragraphs():
    text = "第一段。\n\n\n\n\n第二段。"
    result = clean_body_for_preview(text)
    assert result == "第一段。\n\n第二段。"


def test_clean_body_for_preview_does_not_drop_any_words():
    """只重排空白/換行，不能遺漏任何實際內容"""
    text = "內政部長\n劉世芳\n出席記者會，\n說明治安政策。"
    result = clean_body_for_preview(text)
    for word in ["內政部長", "劉世芳", "出席記者會", "說明治安政策"]:
        assert word in result


def test_clean_body_for_preview_empty_text():
    assert clean_body_for_preview("") == ""
    assert clean_body_for_preview(None) is None


def test_highlight_keywords_after_cleaning_shows_continuous_prose_with_bold_match():
    raw = "第一句與案情無關。\n內政部長劉世芳出席記者會。\n第三句補充說明。"
    cleaned = clean_body_for_preview(raw)
    result = highlight_keywords(cleaned, ["內政部長"])
    assert "\n" not in result
    assert "<strong>內政部長</strong>" in result
