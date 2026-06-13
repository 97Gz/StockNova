import { Star } from 'lucide-react'
import { useNavigate } from 'react-router'

import { useWatchlistQuotes } from '@/features/watchlist/useWatchlistQuotes'
import { formatPct, formatPrice, pctColor } from '@/lib/format'
import { cn } from '@/lib/utils'

/** 仪表盘的自选股快览：前 8 只，点击进个股页。 */
export function WatchlistGlanceCard() {
  const navigate = useNavigate()
  const { items, isLoading } = useWatchlistQuotes()

  if (!isLoading && items.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-text-muted">
        <Star className="size-6 opacity-40" />
        <p className="text-xs">还没有自选股</p>
        <button
          className="rounded-control border border-gold/40 px-3 py-1 text-xs text-gold hover:bg-gold/10"
          onClick={() => navigate('/watchlist')}
        >
          去添加
        </button>
      </div>
    )
  }

  return (
    <ul className="divide-y divide-border/60">
      {items.slice(0, 8).map((it) => (
        <li key={it.symbol}>
          <button
            className="flex w-full items-center justify-between py-1.5 text-left transition-colors hover:bg-accent/40"
            onClick={() => navigate(`/stock/${it.symbol}`)}
          >
            <span className="min-w-0">
              <span className="block truncate text-xs">{it.name}</span>
              <span className="font-data text-[10px] text-text-muted">{it.symbol}</span>
            </span>
            <span className="text-right">
              <span className={cn('block font-data text-sm', pctColor(it.quote?.pct_change ?? 0))}>
                {it.quote ? formatPrice(it.quote.price) : '--'}
              </span>
              <span className={cn('font-data text-[10px]', pctColor(it.quote?.pct_change ?? 0))}>
                {it.quote ? formatPct(it.quote.pct_change) : '--'}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
