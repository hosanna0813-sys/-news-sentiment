"""集中式 logging 設定，所有模組共用同一個 logger 家族。"""
from __future__ import annotations

import logging
import logging.handlers
from .paths import get_logs_dir

_CONFIGURED = False


def setup_logging(debug: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = logging.DEBUG if debug else logging.INFO
    log_file = get_logs_dir() / "app.log"

    root = logging.getLogger("nsd")
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # 絕不記錄 API Key：呼叫端必須自行遮罩，這裡僅提供一個提醒性 filter
    class _NoSecretFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = str(record.getMessage())
            if "sk-ant-" in msg:
                record.msg = "[已遮罩：訊息內含疑似 API Key，已阻擋寫入 log]"
                record.args = ()
            return True

    root.addFilter(_NoSecretFilter())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"nsd.{name}")
