"""本地启动入口：python run.py

默认只监听本机（127.0.0.1）。要让同一 Wi-Fi 下的手机访问，用环境变量指定：

    PowerShell:  $env:HOST="0.0.0.0"; $env:PORT="5000"; python run.py
    CMD:         set HOST=0.0.0.0 && set PORT=5000 && python run.py

然后在手机浏览器打开 http://<电脑的局域网 IP>:5000
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=True)
