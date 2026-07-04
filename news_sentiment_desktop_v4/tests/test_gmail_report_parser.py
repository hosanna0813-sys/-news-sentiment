"""
gmail_report_parser.parse_report_html() 的單元測試。

HTML fixture 已對照過真機取得的真實 Gmail HTML 結構調整過，格式假設成立。
"""
from __future__ import annotations

from app.services.gmail.gmail_report_parser import parse_report_html

SAMPLE_HTML = """
<html><body>
<div>
<h3>林口萬家福男子酒後持刀砍人 外籍男手臂受傷送醫</h3>
<p>【2026-07-03 CNA - 社會 中央社】</p>
<p>（中央社記者曹亞沿新北3日電）新北林口萬家福今天下午發生傷人案，張姓男子疑酒後精神不穩，
持刀刺傷1名印度籍男子手臂。傷者經送醫包紮後，幸無生命危險。</p>
<p><a href="https://www.cna.com.tw/news/asoc/202607030123.aspx">Source</a></p>
<p><a href="#top">【Back】</a></p>

<h3>在家疑似太吵 基隆19歲姊姊持刀砍傷弟弟</h3>
<p>【2026-07-03 CNA - 社會 中央社】</p>
<p>（中央社記者沈如峰基隆3日電）基隆市1名19歲姊姊今天疑因不滿15歲的弟弟在家太吵，
雙方發生口角衝突，姊姊一時氣憤持刀砍傷弟弟，所幸無生命危險。</p>
<p>依托咪酯 喪屍煙彈 國安局 新興毒品 跨境緝毒</p>
<p><a href="https://www.cna.com.tw/news/asoc/202607030124.aspx">Source</a></p>
<p><a href="#top">【Back】</a></p>

<h3>新興毒品走私情資曝光 國安局：依托咪酯占新興毒品12%</h3>
<p>【2026-07-03 CTWant - 政治 陳昱丞】</p>
<p>國安局指出，依托咪酯等新興毒品走私情形受關注，未來將強化跨機關情報整合、國際合作及源頭查緝，
打擊跨境毒品犯罪。</p>
<p>Prismintelligence 哪些國家是台灣毒品原料的主要來源？</p>
<p>您的專屬推薦：深度解析 國安局如何運用情報手段打擊毒品？</p>
<p>NewsScope AIBETA Start</p>
<p><a href="https://www.ctwant.com/article/xxx">Source</a></p>
<p><a href="#top">【Back】</a></p>

<p>【2026-07-03 CNA - 社會 中央社】</p>
<p>這則格式異常，緊鄰中繼資料列前一行是【Back】（雜訊），找不到標題，應被跳過。</p>
<p><a href="https://example.com/no-title">Source</a></p>
<p><a href="#top">【Back】</a></p>
</div>
</body></html>
"""


def test_parses_expected_number_of_items_and_skips_malformed_one():
    items = parse_report_html(SAMPLE_HTML, import_batch_id="batch_test")
    assert len(items) == 3


def test_fields_mapped_correctly_for_first_item():
    items = parse_report_html(SAMPLE_HTML, import_batch_id="batch_test")
    first = items[0]
    assert first.title == "林口萬家福男子酒後持刀砍人 外籍男手臂受傷送醫"
    assert first.published_at == "2026-07-03"
    assert first.source == "CNA"
    assert first.channel == "社會"
    assert first.author == "中央社"
    assert first.url == "https://www.cna.com.tw/news/asoc/202607030123.aspx"
    assert "張姓男子疑酒後精神不穩" in first.body_text
    assert first.body_source == "Gmail正文"
    assert first.import_batch_id == "batch_test"


def test_keyword_tag_line_is_kept_in_body_not_misparsed_as_next_article():
    """廠商附加的關鍵字標籤列（無標點的空白分隔詞組）目前沒有可靠的規則能與
    「正文最後一句剛好也沒有標點」區分，因此刻意不嘗試濾除，只保留原樣併入正文——
    這裡只驗證它不會被誤判成下一則的中繼資料列或把正文切斷在錯誤位置。"""
    items = parse_report_html(SAMPLE_HTML, import_batch_id="batch_test")
    second = items[1]
    assert "依托咪酯 喪屍煙彈" in second.body_text
    assert "雙方發生口角衝突" in second.body_text


def test_prismintelligence_and_newsscope_noise_filtered_out():
    items = parse_report_html(SAMPLE_HTML, import_batch_id="batch_test")
    third = items[2]
    assert "Prismintelligence" not in third.body_text
    assert "NewsScope" not in third.body_text
    assert "您的專屬推薦" not in third.body_text
    assert third.url == "https://www.ctwant.com/article/xxx"


def test_empty_html_returns_no_items():
    assert parse_report_html("<html><body>沒有任何新聞格式</body></html>") == []


MISSING_SOURCE_HTML = """
<html><body>
<div>
<h3>第一則新聞：有 Source 連結</h3>
<p>【2026-07-03 CNA - 社會 中央社】</p>
<p>這是第一則新聞的正文內容，長度足夠不會被判定為可疑內容不會被判定為可疑內容。</p>
<p><a href="https://example.com/article-1">Source</a></p>
<p><a href="#top">【Back】</a></p>

<h3>第二則新聞：真實信件裡剛好沒有附 Source 連結</h3>
<p>【2026-07-03 CNA - 社會 中央社】</p>
<p>這是第二則新聞的正文內容，這則在真實信件格式裡碰巧沒有提供原始連結不會被判定為可疑。</p>
<p><a href="#top">【Back】</a></p>

<h3>第三則新聞：也有自己的 Source 連結</h3>
<p>【2026-07-03 CNA - 社會 中央社】</p>
<p>這是第三則新聞的正文內容，長度足夠不會被判定為可疑內容不會被判定為可疑內容。</p>
<p><a href="https://example.com/article-3">Source</a></p>
<p><a href="#top">【Back】</a></p>
</div>
</body></html>
"""


def test_missing_source_link_does_not_shift_subsequent_urls():
    """對應真機驗證抓到的實際 bug：文件中間某一則新聞剛好沒有 Source 連結時，
    舊版「全域位置對應」寫法會讓後面所有新聞的 url 系統性錯位一格。
    新版應該只讓「這一則」的 url 是空字串，不影響其他則。"""
    items = parse_report_html(MISSING_SOURCE_HTML, import_batch_id="batch_test")
    assert len(items) == 3
    assert items[0].title == "第一則新聞：有 Source 連結"
    assert items[0].url == "https://example.com/article-1"
    assert items[1].title == "第二則新聞：真實信件裡剛好沒有附 Source 連結"
    assert items[1].url == ""
    assert items[2].title == "第三則新聞：也有自己的 Source 連結"
    assert items[2].url == "https://example.com/article-3"
