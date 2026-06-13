# 同步任务监督探针：每 30 秒打一行状态 + API 延迟，直到任务结束
# 用途：E2E 验证「同步期间 API 持续可用」这条验收标准
param([int]$IntervalSec = 30)

while ($true) {
    $ts = Get-Date -Format "HH:mm:ss"
    try {
        $t = Measure-Command {
            $script:s = (Invoke-RestMethod http://127.0.0.1:8000/api/v1/tasks/sync/status -TimeoutSec 10).data
        }
        $ms = [math]::Round($t.TotalMilliseconds)
        Write-Host ("[{0}] state={1} phase={2} done={3}/{4} failed={5} api={6}ms msg={7}" -f `
            $ts, $s.state, $s.phase, $s.done, $s.total, $s.failed, $ms, $s.message)
        if ($s.state -in @("done", "failed", "cancelled", "idle")) {
            Write-Host "SYNC_FINISHED state=$($s.state)"
            break
        }
    }
    catch {
        Write-Host "[$ts] API_TIMEOUT_OR_ERROR: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSec
}
