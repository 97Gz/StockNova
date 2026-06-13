import { useMutation } from '@tanstack/react-query'
import { Download, Plus, Star, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { useWatchlistQuotes } from '@/features/watchlist/useWatchlistQuotes'
import type { HoldingAi, SearchResult } from '@/lib/api'
import { addWatchlist, ApiError, removeWatchlist, searchStocks } from '@/lib/api'
import { downloadCsv } from '@/lib/csv'
import { formatAmount, formatPct, formatPrice, pctColor } from '@/lib/format'
import { cn } from '@/lib/utils'

/** 评级 → 徽章配色（与诊断/研报库统一） */
const RATING_CLS: Record<string, string> = {
  强烈买入: 'bg-up text-white',
  买入: 'bg-up/80 text-white',
  持有: 'bg-muted text-foreground',
  减仓: 'bg-down/70 text-white',
  卖出: 'bg-down text-white',
}

/** 自选表内联：AI 评级徽章 + 评分（未诊断显示占位） */
function AiBadge({ ai }: { ai: HoldingAi | null | undefined }) {
  if (!ai || !ai.rating) {
    return <span className="text-[10px] text-text-muted/70">未诊断</span>
  }
  return (
    <div className="flex flex-col items-center gap-0.5">
      <span
        className={cn(
          'inline-block rounded-full px-2 py-0.5 text-[10px] font-medium',
          RATING_CLS[ai.rating] ?? 'bg-muted',
        )}
      >
        {ai.rating}
      </span>
      <span className="font-data text-[9px] text-text-muted">{ai.score} 分</span>
    </div>
  )
}

/** 添加自选的内联搜索框（输入 → 下拉候选 → 点击添加） */
function AddBox({ onAdded }: { onAdded: () => void }) {
  const [keyword, setKeyword] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [error, setError] = useState('')

  const search = useMutation({
    mutationFn: searchStocks,
    onSuccess: setResults,
  })
  const add = useMutation({
    mutationFn: addWatchlist,
    onSuccess: () => {
      setKeyword('')
      setResults([])
      setError('')
      onAdded()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : '添加失败'),
  })

  return (
    <div className="relative w-72">
      <div className="flex items-center gap-2 rounded-control border bg-background px-3 py-2">
        <Plus className="size-4 text-text-muted" />
        <input
          value={keyword}
          onChange={(e) => {
            const v = e.target.value
            setKeyword(v)
            setError('')
            if (v.trim()) search.mutate(v.trim())
            else setResults([])
          }}
          placeholder="输入代码 / 拼音添加自选"
          className="w-full bg-transparent text-sm outline-none placeholder:text-text-muted"
        />
      </div>
      {error && <p className="mt-1 text-xs text-up">{error}</p>}
      {results.length > 0 && (
        <ul className="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded-inner border bg-popover shadow-lg">
          {results.map((r) => (
            <li key={r.symbol}>
              <button
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-accent"
                onClick={() => add.mutate(r.symbol)}
              >
                <span>
                  {r.name}
                  <span className="ml-2 font-data text-xs text-text-muted">{r.symbol}</span>
                </span>
                <span className="text-[10px] text-text-muted">{r.market}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * 自选股页（M2）：实时报价表格 + 搜索添加 + 删除。
 * 盘中价格经 WS 5 秒推送自动跳动；点行进个股页。
 */
export function WatchlistPage() {
  const navigate = useNavigate()
  const { items, isLoading, invalidate } = useWatchlistQuotes()
  const remove = useMutation({
    mutationFn: removeWatchlist,
    onSuccess: invalidate,
  })

  // 导出 CSV：当前价 + AI 评级/评分/目标止损
  const exportCsv = () => {
    const header = [
      '代码',
      '名称',
      '最新价',
      '涨跌幅',
      '成交额',
      '换手率',
      'AI评级',
      'AI评分',
      '目标价',
      '止损价',
      '备注',
    ]
    const rows = items.map((it) => [
      it.symbol,
      it.name,
      it.quote?.price ?? '',
      it.quote?.pct_change ?? '',
      it.quote?.amount ?? '',
      it.quote?.turnover ?? '',
      it.ai?.rating || '',
      it.ai?.score ?? '',
      it.ai?.target_price || '',
      it.ai?.stop_loss_price || '',
      it.note ?? '',
    ])
    downloadCsv(`自选股_${new Date().toISOString().slice(0, 10)}.csv`, [header, ...rows])
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-text-muted">
          共 <span className="font-data text-gold">{items.length}</span> 只 · 盘中每 5 秒自动刷新
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={exportCsv}
            disabled={items.length === 0}
            title="导出自选股为 CSV"
            className="flex items-center gap-1.5 rounded-control border px-3 py-2 text-xs text-text-muted transition-colors hover:text-foreground disabled:opacity-40"
          >
            <Download className="size-3.5" /> 导出
          </button>
          <AddBox onAdded={invalidate} />
        </div>
      </div>

      <section className="overflow-hidden rounded-card border bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-[11px] text-text-muted">
              <th className="px-4 py-2.5 font-medium">名称 / 代码</th>
              <th className="px-3 py-2.5 text-right font-medium">最新价</th>
              <th className="px-3 py-2.5 text-right font-medium">涨跌幅</th>
              <th className="hidden px-3 py-2.5 text-right font-medium md:table-cell">最高 / 最低</th>
              <th className="hidden px-3 py-2.5 text-right font-medium lg:table-cell">成交额</th>
              <th className="hidden px-3 py-2.5 text-right font-medium lg:table-cell">换手</th>
              <th className="px-3 py-2.5 text-center font-medium text-gold/90">AI 研判</th>
              <th className="px-3 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {isLoading && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-text-muted">
                  加载中…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-14 text-center">
                  <Star className="mx-auto mb-2 size-6 text-text-muted/40" />
                  <p className="text-xs text-text-muted">用上方「输入代码 / 拼音」搜索框把关注的股票加进来吧</p>
                </td>
              </tr>
            )}
            {items.map((it) => {
              const q = it.quote
              return (
                <tr
                  key={it.symbol}
                  className="cursor-pointer transition-colors hover:bg-accent/40"
                  onClick={() => navigate(`/stock/${it.symbol}`)}
                >
                  <td className="px-4 py-2.5">
                    <div className="text-[13px]">{it.name}</div>
                    <div className="font-data text-[10px] text-text-muted">{it.symbol}</div>
                  </td>
                  <td className={cn('px-3 py-2.5 text-right font-data', pctColor(q?.pct_change ?? 0))}>
                    {q ? formatPrice(q.price) : '--'}
                  </td>
                  <td className={cn('px-3 py-2.5 text-right font-data', pctColor(q?.pct_change ?? 0))}>
                    {q ? formatPct(q.pct_change) : '--'}
                  </td>
                  <td className="hidden px-3 py-2.5 text-right font-data text-xs text-muted-foreground md:table-cell">
                    {q ? `${formatPrice(q.high)} / ${formatPrice(q.low)}` : '--'}
                  </td>
                  <td className="hidden px-3 py-2.5 text-right font-data text-xs text-muted-foreground lg:table-cell">
                    {q ? formatAmount(q.amount) : '--'}
                  </td>
                  <td className="hidden px-3 py-2.5 text-right font-data text-xs text-muted-foreground lg:table-cell">
                    {q ? `${q.turnover.toFixed(2)}%` : '--'}
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    <AiBadge ai={it.ai} />
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <button
                      title="移出自选"
                      className="rounded-control p-1.5 text-text-muted transition-colors hover:bg-up/10 hover:text-up"
                      onClick={(e) => {
                        e.stopPropagation()
                        remove.mutate(it.symbol)
                      }}
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>
    </div>
  )
}
