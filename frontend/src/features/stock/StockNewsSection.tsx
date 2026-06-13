import { useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'

import { SentimentPanel } from '@/features/news/SentimentPanel'
import { fetchStockFundFlow, fetchStockNews } from '@/lib/api'
import { cn } from '@/lib/utils'

/** 金额（元）→ 带方向色的"亿/万"文本 */
function flowText(v: number): { text: string; cls: string } {
  const yi = v / 1e8
  const text = Math.abs(yi) >= 1 ? `${yi > 0 ? '+' : ''}${yi.toFixed(2)}亿` : `${v > 0 ? '+' : ''}${(v / 1e4).toFixed(0)}万`
  return { text, cls: v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-muted' }
}

/** 个股资金流摘要条（有数据才渲染） */
function FundFlowStrip({ symbol }: { symbol: string }) {
  const ff = useQuery({
    queryKey: ['fundflow', symbol],
    queryFn: () => fetchStockFundFlow(symbol),
    staleTime: 300_000,
  })
  if (!ff.data || ff.data.length === 0) return null
  const latest = ff.data[0]
  const cells: { label: string; v: number }[] = [
    { label: '今日主力净流入', v: latest.main_net },
    { label: '3日累计', v: latest.net_3d },
    { label: '5日累计', v: latest.net_5d },
    { label: '10日累计', v: latest.net_10d },
  ]
  return (
    <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
      {cells.map(({ label, v }) => {
        const { text, cls } = flowText(v)
        return (
          <div key={label} className="rounded-inner border bg-muted/20 px-2.5 py-2">
            <div className="text-[10px] text-text-muted">{label}</div>
            <div className={cn('font-data text-sm font-medium', cls)}>{text}</div>
          </div>
        )
      })}
      <div className="col-span-full text-right text-[10px] text-text-muted">
        主力口径=大单+超大单 · 数据日 {latest.date} · 占比 {latest.main_pct.toFixed(2)}%
      </div>
    </div>
  )
}

/**
 * 个股页"消息面"区块（M5）：资金流摘要 + AI 情绪诊断 + 新闻时间线。
 */
export function StockNewsSection({ symbol, name }: { symbol: string; name?: string }) {
  const news = useQuery({
    queryKey: ['stock-news', symbol],
    queryFn: () => fetchStockNews(symbol),
    enabled: Boolean(symbol),
    staleTime: 600_000,
  })

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {/* 左：资金流 + AI 情绪 */}
      <section className="rounded-card border bg-card p-4">
        <FundFlowStrip symbol={symbol} />
        <SentimentPanel symbol={symbol} name={name} />
      </section>

      {/* 右：新闻时间线 */}
      <section className="rounded-card border bg-card p-4">
        <h3 className="mb-2 text-[13px] font-medium text-muted-foreground">近期新闻</h3>
        {news.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-10 animate-pulse rounded-inner bg-muted/40" />
            ))}
          </div>
        ) : news.isError ? (
          <div className="py-6 text-center text-xs text-text-muted">
            {(news.error as Error).message}
          </div>
        ) : (
          <div className="max-h-80 space-y-2.5 overflow-y-auto pr-1">
            {(news.data ?? []).map((n) => (
              <a
                key={n.code}
                href={n.url}
                target="_blank"
                rel="noreferrer"
                className="group block rounded-inner border border-transparent p-2 transition-colors hover:border-border hover:bg-muted/30"
              >
                <div className="flex items-start justify-between gap-2">
                  <h4 className="text-xs font-medium leading-snug group-hover:text-gold">{n.title}</h4>
                  <ExternalLink className="mt-0.5 size-3 shrink-0 text-text-muted opacity-0 transition-opacity group-hover:opacity-100" />
                </div>
                <div className="mt-1 flex items-center gap-2 font-data text-[10px] text-text-muted">
                  <span>{n.publish_time.slice(5, 16)}</span>
                  {n.media && <span>{n.media}</span>}
                </div>
              </a>
            ))}
            {(news.data ?? []).length === 0 && (
              <div className="py-6 text-center text-xs text-text-muted">暂无近期新闻</div>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
