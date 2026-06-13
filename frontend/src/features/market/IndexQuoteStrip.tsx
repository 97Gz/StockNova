import { useQuery } from '@tanstack/react-query'

import { fetchIndices } from '@/lib/api'
import { formatAmount, formatPct, formatPrice, pctColor } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * 指数行情条：6 大核心指数横排卡片（仪表盘顶部）。
 * 盘中 30 秒自动刷新；盘后接口返回收盘数据，无需区分时段。
 */
export function IndexQuoteStrip() {
  const { data, isLoading } = useQuery({
    queryKey: ['indices'],
    queryFn: fetchIndices,
    refetchInterval: 30_000,
  })

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-[88px] animate-pulse rounded-inner border bg-card" />
        ))}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      {(data ?? []).map((idx) => (
        <div
          key={idx.symbol}
          className="rounded-inner border bg-card p-3 transition-[border-color] hover:border-gold/30"
        >
          <div className="flex items-baseline justify-between">
            <span className="text-xs text-muted-foreground">{idx.name}</span>
            <span className="font-data text-[10px] text-text-muted">{idx.symbol}</span>
          </div>
          <div className={cn('mt-1 font-data text-lg font-semibold', pctColor(idx.pct_change))}>
            {formatPrice(idx.price)}
          </div>
          <div className="flex items-center justify-between text-[11px]">
            <span className={cn('font-data', pctColor(idx.pct_change))}>
              {formatPct(idx.pct_change)}
            </span>
            <span className="font-data text-text-muted">{formatAmount(idx.amount)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}
