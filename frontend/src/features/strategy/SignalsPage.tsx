import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Filter, ListChecks, Loader2, RefreshCw, X } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { ApiError, fetchTodaySignals, runStrategyBatch } from '@/lib/api'
import { formatPrice } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * 选股结果页（M3）：展示每日跑批存档的共振排行。
 * 与策略广场的区别——广场是"现选现扫"的交互式工具，
 * 这里是"每天 15:50 自动跑全部策略"的存档结果，命中越多策略排得越前。
 * 顶部策略筛选器：勾选感兴趣的策略 → 只看这几个策略的组合命中。
 */
export function SignalsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  // 选中的策略 id 集合；空 = 全部策略
  const [picked, setPicked] = useState<string[]>([])

  const signals = useQuery({
    queryKey: ['today-signals', 'page', picked],
    queryFn: () => fetchTodaySignals({ top: 100, strategies: picked }),
  })

  const batch = useMutation({
    mutationFn: runStrategyBatch,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['today-signals'] }),
  })

  const data = signals.data
  const byStrategy = data?.by_strategy ?? []

  const togglePick = (id: string) =>
    setPicked((prev) => (prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-text-muted">
          每个交易日 15:50 自动跑批全部内置策略；命中策略数越多（共振越强）排名越靠前
          {data?.trade_date && (
            <span className="ml-2 font-data text-gold">数据日 {data.trade_date}</span>
          )}
        </p>
        <button
          disabled={batch.isPending}
          className={cn(
            'flex items-center gap-1.5 rounded-control border border-gold/40 px-3 py-1.5 text-xs text-gold',
            'transition-colors hover:bg-gold/10 disabled:opacity-50',
          )}
          onClick={() => batch.mutate()}
        >
          {batch.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="size-3.5" />
          )}
          {batch.isPending ? '跑批中（约 6 秒）…' : '立即重新跑批'}
        </button>
      </div>

      {batch.isError && (
        <p className="rounded-inner border border-up/30 bg-up/5 px-3 py-2 text-xs text-up">
          {batch.error instanceof ApiError ? batch.error.message : '跑批失败，请稍后重试'}
        </p>
      )}
      {batch.isSuccess && (
        <p className="rounded-inner border border-gold/30 bg-gold/5 px-3 py-2 text-xs text-gold">
          跑批完成：{batch.data.strategies} 个策略，共 {batch.data.signals} 条信号（{batch.data.trade_date}）
        </p>
      )}

      {/* 策略筛选器：默认看全部，勾选后只看选中策略的组合命中 */}
      {byStrategy.length > 0 && (
        <section className="rounded-card border bg-card p-4">
          <div className="mb-2.5 flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Filter className="size-3.5" />
              {picked.length === 0
                ? '点击策略只看它们的组合命中（可多选）'
                : `已选 ${picked.length} 个策略——下表只统计这些策略的命中`}
            </span>
            {picked.length > 0 && (
              <button
                className="flex items-center gap-1 rounded-control px-2 py-1 text-[11px] text-text-muted transition-colors hover:bg-accent hover:text-foreground"
                onClick={() => setPicked([])}
              >
                <X className="size-3" /> 清空筛选
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {byStrategy.map((s) => {
              const active = picked.includes(s.id)
              return (
                <button
                  key={s.id}
                  onClick={() => togglePick(s.id)}
                  className={cn(
                    'rounded-full border px-2.5 py-1 text-[11px] transition-colors',
                    active
                      ? 'border-gold/60 bg-gold/15 text-gold'
                      : 'border-border text-muted-foreground hover:border-gold/30 hover:text-foreground',
                  )}
                >
                  {s.name}
                  <span className={cn('ml-1.5 font-data', active ? 'text-gold' : 'text-text-muted')}>
                    {s.count}
                  </span>
                </button>
              )
            })}
          </div>
        </section>
      )}

      <section className="overflow-hidden rounded-card border bg-card">
        {signals.isLoading && (
          <div className="px-4 py-14 text-center text-xs text-text-muted">加载中…</div>
        )}

        {!signals.isLoading && (!data?.trade_date || data.items.length === 0) && (
          <div className="px-4 py-14 text-center">
            <ListChecks className="mx-auto mb-2 size-6 text-text-muted/40" />
            <p className="text-xs text-text-muted">
              {picked.length > 0
                ? '选中的策略今天没有共同命中的股票——试着减少勾选或清空筛选'
                : '还没有跑批记录——点右上角「立即重新跑批」生成今日信号'}
            </p>
          </div>
        )}

        {data && data.items.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-[11px] text-text-muted">
                <th className="w-12 px-4 py-2.5 font-medium">#</th>
                <th className="px-3 py-2.5 font-medium">名称 / 代码</th>
                <th className="px-3 py-2.5 text-right font-medium">信号日收盘</th>
                <th className="px-3 py-2.5 text-center font-medium">命中策略数</th>
                <th className="hidden px-3 py-2.5 font-medium lg:table-cell">命中策略</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {data.items.map((it, idx) => (
                <tr
                  key={it.symbol}
                  className="cursor-pointer transition-colors hover:bg-accent/40"
                  onClick={() => navigate(`/stock/${it.symbol}`)}
                >
                  <td className="px-4 py-2.5 font-data text-xs text-text-muted">{idx + 1}</td>
                  <td className="px-3 py-2.5">
                    <span className="text-[13px]">{it.name}</span>
                    <span className="ml-2 font-data text-[10px] text-text-muted">{it.symbol}</span>
                  </td>
                  <td className="px-3 py-2.5 text-right font-data">
                    {it.close != null ? formatPrice(it.close) : '--'}
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    <span className="inline-block rounded-full border border-gold/40 bg-gold/10 px-2 py-0.5 font-data text-[11px] text-gold">
                      {it.hit_count}
                    </span>
                  </td>
                  <td className="hidden px-3 py-2.5 lg:table-cell">
                    <div className="flex flex-wrap gap-1">
                      {it.strategies.map((name) => (
                        <span
                          key={name}
                          className="rounded-full bg-gold/8 px-1.5 py-px text-[10px] text-gold/90"
                        >
                          {name}
                        </span>
                      ))}
                      {it.hit_count > it.strategies.length && (
                        <span className="text-[10px] text-text-muted">
                          +{it.hit_count - it.strategies.length}
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
