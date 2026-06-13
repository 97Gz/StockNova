# 星智股一键开发脚本：同时启动后端(8000)与前端(5173)
# 用法：在仓库根目录执行  .\scripts\dev.ps1
# 停止：关闭弹出的两个窗口即可

$root = Split-Path -Parent $PSScriptRoot

# 后端：uv 管理的 FastAPI（--reload 改代码自动重启）
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$root\backend'; uv run uvicorn app.main:app --reload --port 8000"
) -WindowStyle Normal

# 前端：Vite 开发服务器（已配置 /api 与 /ws 代理到后端）
# 注意：本机 npm 直连官方源会卡死，统一使用 pnpm（.npmrc 已固定国内镜像）
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$root\frontend'; pnpm run dev"
) -WindowStyle Normal

Write-Host "星智股开发环境启动中："
Write-Host "  后端  http://127.0.0.1:8000  (API 文档 /docs)"
Write-Host "  前端  http://127.0.0.1:5173"
