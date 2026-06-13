import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router'

import { fetchTodaySignals } from '@/lib/api'

/**
 * 首页「今日策略信号精选」卡：跑批共振 Top 列表的紧凑版。
 * 点行进个股页，点底部链接进完整的选股结果页。
 */
export function TodaySignalsCard() {
  const navigate = useNavigate()
  const signals = useQuery({
    queryKey: ['today-signals'],
    queryFn: () => fetchTodaySignals(),
    staleTime: 5 * 60_000,
  })

  if (signals.isLoading) {
    return <div className="h-full animate-pulse rounded-inner bg-muted/40" />
  }

  const data = signals.data
  if (!data?.trade_date || data.items.length === 0) {
    return (
      <div className="flex h-full min-h-20 flex-col items-center justify-center gap-2">
        <span className="text-xs text-text-muted/60">今天还没有跑批信号</span>
        <button
          className="rounded-control border border-gold/40 px-3 py-1 text-xs text-gold hover:bg-gold/10"
          onClick={() => navigate('/signals')}
        >
          去生成 →
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <ul className="flex-1 space-y-1">
        {data.items.slice(0, 6).map((it) => (
          <li key={it.symbol}>
            <button
              className="flex w-full items-center justify-between gap-2 rounded-inner px-2 py-1.5 text-left transition-colors hover:bg-accent/50"
              onClick={() => navigate(`/stock/${it.symbol}`)}
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate text-[12px]">{it.name}</span>
                <span className="shrink-0 font-data text-[10px] text-text-muted">{it.symbol}</span>
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                <span className="hidden max-w-40 truncate text-[10px] text-text-muted xl:inline">
                  {it.strategies.slice(0, 2).join(' · ')}
                </span>
                <span className="rounded-full border border-gold/40 bg-gold/10 px-1.5 py-px font-data text-[10px] text-gold">
                  {it.hit_count} 策略
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      <button
        className="mt-2 w-full rounded-inner border border-border py-1.5 text-[11px] text-muted-foreground transition-colors hover:border-gold/30 hover:text-gold"
        onClick={() => navigate('/signals')}
      >
        查看完整选股结果（{data.trade_date}）→
      </button>
    </div>
  )
}
