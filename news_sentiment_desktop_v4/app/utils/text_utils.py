"""通用工具：ID 產生、文字清理、JSON 安全解析"""
from __future__ import annotations

import html
import json
import re
import uuid
from typing import Any, List, Optional


def new_id(prefix: str = "") -> str:
    u = uuid.uuid4().hex[:12]
    return f"{prefix}{u}" if prefix else u


def normalize_whitespace(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_body_for_preview(text: str) -> str:
    """新聞正文「畫面預覽」用的顯示層清理，不改動資料庫裡儲存的原始 body_text，
    也不影響送進 AI 判斷的內容或 Word 匯出。

    來源網頁常見同一段文字被拆成好幾行（例如 CMS 編輯器裡按 Enter 換行、或
    每個 <p> 對應一句話而非一個完整段落），這些單一換行被 normalize_whitespace()
    刻意保留（它只把 3 個以上的換行收斂成 2 個，視為段落間距），但預覽區塊的
    CSS 是 white-space: pre-wrap，會把「每一個」換行都畫成一次真正換行，讓文字
    看起來被切成一截一截。這裡把「原文中的段落分隔」（連續 2 個以上換行）保留
    下來，段落內部零星的單一換行則攤平成空白，讓同一段文字連續顯示。"""
    if not text:
        return text
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n{2,}", normalized)
    cleaned = []
    for para in paragraphs:
        flat = re.sub(r"\s*\n\s*", " ", para)
        flat = re.sub(r"[ \t　]+", " ", flat).strip()
        if flat:
            cleaned.append(flat)
    return "\n\n".join(cleaned)


def safe_json_loads(text: str) -> Optional[Any]:
    """嚴格 JSON 解析 + 容錯：去除 ```json fences、前後雜訊文字"""
    if not text:
        return None
    text = text.strip()
    # 去除 markdown code fence
    text = re.sub(r"^```(json)?", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 嘗試找出第一個 { 或 [ 到最後一個對應符號之間的內容
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = text.find(open_c)
        end = text.rfind(close_c)
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return None


def word_count_cjk_aware(text: str) -> int:
    """粗略估算字數：中日韓字元逐字計，其餘以空白斷詞計"""
    if not text:
        return 0
    cjk = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", text)
    non_cjk_text = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf]", " ", text)
    other_tokens = [t for t in non_cjk_text.split() if t]
    return len(cjk) + len(other_tokens)


def coerce_model_list(data, key: str) -> list:
    """
    從模型輸出中穩健取出 key 對應的清單（V4.1.5）。
    模型有時會把整包 JSON 或清單「包成字串」回傳，例如：
        data = {"topics": "{\"topics\": [...]}"}   （字串包整份 JSON）
        data = {"topics": "[{...}, {...}]"}          （字串包清單）
        data = "{\"topics\": [...]}"                （整個 data 是字串）
    一律嘗試解析字串為 JSON 再取值；清單內若仍有字串項目，也逐項嘗試解析為物件。
    """
    if isinstance(data, str):
        data = safe_json_loads(data)
    if not isinstance(data, (dict, list)):
        return []
    value = data.get(key, []) if isinstance(data, dict) else data
    if isinstance(value, str):
        parsed = safe_json_loads(value)
        if isinstance(parsed, dict):
            value = parsed.get(key, [])
        elif isinstance(parsed, list):
            value = parsed
        else:
            return []
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str):
            parsed = safe_json_loads(item)
            item = parsed if isinstance(parsed, dict) else item
        out.append(item)
    return out


_CJK_RE = None

def title_body_overlap(title: str, body: str) -> bool:
    """
    正文品質檢查（V4.2.0）：判斷正文與標題是否有關鍵字重疊。
    取標題中的 CJK 雙字詞（bigram）與長度>=3的英數詞，任一出現在正文即視為相關。
    標題過短（<4個有效字元）時不做判斷（回傳 True，避免誤殺）。
    """
    import re as _re
    if not title or not body:
        return False
    cjk_chars = _re.findall(r"[\u4e00-\u9fff]", title)
    tokens = set()
    for i in range(len(cjk_chars) - 1):
        tokens.add(cjk_chars[i] + cjk_chars[i + 1])
    for w in _re.findall(r"[A-Za-z0-9]{3,}", title):
        tokens.add(w.lower())
    if len(cjk_chars) + sum(len(w) for w in _re.findall(r"[A-Za-z0-9]{3,}", title)) < 4:
        return True
    body_lower = body.lower()
    return any(t in body or t in body_lower for t in tokens)


# 模型輸出滲漏的工具/XML 標記（V4.2.1）：Tool Use 生成時偶爾把標記文字漏進欄位值，
# 例如 "</summary_150>"、"<parameter name=\"key_actors\">"、"<tool_call>"。
# 僅比對「XML 形式的標籤」（<、可選 /、識別字開頭、可選屬性、>），
# 一般新聞正文中的 "3<5"、"A<B公司>" 等不符合屬性語法的內容不受影響。
_MODEL_ARTIFACT_TAG_RE = re.compile(
    r"</?[A-Za-z_][\w.\-]*(?:\s+[\w:\-]+=(?:\"[^\"]*\"|'[^']*'|[\w.\-]+))*\s*/?>")


def strip_model_artifacts(text: str) -> str:
    """
    清除模型結構化輸出滲漏的 XML/工具標記與「字面 \\n」（V4.2.1）。
    模型偶爾會在欄位值內殘留 tool use 標記（如 </summary_150>、
    <parameter name=...>）或輸出兩字元的字面 "\\n" 而非真正換行，
    導致 Word 早報出現亂碼標記。套用於 ModelGateway 全部輸出路徑。
    """
    if not text:
        return text
    text = _MODEL_ARTIFACT_TAG_RE.sub("", text)
    # 字面 "\n"（反斜線+n 兩個字元）轉為真正換行；"\t" 轉空白
    text = text.replace("\\n", "\n").replace("\\t", " ")
    return text.strip()


def strip_artifacts_deep(data):
    """遞迴清洗結構化輸出中所有字串值（dict/list 走訪，其他型別原樣回傳）"""
    if isinstance(data, str):
        return strip_model_artifacts(data)
    if isinstance(data, dict):
        return {k: strip_artifacts_deep(v) for k, v in data.items()}
    if isinstance(data, list):
        return [strip_artifacts_deep(v) for v in data]
    return data


_SENTENCE_ENDINGS = "。！？!?；;…"


def truncate_at_sentence(text: str, max_chars: int) -> str:
    """
    超過 max_chars 時在「最後一個完整句尾」截斷（V4.2.1，摘要 180 字保險）。
    max_chars 內找不到任何句尾標點時退而求其次直接硬切，避免回傳過長內容。
    """
    if not text or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for i in range(len(cut) - 1, -1, -1):
        if cut[i] in _SENTENCE_ENDINGS:
            return cut[:i + 1]
    return cut


def extract_placeholders(template: str) -> set:
    """取出模板中的 {佔位符} 名稱集合（供 Prompt 編輯器缺漏警告使用）"""
    if not template:
        return set()
    return set(re.findall(r"\{(\w+)\}", template))


def extract_keywords_from_taxonomy(taxonomy: str) -> List[str]:
    """從「議題／關鍵字彙整表」free-text（設定頁 keyword_taxonomy）粗略取出個別
    關鍵字詞，只用於新聞正文預覽的加粗提示——不影響 AI 判斷邏輯（那邊仍是整段
    原文交給模型理解語意，見 app/web/routes/retention.py 的
    build_keyword_context()）。格式不強制工整：逐行嘗試切出「議題欄」與「關鍵字
    欄」（用 tab／全形空白／兩個以上空白分隔，找不到就整行當關鍵字欄），關鍵字欄
    再依常見布林/分隔符號拆開。單字元詞（雜訊機率高）不收錄。"""
    if not taxonomy:
        return []
    keywords = set()
    for line in taxonomy.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"\t|　| {2,}", line, maxsplit=1)
        expr = parts[1] if len(parts) > 1 else parts[0]
        for token in re.split(r"[|&()（）,，、\s]+", expr):
            token = token.strip()
            if len(token) >= 2:
                keywords.add(token)
    return sorted(keywords, key=len, reverse=True)


def highlight_keywords(text: str, keywords: List[str]) -> str:
    """把 text 轉成 HTML 安全字串，並將 keywords 中有出現的詞以 <strong> 包住
    （新聞正文預覽加粗提示用）。完整原文照樣輸出、不做任何截斷，只是額外標記。
    keywords 應已由長到短排序，讓較長、較specific 的詞在同一個起始位置優先命中，
    不會被短詞搶先比對到一部分。"""
    if not text:
        return ""
    if not keywords:
        return html.escape(text)
    pattern = "|".join(re.escape(k) for k in keywords if k)
    if not pattern:
        return html.escape(text)
    regex = re.compile(f"({pattern})", re.IGNORECASE)
    parts = regex.split(text)
    out = []
    for i, part in enumerate(parts):
        if not part:
            continue
        escaped = html.escape(part)
        if i % 2 == 1:  # re.split 搭配捕獲群組時，奇數索引是命中的關鍵字本身
            out.append(f"<strong>{escaped}</strong>")
        else:
            out.append(escaped)
    return "".join(out)


def safe_format(template: str, **kwargs) -> str:
    """format 的安全版本：模板缺少某個佔位符時不會 KeyError（保留原樣），
    供「使用者自訂 Prompt 模板可能沒有新版佔位符」的情境使用。"""
    class _SafeDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"
    try:
        return template.format_map(_SafeDict(**kwargs))
    except Exception:
        return template
