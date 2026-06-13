"""星智股 StockNova 桌面端启动器（PyWebView 套壳）。

职责：
1. 把数据目录指向 %APPDATA%/StockNova（打包后程序目录不可写）；
2. 在后台线程启动 uvicorn（FastAPI 同时托管前端静态页）；
3. 等端口就绪后，用 PyWebView 开一个原生窗口加载本地页面；
4. 可选系统托盘（pystray）：关闭窗口最小化到托盘，托盘菜单可显示/退出。

文件名特意取 launcher 而非 app：避免与后端 `app` 包同名，
否则 PyInstaller 会把入口脚本注册成顶层模块 `app`，反过来遮蔽
后端的 `app` 包，导致 `app.main` 打包时找不到。

开发期自测（已构建前端 dist 后）：
    uv run python desktop/launcher.py
打包见同目录 build.ps1（PyInstaller 生成绿色版 zip）。
"""

import os
import socket
import sys
import threading
import time
from pathlib import Path


def _resource_root() -> Path:
    """资源根目录：打包后为 PyInstaller 解包目录，开发期为仓库根。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parents[1]


def _setup_env() -> None:
    """启动前置：数据目录、静态目录、监听地址写入环境变量。"""
    appdata = Path(os.getenv("APPDATA", str(Path.home()))) / "StockNova"
    appdata.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("STOCKNOVA_DATA_DIR", str(appdata))
    os.environ.setdefault("STOCKNOVA_HOST", "127.0.0.1")

    root = _resource_root()
    # 前端构建产物：打包时随包附带 frontend/dist；开发期用仓库内 dist
    os.environ.setdefault("STOCKNOVA_STATIC_DIR", str(root / "frontend" / "dist"))
    # 让 uvicorn 能 import 到 backend 包（app.main）
    backend = root / "backend"
    if backend.exists():
        sys.path.insert(0, str(backend))


PORT = int(os.getenv("STOCKNOVA_PORT", "8000"))
URL = f"http://127.0.0.1:{PORT}"


def _run_server() -> None:
    """后台线程：跑 uvicorn（日志降噪，桌面端不需要 access log）。

    直接导入 app 对象（而非字符串路径），让 PyInstaller 静态分析能顺着
    app.main 的 import 图把 fastapi/akshare/duckdb 等依赖一并打包进去。
    """
    import uvicorn

    from app.main import app as fastapi_app

    uvicorn.run(fastapi_app, host="127.0.0.1", port=PORT, log_level="warning")


def _wait_port(timeout: float = 30.0) -> bool:
    """轮询等待服务端口就绪（后端首启要建库/装配服务，给足时间）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                return True
        time.sleep(0.3)
    return False


def _tray_image():
    """生成托盘图标（无外部资源依赖）：墨黑底 + 金色 S。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (15, 17, 21, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), outline=(212, 175, 55, 255), width=3)
    d.text((24, 20), "S", fill=(212, 175, 55, 255))
    return img


def _start_tray(window) -> None:
    """可选系统托盘：缺 pystray/Pillow 时静默跳过（不影响主窗口）。"""
    try:
        import pystray
    except Exception:  # noqa: BLE001 - 托盘是增强项，缺依赖不报错
        return

    def show(_icon, _item) -> None:
        window.show()

    def quit_app(icon, _item) -> None:
        icon.stop()
        window.destroy()

    menu = pystray.Menu(
        pystray.MenuItem("打开主界面", show, default=True),
        pystray.MenuItem("退出", quit_app),
    )
    icon = pystray.Icon("StockNova", _tray_image(), "星智股 StockNova", menu)
    threading.Thread(target=icon.run, daemon=True).start()


def main() -> None:
    _setup_env()

    import webview

    threading.Thread(target=_run_server, daemon=True).start()
    if not _wait_port():
        # 端口没起来也照常开窗口，页面会显示连接错误，便于排查
        print("警告：后端服务在 30s 内未就绪，窗口可能加载失败")

    window = webview.create_window(
        "星智股 StockNova",
        URL,
        width=1480,
        height=920,
        min_size=(1100, 700),
    )
    _start_tray(window)
    webview.start()


if __name__ == "__main__":
    main()
