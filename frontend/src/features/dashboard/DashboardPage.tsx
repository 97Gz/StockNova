import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { AmountTrendCard } from '@/features/market/AmountTrendCard'
import { BoardHeatCard } from '@/features/market/BoardHeatCard'
import { DistributionCard } from '@/features/market/DistributionCard'
import { IndexQuoteStrip } from '@/features/market/IndexQuoteStrip'
import { IndexTrendCard } from '@/features/market/IndexTrendCard'
import { MarketNewsCard } from '@/features/market/MarketNewsCard'
import { MarketSummaryCard } from '@/features/market/MarketSummaryCard'
import { TodaySignalsCard } from '@/features/strategy/TodaySignalsCard'
import { WatchlistGlanceCard } from '@/features/watchlist/WatchlistGlanceCard'
import { fetchMarketOverview } from '@/lib/api'
import { formatAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Bento 卡片外壳：统一边框 / 圆角 / hover 行为（稳定 hover，不位移） */
function Card({
  title,
  extra,
  className,
  children,
}: {
  title: string
  extra?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <section
      className={cn(
        'group flex flex-col rounded-card border bg-card p-4',
        'transition-[border-color,box-shadow] duration-200',
        'hover:border-gold/30 hover:shadow-[0_8px_30px_-12px_rgba(0,0,0,0.4)]',
        className,
      )}
    >
      <header className="mb-2 flex items-center justify-between">
        <h3 className="text-[13px] font-medium text-muted-foreground">{title}</h3>
        {extra}
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  )
}

/**
 * 今日盘面（M2 真实数据版）：
 * 指数条 → 大盘走势 + 市场温度 + 行业热力 → 涨跌分布 + 成交趋势 + 自选快览
 * → 策略信号 / AI 摘要 / 要闻（M3/M5 占位）。
 */
export function DashboardPage() {
  const overview = useQuery({
    queryKey: ['market-overview'],
    queryFn: fetchMarketOverview,
    staleTime: 5 * 60_000,
  })
  const ov = overview.data

  return (
    <div className="space-y-4">
      <IndexQuoteStrip />

      <div className="grid grid-cols-12 gap-4">
        <Card
          title="上证指数 · 近半年走势与量能"
          extra={
            ov && (
              <span className="font-data text-[10px] text-text-muted">{ov.trade_date}</span>
            )
          }
          className="col-span-12 min-h-72 lg:col-span-6"
        >
          <IndexTrendCard />
        </Card>

        <Card title="市场温度计" className="col-span-12 min-h-72 md:col-span-6 lg:col-span-3">
          {ov ? (
            <div className="flex h-full flex-col">
              <div className="grid grid-cols-2 gap-2 text-center">
                <div className="rounded-inner bg-up/10 py-2">
                  <div className="font-data text-xl font-semibold text-up">{ov.up}</div>
                  <div className="text-[10px] text-text-muted">上涨家数</div>
                </div>
                <div className="rounded-inner bg-down/10 py-2">
                  <div className="font-data text-xl font-semibold text-down">{ov.down}</div>
                  <div className="text-[10px] text-text-muted">下跌家数</div>
                </div>
                <div className="rounded-inner bg-up/10 py-2">
                  <div className="font-data text-lg font-semibold text-up">{ov.limit_up}</div>
                  <div className="text-[10px] text-text-muted">涨停</div>
                </div>
                <div className="rounded-inner bg-down/10 py-2">
                  <div className="font-data text-lg font-semibold text-down">{ov.limit_down}</div>
                  <div className="text-[10px] text-text-muted">跌停</div>
                </div>
              </div>
              <div className="mt-3 rounded-inner border border-gold/20 bg-gold/5 py-2 text-center">
                <div className="font-data text-lg font-semibold text-gold">
                  {formatAmount(ov.total_amount)}
                </div>
                <div className="text-[10px] text-text-muted">两市成交额（{ov.trade_date}）</div>
              </div>
              {/* 涨跌占比条 */}
              <div className="mt-auto pt-3">
                <div className="flex h-2 overflow-hidden rounded-full">
                  <div className="bg-up" style={{ width: `${(ov.up / (ov.up + ov.down + ov.flat)) * 100}%` }} />
                  <div className="bg-flat/50" style={{ width: `${(ov.flat / (ov.up + ov.down + ov.flat)) * 100}%` }} />
                  <div className="bg-down" style={{ width: `${(ov.down / (ov.up + ov.down + ov.flat)) * 100}%` }} />
                </div>
                <div className="mt-1 flex justify-between text-[10px] text-text-muted">
                  <span>红盘 {((ov.up / (ov.up + ov.down + ov.flat)) * 100).toFixed(0)}%</span>
                  <span>绿盘 {((ov.down / (ov.up + ov.down + ov.flat)) * 100).toFixed(0)}%</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="h-full animate-pulse rounded-inner bg-muted/40" />
          )}
        </Card>

        <Card title="行业热力图" className="col-span-12 min-h-72 md:col-span-6 lg:col-span-3">
          <BoardHeatCard />
        </Card>

        <Card title="涨跌分布" className="col-span-12 min-h-56 md:col-span-6 lg:col-span-5">
          {ov ? (
            <DistributionCard overview={ov} />
          ) : (
            <div className="h-full animate-pulse rounded-inner bg-muted/40" />
          )}
        </Card>

        <Card title="两市成交额趋势 · 30日" className="col-span-12 min-h-56 md:col-span-6 lg:col-span-4">
          {ov ? (
            <AmountTrendCard overview={ov} />
          ) : (
            <div className="h-full animate-pulse rounded-inner bg-muted/40" />
          )}
        </Card>

        <Card title="自选股快览" className="col-span-12 min-h-56 lg:col-span-3">
          <WatchlistGlanceCard />
        </Card>

        <Card title="今日策略信号精选" className="col-span-12 min-h-56 lg:col-span-4">
          <TodaySignalsCard />
        </Card>
        <Card title="AI 盘面摘要" className="col-span-12 min-h-56 md:col-span-6 lg:col-span-4">
          <MarketSummaryCard />
        </Card>
        <Card title="市场要闻" className="col-span-12 min-h-56 md:col-span-6 lg:col-span-4">
          <MarketNewsCard />
        </Card>
      </div>
    </div>
  )
}
