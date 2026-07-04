"""通用工具：ID 產生、文字清理、JSON 安全解析"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional


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
