import { useMutation } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Star } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import type { ScanItem } from '@/lib/api'
import { addWatchlist, ApiError } from '@/lib/api'
import { formatPct, formatPrice, pctColor } from '@/lib/format'
import { cn } from '@/lib/utils'

/** 共振评分徽章：分越高颜色越金（命中所选策略的比例） */
function ScoreBadge({ score }: { score: number }) {
  const cls =
    score >= 100
      ? 'bg-gold/15 text-gold border-gold/40'
      : score >= 50
        ? 'bg-gold/8 text-gold/80 border-gold/20'
        : 'bg-muted text-muted-foreground border-transparent'
  return (
    <span className={cn('inline-block rounded-full border px-2 py-0.5 font-data text-[11px]', cls)}>
      {score}
    </span>
  )
}

/** 单行：基础行情 + 命中策略标签；点开展示每个策略的人话化理由 */
function ResultRow({ item }: { item: ScanItem }) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [added, setAdded] = useState(false)
  const [error, setError] = useState('')

  const add = useMutation({
    mutationFn: addWatchlist,
    onSuccess: () => setAdded(true),
    onError: (e) => {
      // 已在自选里也算"已加"，其他错误展示文案
      if (e instanceof ApiError && e.message.includes('已在')) setAdded(true)
      else setError(e instanceof ApiError ? e.message : '添加失败')
    },
  })

  return (
    <>
      <tr
        className="cursor-pointer transition-colors hover:bg-accent/40"
        onClick={() => setOpen((v) => !v)}
      >
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[13px]">{item.name}</span>
            <span className="font-data text-[10px] text-text-muted">{item.symbol}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {item.hits.map((h) => (
              <span
                key={h.strategy_id}
                className="rounded-full bg-gold/8 px-1.5 py-px text-[10px] text-gold/90"
              >
                {h.name}
              </span>
            ))}
          </div>
        </td>
        <td className={cn('px-3 py-2.5 text-right font-data', pctColor(item.pct_change ?? 0))}>
          {item.close != null ? formatPrice(item.close) : '--'}
        </td>
        <td className={cn('px-3 py-2.5 text-right font-data', pctColor(item.pct_change ?? 0))}>
          {item.pct_change != null ? formatPct(item.pct_change) : '--'}
        </td>
        <td className="hidden px-3 py-2.5 text-right font-data text-xs text-muted-foreground md:table-cell">
          {item.turnover != null ? `${item.turnover.toFixed(2)}%` : '--'}
        </td>
        <td className="hidden px-3 py-2.5 text-right font-data text-xs text-muted-foreground md:table-cell">
          {item.vol_ratio != null ? item.vol_ratio.toFixed(2) : '--'}
        </td>
        <td className="px-3 py-2.5 text-center">
          <ScoreBadge score={item.score} />
        </td>
        <td className="px-3 py-2.5">
          <div className="flex items-center justify-end gap-1">
            <button
              title={added ? '已在自选' : '加自选'}
              className={cn(
                'rounded-control p-1.5 transition-colors',
                added ? 'text-gold' : 'text-text-muted hover:bg-gold/10 hover:text-gold',
              )}
              onClick={(e) => {
                e.stopPropagation()
                if (!added) add.mutate(item.symbol)
              }}
            >
              <Star className={cn('size-3.5', added && 'fill-gold')} />
            </button>
            <button
              className="rounded-control p-1.5 text-text-muted transition-colors hover:bg-accent"
              onClick={(e) => {
                e.stopPropagation()
                setOpen((v) => !v)
              }}
            >
              {open ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
            </button>
          </div>
          {error && <p className="mt-1 text-right text-[10px] text-up">{error}</p>}
        </td>
      </tr>
      {open && (
        <tr className="bg-background/50">
          <td colSpan={7} className="px-6 py-3">
            <div className="space-y-2.5">
              {item.hits.map((h) => (
                <div key={h.strategy_id}>
                  <div className="mb-1 text-[11px] font-medium text-gold/90">「{h.name}」命中理由</div>
                  <ul className="space-y-0.5">
                    {h.reasons.map((r, i) => (
                      <li key={i} className="text-[12px] leading-relaxed text-muted-foreground">
                        {r}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              <button
                className="rounded-control border border-gold/30 px-2.5 py-1 text-[11px] text-gold transition-colors hover:bg-gold/10"
                onClick={() => navigate(`/stock/${item.symbol}`)}
              >
                查看个股 K 线 →
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/** 扫描结果表：评分（共振）降序，由后端排序保证 */
export function ScanResultTable({ items }: { items: ScanItem[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b text-left text-[11px] text-text-muted">
          <th className="px-4 py-2.5 font-medium">名称 / 命中策略</th>
          <th className="px-3 py-2.5 text-right font-medium">收盘价</th>
          <th className="px-3 py-2.5 text-right font-medium">涨跌幅</th>
          <th className="hidden px-3 py-2.5 text-right font-medium md:table-cell">换手</th>
          <th className="hidden px-3 py-2.5 text-right font-medium md:table-cell">量比</th>
          <th className="px-3 py-2.5 text-center font-medium">共振分</th>
          <th className="px-3 py-2.5 text-right font-medium">操作</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border/60">
        {items.map((item) => (
          <ResultRow key={item.symbol} item={item} />
        ))}
      </tbody>
    </table>
  )
}
