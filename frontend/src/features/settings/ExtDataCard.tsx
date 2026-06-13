import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  ApiError,
  fetchExtSyncStatus,
  triggerExtSync,
  triggerMinuteSync,
  type ExtTableStat,
} from '@/lib/api'

const TABLES: { key: keyof Awaited<ReturnType<typeof fetchExtSyncStatus>>['stats']; label: string }[] = [
  { key: 'fund_flow', label: '资金流快照' },
  { key: 'dragon_tiger', label: '龙虎榜明细' },
  { key: 'earnings', label: '业绩预告' },
  { key: 'popularity', label: '人气榜' },
  { key: 'minute_bars', label: '5分钟K线' },
]

/**
 * 扩展数据卡（M5）：资金流/龙虎榜/业绩预告/人气榜/5分钟线的库存与手动同步。
 * 扩展数据每交易日 15:45 自动同步；5分钟线 15:40 自动同步（盘中走势策略原料）。
 */
export function ExtDataCard() {
  const queryClient = useQueryClient()
  const status = useQuery({
    queryKey: ['ext-sync-status'],
    queryFn: fetchExtSyncStatus,
    // 任一同步进行中密集刷新看进度，平时低频
    refetchInterval: (q) =>
      q.state.data?.running || q.state.data?.minute?.running ? 3_000 : 60_000,
  })

  const trigger = useMutation({
    mutationFn: triggerExtSync,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ext-sync-status'] }),
  })
  const minuteTrigger = useMutation({
    mutationFn: triggerMinuteSync,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ext-sync-status'] }),
  })

  const d = status.data
  const running = d?.running ?? false
  const minuteRunning = d?.minute?.running ?? false
  const progress = d?.minute?.progress

  return (
    <section className="col-span-12 rounded-card border bg-card p-5 lg:col-span-5">
      <header className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">扩展数据（M5）</h3>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={minuteRunning || minuteTrigger.isPending}
            onClick={() => minuteTrigger.mutate()}
            title="拉取当日全市场 5 分钟K线（约 2~4 分钟），盘中走势策略的数据原料"
          >
            {minuteRunning || minuteTrigger.isPending ? (
              <span className="flex items-center gap-1.5">
                <Loader2 className="size-3.5 animate-spin" />
                {progress ? `${progress.done}/${progress.total}` : '分钟线…'}
              </span>
            ) : (
              '同步分钟线'
            )}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={running || trigger.isPending}
            onClick={() => trigger.mutate()}
            title="拉取资金流/龙虎榜/业绩预告/人气榜（约 1~2 分钟）"
          >
            {running || trigger.isPending ? (
              <span className="flex items-center gap-1.5">
                <Loader2 className="size-3.5 animate-spin" /> 同步中…
              </span>
            ) : (
              <span className="flex items-center gap-1.5">
                <RefreshCw className="size-3.5" /> 立即同步
              </span>
            )}
          </Button>
        </div>
      </header>
      <p className="mt-1 text-xs text-text-muted">
        资金流 / 龙虎榜 / 业绩预告 / 人气榜 15:45 自动同步 · 5分钟线 15:40 自动同步，均从接入日起逐日积累
      </p>

      <dl className="mt-4 space-y-2.5">
        {TABLES.map(({ key, label }) => {
          const stat: ExtTableStat | undefined = d?.stats?.[key]
          return (
            <div key={key} className="flex items-center justify-between text-[13px]">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-data text-foreground">
                {stat ? (
                  <>
                    {stat.rows.toLocaleString('zh-CN')} 行
                    <span className="ml-2 text-text-muted">
                      {stat.latest ? `最新 ${stat.latest}` : '暂无数据'}
                    </span>
                  </>
                ) : (
                  '—'
                )}
              </dd>
            </div>
          )
        })}
      </dl>

      {(trigger.isError || minuteTrigger.isError) && (
        <div className="mt-3 rounded-control border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
          {((trigger.error ?? minuteTrigger.error) as ApiError).message}
        </div>
      )}
    </section>
  )
}
