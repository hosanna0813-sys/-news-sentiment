"""
正文抓取服務 — 對應規格書 八

保守策略：
    1. 優先辨識 <article>、<main>、JSON-LD articleBody、常見主文容器 class/id。
    2. 以 H1 標題作為主文定位輔助。
    3. 只保留連續正文段落。
    4. 遇到「延伸閱讀、相關新聞、更多報導、熱門新聞、推薦閱讀」等標記即停止。
    5. 無法取得乾淨主文時，標記「未取得可用正文」，不可硬塞整頁文字。

不繞過登入牆、付費牆、驗證碼、反爬蟲或 robots 規則：
    - 呼叫前檢查 robots.txt（可透過設定停用，但預設遵守）。
    - 遇到明顯的登入/付費牆關鍵字，直接標記失敗，不強行擷取殘缺內容。

每個網域限速：以最後一次請求時間戳記錄，確保同網域請求間隔 >= per_domain_delay_sec。
"""
from __future__ import annotations

import json
import re
import time
import threading
from dataclasses import dataclass
from typing import Optional, Dict
from urllib.parse import urlparse
from urllib import robotparser

from app.utils.logging_setup import get_logger
from app.utils.text_utils import normalize_whitespace, word_count_cjk_aware

logger = get_logger("scraper")

# 讓 Python 的 SSL 驗證改用作業系統憑證庫（Windows 憑證存放區）。
# 公司網路代理/防火牆的 TLS 檢查憑證通常已安裝在系統憑證庫中，
# 但 requests 預設用 certifi 自帶憑證庫而不認得它，導致大量 SSL 憑證錯誤。
# truststore 注入後即可信任系統憑證，這比停用 SSL 驗證安全得多。
_TRUSTSTORE_OK = False
try:
    import truststore  # type: ignore
    truststore.inject_into_ssl()
    _TRUSTSTORE_OK = True
    logger.info("已啟用 truststore：SSL 驗證改用作業系統憑證庫")
except Exception as _e:  # 未安裝或注入失敗時維持原行為
    logger.info(f"truststore 未啟用（{_e}），SSL 驗證使用 certifi 預設憑證庫")

STOP_MARKERS = ["延伸閱讀", "相關新聞", "更多報導", "熱門新聞", "推薦閱讀", "延伸閱讀：", "你可能也想看"]
PAYWALL_MARKERS = ["訂閱會員", "付費閱讀", "登入後閱讀全文", "會員限定", "subscribe to continue", "paywall"]
MAIN_CONTAINER_SELECTORS = [
    "article", "main",
    '[itemprop="articleBody"]', ".article-content", ".article-body", ".story-body",
    "#article-content", "#story", ".post-content", ".entry-content",
]


@dataclass
class FetchOutcome:
    status: str          # 成功 / 失敗 / 略過
    detail: str
    body_text: str = ""
    quality_score: float = 0.0
    word_count: int = 0


class _DomainRateLimiter:
    def __init__(self, delay_sec: float):
        self.delay_sec = delay_sec
        self._last_access: Dict[str, float] = {}
        self._lock = threading.Lock()

    def wait_if_needed(self, domain: str) -> None:
        with self._lock:
            last = self._last_access.get(domain, 0)
            wait = self.delay_sec - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        with self._lock:
            self._last_access[domain] = time.time()


class BodyScraper:
    def __init__(self, per_domain_delay_sec: float = 2.0, timeout_sec: int = 15,
                 user_agent: str = "NewsSentimentDesktop/4.0", respect_robots_txt: bool = True,
                 verify_ssl: bool = True, site_selectors: Optional[Dict[str, str]] = None):
        self.timeout_sec = timeout_sec
        self.user_agent = user_agent
        self.respect_robots_txt = respect_robots_txt
        self.verify_ssl = verify_ssl
        # 站點專屬 CSS selector（V4.2.0）：{domain子字串: css selector}
        # 命中時 requests+BeautifulSoup 直接抽主文，省去 Playwright 成本；
        # 未命中或抽取失敗時回退通用啟發式擷取。
        self.site_selectors = site_selectors or {}
        if not verify_ssl:
            # 使用者明確選擇停用驗證（例如公司代理環境），關閉 urllib3 的重複警告
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
        self._limiter = _DomainRateLimiter(per_domain_delay_sec)
        self._robots_cache: Dict[str, Optional[robotparser.RobotFileParser]] = {}

    def _get_robots(self, base_url: str) -> Optional[robotparser.RobotFileParser]:
        if base_url in self._robots_cache:
            return self._robots_cache[base_url]
        rp = robotparser.RobotFileParser()
        rp.set_url(base_url.rstrip("/") + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None
        self._robots_cache[base_url] = rp
        return rp

    def fetch(self, url: str) -> FetchOutcome:
        import requests

        try:
            parsed = urlparse(url)
        except Exception:
            return FetchOutcome(status="失敗", detail="URL 格式錯誤")
        if not parsed.scheme or not parsed.netloc:
            return FetchOutcome(status="失敗", detail="URL 格式錯誤")

        domain = parsed.netloc
        base_url = f"{parsed.scheme}://{domain}"

        if self.respect_robots_txt:
            rp = self._get_robots(base_url)
            if rp is not None:
                try:
                    if not rp.can_fetch(self.user_agent, url):
                        return FetchOutcome(status="略過", detail="robots.txt 禁止抓取")
                except Exception:
                    pass

        self._limiter.wait_if_needed(domain)

        import requests as _requests
        try:
            resp = self._session_get(url)
        except _requests.exceptions.SSLError as e:
            hint = ""
            if self.verify_ssl:
                hint = "（若您位於公司網路/代理環境，可至系統設定→正文抓取設定，停用 SSL 憑證驗證後重試）"
            return FetchOutcome(status="失敗", detail=f"SSL 憑證錯誤{hint}: {e}")
        except _requests.exceptions.Timeout:
            return FetchOutcome(status="失敗", detail=f"逾時（超過 {self.timeout_sec} 秒無回應）")
        except _requests.exceptions.ConnectionError as e:
            return FetchOutcome(status="失敗", detail=f"連線失敗: {e}")
        except Exception as e:
            return FetchOutcome(status="失敗", detail=f"未預期錯誤: {e}")

        if resp is None:
            return FetchOutcome(status="失敗", detail="無回應")

        status_code = resp.status_code
        if status_code == 403:
            return FetchOutcome(status="失敗", detail="403 Forbidden（可能為反爬蟲或需登入）")
        if status_code == 404:
            return FetchOutcome(status="失敗", detail="404 Not Found")
        if status_code >= 500:
            return FetchOutcome(status="失敗", detail=f"伺服器錯誤 {status_code}")
        if status_code != 200:
            return FetchOutcome(status="失敗", detail=f"HTTP {status_code}")

        html = resp.text
        if any(marker.lower() in html.lower() for marker in PAYWALL_MARKERS):
            return FetchOutcome(status="失敗", detail="偵測到登入牆／付費牆標記，不強行擷取")

        # 站點專屬 selector 優先（V4.2.0）
        body_text = self._extract_by_site_selector(url, html)
        detail = "站點selector" if body_text else ""
        if not body_text:
            body_text = self._extract_main_text(html)
        if not body_text or word_count_cjk_aware(body_text) < 50:
            return FetchOutcome(status="失敗", detail="未取得可用正文（無法辨識乾淨主文容器）")

        word_count = word_count_cjk_aware(body_text)
        quality = min(1.0, word_count / 500)
        return FetchOutcome(status="成功", detail=detail, body_text=body_text,
                             quality_score=round(quality, 2), word_count=word_count)

    def _extract_by_site_selector(self, url: str, html: str) -> str:
        """以站點專屬 CSS selector 擷取主文；未設定/未命中/內容不足回傳空字串以回退通用擷取"""
        selector = None
        for domain_key, sel in self.site_selectors.items():
            if domain_key and domain_key in url:
                selector = sel
                break
        if not selector:
            return ""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            node = soup.select_one(selector)
            if node is None:
                return ""
            paragraphs = [normalize_whitespace(p.get_text()) for p in node.find_all("p")]
            paragraphs = [p for p in paragraphs if p]
            if not paragraphs:
                text = normalize_whitespace(node.get_text())
                paragraphs = [text] if text else []
            body = "\n".join(paragraphs)
            for marker in STOP_MARKERS:
                idx = body.find(marker)
                if idx != -1:
                    body = body[:idx]
            body = normalize_whitespace(body)
            return body if word_count_cjk_aware(body) >= 50 else ""
        except Exception:
            return ""

    def _session_get(self, url: str):
        import requests
        headers = {"User-Agent": self.user_agent}
        return requests.get(url, headers=headers, timeout=self.timeout_sec,
                             allow_redirects=True, verify=self.verify_ssl)

    def _extract_main_text(self, html: str) -> str:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # 1) 優先嘗試 JSON-LD articleBody
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "{}")
                candidates = data if isinstance(data, list) else [data]
                for c in candidates:
                    if isinstance(c, dict) and c.get("articleBody"):
                        text = normalize_whitespace(c["articleBody"])
                        if text:
                            return self._truncate_at_stop_marker(text)
            except Exception:
                continue

        # 2) 移除明顯的雜訊區塊
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe"]):
            tag.decompose()

        # 3) 依常見主文容器 selector 尋找
        container = None
        for sel in MAIN_CONTAINER_SELECTORS:
            found = soup.select_one(sel)
            if found and len(found.get_text(strip=True)) > 100:
                container = found
                break

        if container is None:
            # 4) 找不到明確容器則放棄，不硬塞整頁文字
            return ""

        paragraphs = []
        for p in container.find_all(["p"]):
            text = normalize_whitespace(p.get_text(" ", strip=True))
            if not text:
                continue
            if any(marker in text for marker in STOP_MARKERS):
                break  # 遇到延伸閱讀等標記即停止
            paragraphs.append(text)

        body_text = "\n".join(paragraphs)
        return self._truncate_at_stop_marker(body_text)

    def _truncate_at_stop_marker(self, text: str) -> str:
        for marker in STOP_MARKERS:
            idx = text.find(marker)
            if idx != -1:
                text = text[:idx]
        return normalize_whitespace(text)
