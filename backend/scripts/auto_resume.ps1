# 东财 K 线域解禁看门狗:每 10 分钟探测一次,解禁后自动触发"继续初始化"补齐遗留
# (30 只 ST 重拉 + 114 板块日线 + 指数刷新),完成后退出。
# 用法: powershell -File scripts\auto_resume.ps1  (建议经 WMI 启动以脱离 IDE 进程树)

$probeUrl = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600519&fields1=f1&fields2=f51,f53&klt=101&fqt=0&beg=20260610&end=20260612'
$logFile = "$PSScriptRoot\..\data\auto_resume.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Log "看门狗启动,每 10 分钟探测东财 K 线域"
for ($i = 0; $i -lt 72; $i++) {  # 最多守 12 小时
    try {
        $resp = Invoke-WebRequest -Uri $probeUrl -TimeoutSec 10 -UseBasicParsing
        if ($resp.StatusCode -eq 200 -and $resp.Content -match '"klines"') {
            Log "K 线域已解禁,触发继续初始化"
            try {
                $r = Invoke-RestMethod -Method POST 'http://127.0.0.1:8000/api/v1/tasks/sync/init' -TimeoutSec 10
                Log "init 已触发: $($r.data.state)"
            } catch {
                Log "触发 init 失败: $($_.Exception.Message)(后端可能未运行)"
            }
            break
        }
        Log "探测响应异常: $($resp.StatusCode)"
    } catch {
        Log "仍在封禁: $($_.Exception.Message.Substring(0, [Math]::Min(80, $_.Exception.Message.Length)))"
    }
    Start-Sleep -Seconds 600
}
Log "看门狗退出"
