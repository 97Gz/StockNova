# 星智股 StockNova · Windows 桌面端打包脚本（PyInstaller 绿色版）
#
# 用法（在仓库根目录的 PowerShell 执行）：
#     ./desktop/build.ps1
# 产物：
#     desktop/dist/StockNova/        —— 可直接运行的绿色版目录（StockNova.exe）
#     release/StockNova-<版本>-win64.zip —— 打包好的发布 zip
#
# 说明：
# - 数据库不随包分发，首次运行后落在 %APPDATA%/StockNova，体积小、可迁移；
# - akshare 含较多数据文件与动态导入，用 --collect-all 确保完整；
# - 若某第三方包运行时报缺模块，按提示在 --collect-all / --hidden-import 补一行。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$version = "0.1.0"

Write-Host "==> [1/4] 构建前端（pnpm build）..." -ForegroundColor Cyan
Push-Location "$root/frontend"
pnpm install --frozen-lockfile
pnpm run build
Pop-Location

Write-Host "==> [2/4] 安装桌面打包依赖（uv sync --group desktop）..." -ForegroundColor Cyan
Push-Location "$root/backend"
uv sync --group desktop

Write-Host "==> [3/4] PyInstaller 打包..." -ForegroundColor Cyan
uv run pyinstaller `
  --noconfirm --clean `
  --name StockNova `
  --windowed `
  --distpath "$root/desktop/dist" `
  --workpath "$root/desktop/build" `
  --specpath "$root/desktop" `
  --paths "$root/backend" `
  --add-data "$root/frontend/dist;frontend/dist" `
  --collect-all akshare `
  --collect-all duckdb `
  --collect-all apscheduler `
  --collect-submodules app `
  --hidden-import app.main `
  "$root/desktop/launcher.py"
Pop-Location

Write-Host "==> [4/4] 压缩为发布 zip..." -ForegroundColor Cyan
$releaseDir = "$root/release"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$zip = "$releaseDir/StockNova-v$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$root/desktop/dist/StockNova/*" -DestinationPath $zip

Write-Host "完成 ✓ 发布包：$zip" -ForegroundColor Green
