"""
新聞輿情系統 Desktop V4.0（Claude 版） — 應用程式進入點

用法：
    python run_desktop.py          # 一般模式
    python run_desktop.py --debug  # debug 模式（詳細 log、Console 視窗）
"""
from __future__ import annotations

import sys
import argparse
from pathlib import Path

# 確保可以用絕對匯入 `app.xxx`（不論從哪個工作目錄執行）
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    parser = argparse.ArgumentParser(description="新聞輿情系統 Desktop V4.0（Claude 版）")
    parser.add_argument("--debug", action="store_true", help="啟用 debug 模式（詳細 log）")
    args = parser.parse_args()

    from PySide6.QtWidgets import QApplication
    from app.controllers.app_context import AppContext
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("新聞輿情系統 Desktop V4.0")
    app.setOrganizationName("NewsSentimentDesktopV4")

    ctx = AppContext(debug=args.debug)

    window = MainWindow(ctx)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
