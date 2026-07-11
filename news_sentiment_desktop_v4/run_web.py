"""網頁版本機測試進入點：python run_web.py

雲端部署（Render）用 gunicorn 啟動（見 Procfile），不會執行這個檔案；這裡只是
方便在自己電腦上用 Flask 內建伺服器測試。
"""
from __future__ import annotations

import os
import webbrowser
import threading

from app.web.server import create_app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    # 本機用 http 測試 Gmail OAuth 時，google-auth-oauthlib 預設要求 https；
    # 部署到 Render 一律是 https，不受這行影響。
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    app = create_app()
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    # threaded=False：與正式部署的 gunicorn -w 1（單 worker、序列處理）行為一致。
    # Flask 內建伺服器預設多執行緒，本機測試時多個分頁同時操作 SQLite 會踩到
    # 正式環境不存在的並發情境，掩蓋或製造誤導性的問題。
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("NSD_DEBUG") == "1", threaded=False)
