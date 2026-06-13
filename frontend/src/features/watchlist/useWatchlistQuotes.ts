import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import type { FullQuote, WatchlistItem } from '@/lib/api'
import { fetchWatchlist } from '@/lib/api'
import { useWsEvent } from '@/lib/ws'

/**
 * 自选股清单 + 实时报价合并 Hook（自选页与仪表盘快览卡共用）。
 *
 * 数据流：REST 首载清单（含一次性报价快照）→ 盘中 WS quotes 事件
 * 持续覆盖最新价。WS 只更新覆盖层（不动 react-query 缓存），
 * 避免高频推送触发整页重渲染风暴。
 */
export function useWatchlistQuotes() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['watchlist'], queryFn: fetchWatchlist })
  const [liveQuotes, setLiveQuotes] = useState<Record<string, FullQuote>>({})

  useWsEvent(
    'quotes',
    useCallback((event: Record<string, unknown>) => {
      const quotes = event.data as FullQuote[]
      setLiveQuotes((prev) => {
        const next = { ...prev }
        for (const q of quotes) next[q.symbol] = q
        return next
      })
    }, []),
  )

  const items: WatchlistItem[] = (query.data ?? []).map((it) => ({
    ...it,
    quote: liveQuotes[it.symbol] ?? it.quote,
  }))

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['watchlist'] })

  return { items, isLoading: query.isLoading, invalidate }
}
