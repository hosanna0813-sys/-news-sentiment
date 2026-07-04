"""
PlaywrightScraper — 瀏覽器渲染抓取（Playwright sync API + GNE 中文新聞正文擷取）

定位：作為 BodyScraper（requests + BeautifulSoup 保守擷取）的第二階段 fallback，
處理 JS 渲染型網站與 Python certifi 不信任公司代理憑證造成的 SSL 錯誤網站
（Chromium 使用作業系統憑證庫）。

V4.1.1 修正：
    - 資源阻擋（圖片/媒體）改用 Chromium 啟動參數與 context 設定實現，
      「不使用 page.route() 請求攔截」。route 攔截器在瀏覽器關閉/程式結束時
      仍可能對已斷開的 driver 管線寫入，造成 Node.js EPIPE 未處理例外崩潰
      （RouteDispatcher/_requestInterceptor）。啟動參數方式無此生命週期問題。
    - close() 改為 page → context → browser → driver 逐層關閉，每層獨立防護，
      且加入短暫緩衝讓 driver 完成未竟訊息，避免關閉競態。

設計原則（同 V4.1.0）：
    - sync API（跑在 QThread worker 內，避免管理 asyncio event loop）
    - 回傳與 BodyScraper 相同的 FetchOutcome，worker/資料庫/UI 不需改動
    - 保留合規要求：robots.txt、每網域限速、付費牆偵測、延伸閱讀截斷、
      字數/品質檢查；遇 403／驗證碼／付費牆一律標記失敗，不做規避
    - playwright / gne 延遲 import，未安裝時其他抓取功能不受影響
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional, Dict, List
from urllib.parse import urlparse
from urllib import robotparser

from app.services.scraping.body_scraper import (
    FetchOutcome, _DomainRateLimiter, STOP_MARKERS, PAYWALL_MARKERS,
)
from app.utils.text_utils import normalize_whitespace, word_count_cjk_aware
from app.utils.logging_setup import get_logger

logger = get_logger("playwright_scraper")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ---------------------------------------------------------------------------
# atexit 安全網（V4.1.5）：登錄所有存活的 scraper 實例，Python 程序結束時
# （包含未預期的例外路徑）強制關閉，避免殘留的 Playwright driver（Node 程序）
# 在管線斷開後仍嘗試送事件而拋出 EPIPE 未處理例外。
# ---------------------------------------------------------------------------
import atexit
import weakref

_ACTIVE_SCRAPERS: "weakref.WeakSet" = weakref.WeakSet()


def _close_all_active_scrapers() -> None:
    for scraper in list(_ACTIVE_SCRAPERS):
        try:
            scraper.close()
        except Exception:
            pass


atexit.register(_close_all_active_scrapers)


class PlaywrightScraper:
    """
    用法（與 BodyScraper 相同介面）：
        scraper = PlaywrightScraper(...)
        scraper.start()
        outcome = scraper.fetch(url)
        scraper.close()
    亦支援 context manager。
    """

    def __init__(self, per_domain_delay_sec: float = 2.0, timeout_sec: int = 45,
                 respect_robots_txt: bool = True,
                 gne_noise_nodes: Optional[Dict[str, List[str]]] = None,
                 block_images: bool = True, recycle_every: int = 20):
        self.timeout_sec = timeout_sec
        self.respect_robots_txt = respect_robots_txt
        self.gne_noise_nodes = gne_noise_nodes or {}
        self.block_images = block_images
        self.recycle_every = recycle_every  # 每抓取 N 則自動重啟瀏覽器（預防驅動累積狀態崩潰）
        self._fetch_count = 0
        self._limiter = _DomainRateLimiter(per_domain_delay_sec)
        self._robots_cache: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._extractor = None
        # V4.2.1：記錄建立執行緒與 driver 程序 PID。Playwright sync API 物件綁定
        # 建立執行緒，跨執行緒優雅關閉可能失敗；失敗時以 taskkill /T（Windows）
        # 強制終止 driver 程序樹，避免殘留的 Node driver 對斷開管線寫入造成 EPIPE 崩潰。
        self._owner_thread_id: Optional[int] = None
        self._driver_pid: Optional[int] = None

    # 驅動死亡特徵（Node 端 EPIPE / 管線斷開）：偵測到即重啟整個 Playwright
    _DRIVER_DEATH_HINTS = ("epipe", "broken pipe", "connection closed",
                            "target closed", "browser has been closed",
                            "pipe closed", "writeerror")

    @classmethod
    def _is_driver_death(cls, error: Exception) -> bool:
        msg = str(error).lower()
        return any(h in msg for h in cls._DRIVER_DEATH_HINTS)

    def restart(self) -> bool:
        """完整重啟 Playwright（驅動崩潰復原／定期回收共用）。回傳是否成功。"""
        logger.warning("重啟 Playwright 瀏覽器...")
        try:
            self.close()
        except Exception:
            pass
        try:
            self.start()
            return True
        except Exception as e:
            logger.error(f"Playwright 重啟失敗: {e}")
            return False

    # ---------- 生命週期 ----------
    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # 延遲 import
        except ImportError as e:
            raise RuntimeError(
                "尚未安裝 playwright 套件。請執行：pip install playwright && "
                "playwright install chromium") from e
        try:
            from gne import GeneralNewsExtractor  # 延遲 import
        except ImportError as e:
            raise RuntimeError("尚未安裝 gne 套件。請執行：pip install gne") from e

        launch_args = ["--disable-dev-shm-usage", "--disable-gpu",
                        "--disable-extensions", "--mute-audio"]
        if self.block_images:
            # 以啟動參數停用圖片載入（加速渲染），不使用 page.route() 請求攔截，
            # 避免攔截器在關閉/程式結束階段寫入已斷開管線造成 Node EPIPE 崩潰
            launch_args.append("--blink-settings=imagesEnabled=false")

        self._pw = sync_playwright().start()
        self._owner_thread_id = threading.get_ident()
        self._driver_pid = self._detect_driver_pid()
        try:
            self._browser = self._pw.chromium.launch(headless=True, args=launch_args)
        except Exception as e:
            self._safe_stop_driver()
            raise RuntimeError(
                f"Chromium 啟動失敗（可能尚未執行 playwright install chromium）：{e}") from e

        self._context = self._browser.new_context(
            user_agent=BROWSER_UA,
            java_script_enabled=True,
            service_workers="block",  # 停用 service worker，減少背景連線與關閉競態
        )
        self._page = self._context.new_page()
        self._extractor = GeneralNewsExtractor()
        _ACTIVE_SCRAPERS.add(self)
        logger.info("Playwright 瀏覽器已啟動"
                     + ("（已以啟動參數停用圖片載入）" if self.block_images else ""))

    def close(self) -> None:
        """逐層關閉：page → context → browser → driver，每層獨立防護。
        跨執行緒關閉且優雅關閉失敗時，改以 taskkill /T 終止 driver 程序樹（V4.2.1）。"""
        cross_thread = (self._owner_thread_id is not None
                        and threading.get_ident() != self._owner_thread_id)
        graceful_failed = False
        for name, closer in (
            ("page", lambda: self._page and self._page.close()),
            ("context", lambda: self._context and self._context.close()),
            ("browser", lambda: self._browser and self._browser.close()),
        ):
            try:
                closer()
            except Exception as e:
                graceful_failed = True
                logger.debug(f"關閉 {name} 時發生非致命錯誤: {e}")
        # 給 driver 一點時間送完未竟訊息，再停止（緩解關閉競態）
        time.sleep(0.3)
        if not self._safe_stop_driver():
            graceful_failed = True
        if graceful_failed and cross_thread:
            # sync API 物件綁定建立執行緒，跨執行緒優雅關閉失敗屬預期情況：
            # 直接終止 driver 程序樹，確保不殘留會拋 EPIPE 的 Node 程序
            logger.warning("跨執行緒優雅關閉失敗，強制終止 Playwright driver 程序樹")
            self._kill_driver_process_tree()
        self._page = self._context = self._browser = None
        self._driver_pid = None
        _ACTIVE_SCRAPERS.discard(self)
        logger.info("Playwright 瀏覽器已關閉")

    def _safe_stop_driver(self) -> bool:
        """回傳是否成功停止（或本來就沒有 driver）"""
        ok = True
        try:
            if self._pw:
                self._pw.stop()
        except Exception as e:
            ok = False
            logger.debug(f"停止 Playwright driver 時發生非致命錯誤: {e}")
        self._pw = None
        return ok

    def _detect_driver_pid(self) -> Optional[int]:
        """取得 Playwright driver（Node 子程序）PID。依賴 SDK 私有屬性，
        取不到時回傳 None（僅失去 taskkill 保險，不影響正常功能）。"""
        try:
            proc = self._pw._connection._transport._proc  # noqa: SLF001（無公開 API）
            return getattr(proc, "pid", None)
        except Exception:
            return None

    def _kill_driver_process_tree(self) -> None:
        """強制終止 driver 程序樹：Windows 用 taskkill /T /F，其他平台用 SIGKILL"""
        pid = self._driver_pid
        if not pid:
            logger.debug("無 driver PID 可供強制終止（偵測失敗或已關閉）")
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, signal.SIGKILL)
            logger.info(f"已強制終止 Playwright driver 程序樹（PID {pid}）")
        except Exception as e:
            logger.debug(f"強制終止 driver 程序樹失敗（可能已結束）: {e}")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---------- robots ----------
    def _robots_allows(self, base_url: str, url: str) -> bool:
        if not self.respect_robots_txt:
            return True
        rp = self._robots_cache.get(base_url)
        if base_url not in self._robots_cache:
            rp = robotparser.RobotFileParser()
            rp.set_url(base_url.rstrip("/") + "/robots.txt")
            try:
                rp.read()
            except Exception:
                rp = None
            self._robots_cache[base_url] = rp
        if rp is None:
            return True
        try:
            return rp.can_fetch(BROWSER_UA, url)
        except Exception:
            return True

    # ---------- 抓取 ----------
    def fetch(self, url: str) -> FetchOutcome:
        if self._page is None:
            return FetchOutcome(status="失敗",
                                 detail="瀏覽器尚未啟動（PlaywrightScraper.start 未呼叫或啟動失敗）")

        try:
            parsed = urlparse(url)
        except Exception:
            return FetchOutcome(status="失敗", detail="URL 格式錯誤")
        if not parsed.scheme or not parsed.netloc:
            return FetchOutcome(status="失敗", detail="URL 格式錯誤")

        domain = parsed.netloc
        base_url = f"{parsed.scheme}://{domain}"
        if not self._robots_allows(base_url, url):
            return FetchOutcome(status="略過", detail="robots.txt 禁止抓取")

        # 定期回收：每 recycle_every 則重啟瀏覽器，預防驅動長時間累積狀態崩潰
        self._fetch_count += 1
        if self.recycle_every > 0 and self._fetch_count % self.recycle_every == 0:
            logger.info(f"已抓取 {self._fetch_count} 則，定期回收瀏覽器")
            if not self.restart():
                return FetchOutcome(status="失敗", detail="瀏覽器定期回收後重啟失敗")

        self._limiter.wait_if_needed(domain)

        html, driver_died = self._render(url)
        if driver_died:
            # 驅動死亡（EPIPE 等）：重啟一次並重試該則新聞（每則最多重啟一次，避免無限循環）
            logger.warning(f"偵測到驅動崩潰，重啟後重試: {url}")
            if self.restart():
                self._limiter.wait_if_needed(domain)
                html, driver_died = self._render(url)
            if html is None:
                return FetchOutcome(status="失敗",
                                     detail="瀏覽器驅動崩潰，重啟後仍無法載入頁面")
        if html is None:
            return FetchOutcome(status="失敗", detail=f"頁面載入失敗或逾時（{self.timeout_sec} 秒）")

        if any(m.lower() in html.lower() for m in PAYWALL_MARKERS):
            return FetchOutcome(status="失敗", detail="偵測到登入牆／付費牆標記，不強行擷取")

        try:
            article = self._extractor.extract(
                html, noise_node_list=self.gne_noise_nodes.get(domain, []))
        except Exception as e:
            return FetchOutcome(status="失敗", detail=f"GNE 正文擷取失敗: {e}")

        body_text = normalize_whitespace(article.get("content", "") or "")
        for marker in STOP_MARKERS:
            idx = body_text.find(marker)
            if idx != -1:
                body_text = body_text[:idx]
        body_text = normalize_whitespace(body_text)

        word_count = word_count_cjk_aware(body_text)
        if not body_text or word_count < 50:
            return FetchOutcome(status="失敗",
                                 detail="未取得可用正文（瀏覽器渲染後 GNE 仍無法擷取乾淨主文）")

        quality = min(1.0, word_count / 500)
        return FetchOutcome(status="成功", detail="瀏覽器渲染", body_text=body_text,
                             quality_score=round(quality, 2), word_count=word_count)

    def _render(self, url: str, retries: int = 2):
        """回傳 (html, driver_died)：html 為 None 表示失敗；driver_died 表示偵測到驅動崩潰"""
        for attempt in range(1, retries + 1):
            try:
                self._page.goto(url, wait_until="domcontentloaded",
                                 timeout=self.timeout_sec * 1000)
                self._page.wait_for_timeout(2500)
                self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                self._page.wait_for_timeout(1500)
                return self._page.content(), False
            except Exception as e:
                logger.warning(f"渲染第 {attempt} 次失敗 {url}: {e}")
                if self._is_driver_death(e):
                    return None, True  # 驅動已死，交由 fetch() 重啟處理
                if "crashed" in str(e).lower():
                    # 僅頁面崩潰（驅動仍活著）：重建 page 再試
                    try:
                        self._page.close()
                    except Exception:
                        pass
                    try:
                        self._page = self._context.new_page()
                    except Exception as e2:
                        if self._is_driver_death(e2):
                            return None, True
                        return None, False
                if attempt < retries:
                    try:
                        self._page.wait_for_timeout(2000)
                    except Exception:
                        time.sleep(2)
        return None, False
