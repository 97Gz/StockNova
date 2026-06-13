import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ExternalLink, Loader2, Newspaper, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

import { SentimentPanel } from '@/features/news/SentimentPanel'
import {
  fetchNewsFeed,
  fetchStockNews,
  fetchWatchlist,
  searchStocks,
  type NewsItem,
} from '@/lib/api'
import { cn } from '@/lib/utils'

/** 快讯时间：今天只显示时分，更早带日期 */
function feedTime(ts: string): string {
  const today = new Date().toISOString().slice(0, 10)
  if (ts.startsWith(today)) return ts.slice(11, 16)
  return `${ts.slice(5, 10)} ${ts.slice(11, 16)}`
}

/** 单条快讯（时间线样式） */
function FeedRow({ item, onStock }: { item: NewsItem; onStock: (s: string) => void }) {
  // 快讯标题与摘要常常重复（标题就是摘要的【】部分），重复时只展示摘要
  const showTitle = item.title && !item.summary.replace(/\s/g, '').includes(item.title.replace(/\s/g, ''))
  return (
    <article className="group relative border-l border-border pb-4 pl-4 last:pb-0">
      <span className="absolute -left-[3px] top-1.5 size-[5px] rounded-full bg-gold/70 transition-colors group-hover:bg-gold" />
      <div className="font-data text-[10px] text-text-muted">{feedTime(item.publish_time)}</div>
      {showTitle && <h4 className="mt-0.5 text-[13px] font-medium leading-snug">{item.title}</h4>}
      <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{item.summary}</p>
      {item.stocks && item.stocks.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {item.stocks.slice(0, 6).map((s) => (
            <button
              key={s}
              onClick={() => onStock(s)}
              className="rounded border border-gold/30 px-1.5 py-0.5 font-data text-[10px] text-gold transition-colors hover:bg-gold/10"
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </article>
  )
}

/** 左侧：7×24 快讯流（60s 自动刷新 + 游标加载更早） */
function FeedColumn() {
  const navigate = useNavigate()
  const [column, setColumn] = useState<'102' | '101'>('102')
  // 翻页累积的更早内容（首页之外的部分）
  const [older, setOlder] = useState<NewsItem[]>([])
  const [cursor, setCursor] = useState('')
  const [loadingMore, setLoadingMore] = useState(false)

  const head = useQuery({
    queryKey: ['news-feed', column],
    queryFn: () => fetchNewsFeed('', column),
    refetchInterval: 60_000, // 与后端 60s 缓存对齐
  })

  // tab 切换：换栏目的同时清空累积的翻页内容
  function switchColumn(next: '102' | '101') {
    setColumn(next)
    setOlder([])
    setCursor('')
  }

  const items = useMemo(() => {
    const seen = new Set<string>()
    const merged: NewsItem[] = []
    for (const it of [...(head.data?.items ?? []), ...older]) {
      if (!seen.has(it.code)) {
        seen.add(it.code)
        merged.push(it)
      }
    }
    return merged
  }, [head.data, older])

  async function loadMore() {
    const next = cursor || head.data?.next_cursor
    if (!next) return
    setLoadingMore(true)
    try {
      const page = await fetchNewsFeed(next, column)
      setOlder((prev) => [...prev, ...page.items])
      setCursor(page.next_cursor)
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <section className="rounded-card border bg-card p-4">
      <header className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground">
          <Newspaper className="size-3.5 text-gold" />
          7×24 全球财经快讯
        </h3>
        <div className="flex gap-1 rounded-control border p-0.5">
          {(
            [
              ['102', '全部'],
              ['101', '重点'],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => switchColumn(key)}
              className={cn(
                'rounded px-2.5 py-1 text-[11px] transition-colors',
                column === key ? 'bg-gold/15 text-gold' : 'text-text-muted hover:text-foreground',
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      {head.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-14 animate-pulse rounded-inner bg-muted/40" />
          ))}
        </div>
      ) : head.isError ? (
        <div className="py-12 text-center text-sm text-text-muted">
          快讯源暂时不可用：{(head.error as Error).message}
        </div>
      ) : (
        <>
          <div className="max-h-[calc(100vh-15rem)] space-y-0 overflow-y-auto pr-1">
            {items.map((it) => (
              <FeedRow key={it.code} item={it} onStock={(s) => navigate(`/stock/${s}`)} />
            ))}
          </div>
          <button
            onClick={loadMore}
            disabled={loadingMore}
            className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-control border py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
          >
            {loadingMore ? <Loader2 className="size-3.5 animate-spin" /> : <ChevronDown className="size-3.5" />}
            加载更早的快讯
          </button>
        </>
      )}
    </section>
  )
}

/** 右侧：个股新闻 + AI 情绪诊断 */
function StockColumn() {
  const navigate = useNavigate()
  const [picked, setPicked] = useState<{ symbol: string; name: string } | null>(null)
  const [keyword, setKeyword] = useState('')

  const watchlist = useQuery({ queryKey: ['watchlist'], queryFn: fetchWatchlist })

  // 搜索（输入 2 个字符起查）
  const search = useQuery({
    queryKey: ['stock-search', keyword],
    queryFn: () => searchStocks(keyword),
    enabled: keyword.trim().length >= 2,
    staleTime: 30_000,
  })

  // 当前展示的股票：用户选过的优先，否则默认自选第一只（纯派生，无需 effect）
  const first = watchlist.data?.[0]
  const selected = picked ?? (first ? { symbol: first.symbol, name: first.name } : null)
  const setSelected = setPicked

  const news = useQuery({
    queryKey: ['stock-news', selected?.symbol],
    queryFn: () => fetchStockNews(selected!.symbol),
    enabled: Boolean(selected),
    staleTime: 600_000, // 与后端 10 分钟缓存对齐
  })

  return (
    <div className="space-y-4">
      {/* 选股区 */}
      <section className="rounded-card border bg-card p-4">
        <h3 className="mb-2 text-[13px] font-medium text-muted-foreground">个股消息面</h3>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-text-muted" />
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜代码 / 名称 / 拼音…"
            name="news-stock-search"
            className="w-full rounded-control border bg-transparent py-1.5 pl-8 pr-2 text-xs outline-none transition-colors focus:border-gold/50"
          />
          {search.data && search.data.length > 0 && keyword.trim().length >= 2 && (
            <div className="absolute inset-x-0 top-full z-10 mt-1 max-h-56 overflow-y-auto rounded-control border bg-popover p-1 shadow-lg">
              {search.data.slice(0, 8).map((r) => (
                <button
                  key={r.symbol}
                  onClick={() => {
                    setSelected({ symbol: r.symbol, name: r.name })
                    setKeyword('')
                  }}
                  className="flex w-full items-center justify-between rounded px-2 py-1.5 text-xs hover:bg-muted"
                >
                  <span>{r.name}</span>
                  <span className="font-data text-text-muted">{r.symbol}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* 自选快速切换 */}
        {watchlist.data && watchlist.data.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {watchlist.data.slice(0, 10).map((w) => (
              <button
                key={w.symbol}
                onClick={() => setSelected({ symbol: w.symbol, name: w.name })}
                className={cn(
                  'rounded-control border px-2 py-1 text-[11px] transition-colors',
                  selected?.symbol === w.symbol
                    ? 'border-gold/50 bg-gold/10 text-gold'
                    : 'border-border text-muted-foreground hover:text-foreground',
                )}
              >
                {w.name}
              </button>
            ))}
          </div>
        )}
      </section>

      {selected && (
        <>
          {/* AI 情绪诊断 */}
          <section className="rounded-card border bg-card p-4">
            <SentimentPanel symbol={selected.symbol} name={selected.name} />
          </section>

          {/* 个股新闻列表 */}
          <section className="rounded-card border bg-card p-4">
            <header className="mb-2 flex items-center justify-between">
              <h3 className="text-[13px] font-medium text-muted-foreground">
                {selected.name} · 近期新闻
              </h3>
              <button
                onClick={() => navigate(`/stock/${selected.symbol}`)}
                className="text-[11px] text-gold hover:underline"
              >
                查看个股页 →
              </button>
            </header>
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
              <div className="max-h-[40vh] space-y-2.5 overflow-y-auto pr-1">
                {(news.data ?? []).map((n) => (
                  <a
                    key={n.code}
                    href={n.url}
                    target="_blank"
                    rel="noreferrer"
                    className="group block rounded-inner border border-transparent p-2 transition-colors hover:border-border hover:bg-muted/30"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <h4 className="text-xs font-medium leading-snug group-hover:text-gold">
                        {n.title}
                      </h4>
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
        </>
      )}
    </div>
  )
}

/**
 * 消息中心（M5）：左侧 7×24 快讯流 + 右侧个股新闻与 AI 情绪诊断。
 */
export function NewsPage() {
  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-lg font-semibold">消息中心</h2>
        <p className="mt-0.5 text-xs text-text-muted">
          全球财经快讯实时滚动 · 个股新闻聚合 · AI 阅读新闻打情绪分
        </p>
      </header>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <FeedColumn />
        </div>
        <StockColumn />
      </div>
    </div>
  )
}
