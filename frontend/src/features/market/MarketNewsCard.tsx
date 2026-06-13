import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { ChevronRight } from 'lucide-react'

import { fetchNewsFeed } from '@/lib/api'

/**
 * 市场要闻卡（今日盘面页）：全量快讯前 8 条，60 秒自动刷新。
 *
 * 频道与刷新节奏与消息中心（/news 默认「全部」102）保持一致——
 * 此前用低频的「重点」101 频道导致首页长时间不变，与消息中心脱节。
 * 60s 对齐后端快讯缓存，既新鲜又不打爆数据源。
 */
export function MarketNewsCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['market-news-glance'],
    queryFn: () => fetchNewsFeed('', '102'),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  if (isLoading) return <div className="h-full animate-pulse rounded-inner bg-muted/40" />

  const items = data?.items.slice(0, 8) ?? []
  if (items.length === 0) {
    return (
      <div className="flex h-full min-h-32 items-center justify-center text-xs text-text-muted">
        暂无要闻 · 稍后自动刷新
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <ul className="min-h-0 flex-1 space-y-0.5 overflow-hidden">
        {items.map((n) => (
          <li key={n.code}>
            <a
              href={n.url}
              target="_blank"
              rel="noreferrer"
              className="group flex items-baseline gap-2 rounded-inner px-2 py-1 transition-colors hover:bg-accent/50"
            >
              <span className="shrink-0 font-data text-[10px] text-gold/80">
                {n.publish_time.slice(11, 16)}
              </span>
              <span className="truncate text-[11px] text-muted-foreground transition-colors group-hover:text-foreground">
                {n.title}
              </span>
            </a>
          </li>
        ))}
      </ul>
      <Link
        to="/news"
        className="mt-2 flex w-full items-center justify-center gap-0.5 rounded-inner border border-border py-1.5 text-[11px] text-muted-foreground transition-colors hover:border-gold/30 hover:text-gold"
      >
        进入消息中心 <ChevronRight className="size-3" />
      </Link>
    </div>
  )
}
