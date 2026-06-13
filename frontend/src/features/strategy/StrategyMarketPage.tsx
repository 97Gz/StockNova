import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckSquare, GraduationCap, Hammer, Loader2, Lock, Play, Square, X } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'

import { CustomStrategyBuilder } from '@/features/strategy/CustomStrategyBuilder'
import { riskBadge } from '@/features/strategy/risk'
import { ScanResultTable } from '@/features/strategy/ScanResultTable'
import { StrategyExplainDialog } from '@/features/strategy/StrategyExplainDialog'
import type { ScanResult, StrategyMeta } from '@/lib/api'
import { ApiError, fetchStrategies, runStrategies } from '@/lib/api'
import { cn } from '@/lib/utils'

/** 单个策略卡：勾选参与共振 / 点「讲解」看大白话说明 */
function StrategyCard({
  s,
  checked,
  onToggle,
  onExplain,
}: {
  s: StrategyMeta
  checked: boolean
  onToggle: () => void
  onExplain: () => void
}) {
  const risk = riskBadge(s.risk)
  return (
    <div
      className={cn(
        'group relative flex cursor-pointer flex-col rounded-card border bg-card p-4',
        'transition-[border-color,box-shadow] duration-200',
        checked
          ? 'border-gold/50 shadow-[0_0_0_1px_var(--gold)_inset]'
          : 'hover:border-gold/25',
        !s.available && 'cursor-not-allowed opacity-55',
      )}
      onClick={() => s.available && onToggle()}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5">
            <h4 className="text-[14px] font-semibold">{s.name}</h4>
            {!s.available && <Lock className="size-3 text-text-muted" />}
          </div>
          <p className="mt-0.5 font-data text-[10px] text-text-muted">{s.tech_name}</p>
        </div>
        {s.available ? (
          checked ? (
            <CheckSquare className="size-4.5 shrink-0 text-gold" />
          ) : (
            <Square className="size-4.5 shrink-0 text-text-muted/50 transition-colors group-hover:text-text-muted" />
          )
        ) : (
          <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] text-text-muted">
            即将上线
          </span>
        )}
      </div>

      <p className="mt-2 line-clamp-2 flex-1 text-xs leading-relaxed text-muted-foreground">
        {s.available ? s.summary : s.unavailable_reason}
      </p>

      <div className="mt-3 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className={cn('rounded-full px-1.5 py-px text-[10px]', risk.cls)}>
            风险 {s.risk}
          </span>
          <span className="rounded-full bg-muted px-1.5 py-px text-[10px] text-muted-foreground">
            {s.period}
          </span>
        </div>
        <button
          className="flex items-center gap-1 rounded-control px-1.5 py-1 text-[11px] text-text-muted transition-colors hover:bg-gold/10 hover:text-gold"
          onClick={(e) => {
            e.stopPropagation()
            onExplain()
          }}
        >
          <GraduationCap className="size-3.5" />
          讲解
        </button>
      </div>
    </div>
  )
}

/**
 * 策略广场（M3）：
 * 分类筛选 → 卡片墙多选 → 底部操作栏（共振模式 + 开始选股）→ 结果表。
 * 共振 = 同时命中多个策略的股票得分更高、排得更前。
 */
export function StrategyMarketPage() {
  const strategies = useQuery({ queryKey: ['strategies'], queryFn: fetchStrategies, staleTime: Infinity })
  const [category, setCategory] = useState('全部')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [requireAll, setRequireAll] = useState(false)
  const [explain, setExplain] = useState<StrategyMeta | null>(null)
  const [builderOpen, setBuilderOpen] = useState(false)
  const [result, setResult] = useState<ScanResult | null>(null)
  const [error, setError] = useState('')
  const resultRef = useRef<HTMLDivElement>(null)

  const categories = useMemo(() => {
    const list = strategies.data ?? []
    return ['全部', ...Array.from(new Set(list.map((s) => s.category)))]
  }, [strategies.data])

  const visible = useMemo(() => {
    const list = strategies.data ?? []
    return category === '全部' ? list : list.filter((s) => s.category === category)
  }, [strategies.data, category])

  const scan = useMutation({
    mutationFn: (custom?: Record<string, unknown>) =>
      runStrategies({
        strategy_ids: custom ? [] : Array.from(selected),
        custom_condition: custom,
        require_all: requireAll,
        limit: 100,
      }),
    onSuccess: (data) => {
      setResult(data)
      setError('')
      // 等结果区渲染后平滑滚过去
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : '扫描失败，请稍后重试'),
  })

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const nameOf = (id: string) => strategies.data?.find((s) => s.id === id)?.name ?? id

  return (
    <div className="space-y-4 pb-24">
      {/* 分类 Tab + 自定义条件入口 */}
      <div className="flex flex-wrap items-center gap-2">
        {categories.map((c) => (
          <button
            key={c}
            className={cn(
              'rounded-full border px-3 py-1 text-xs transition-colors',
              category === c
                ? 'border-gold/50 bg-gold/10 text-gold'
                : 'border-border text-muted-foreground hover:border-gold/30',
            )}
            onClick={() => setCategory(c)}
          >
            {c}
          </button>
        ))}
        <button
          className="flex items-center gap-1 rounded-full border border-dashed border-gold/40 px-3 py-1 text-xs text-gold transition-colors hover:bg-gold/10"
          onClick={() => setBuilderOpen(true)}
        >
          <Hammer className="size-3" />
          自定义条件
        </button>
        <p className="ml-auto hidden text-[11px] text-text-muted xl:block">
          勾选多个策略可做「共振筛选」——同时满足多个信号的股票更值得关注
        </p>
      </div>

      {/* 卡片墙 */}
      {strategies.isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-36 animate-pulse rounded-card bg-muted/40" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {visible.map((s) => (
            <StrategyCard
              key={s.id}
              s={s}
              checked={selected.has(s.id)}
              onToggle={() => toggle(s.id)}
              onExplain={() => setExplain(s)}
            />
          ))}
        </div>
      )}

      {/* 扫描结果 */}
      {error && (
        <p className="rounded-inner border border-up/30 bg-up/5 px-3 py-2 text-xs text-up">{error}</p>
      )}
      {result && (
        <section ref={resultRef} className="overflow-hidden rounded-card border bg-card">
          <header className="flex flex-wrap items-center justify-between gap-2 border-b bg-background/40 px-4 py-3">
            <h3 className="text-[13px] font-medium">
              选股结果
              <span className="ml-2 font-data text-gold">{result.total}</span>
              <span className="ml-1 text-[11px] text-text-muted">
                只命中 · 数据日 {result.trade_date} · 按共振分排序
              </span>
            </h3>
            {result.total > result.items.length && (
              <span className="text-[10px] text-text-muted">仅展示前 {result.items.length} 只</span>
            )}
          </header>
          {result.items.length === 0 ? (
            <div className="px-4 py-12 text-center text-xs text-text-muted">
              今天没有股票满足所选条件——空仓等待也是一种策略
            </div>
          ) : (
            <ScanResultTable items={result.items} />
          )}
        </section>
      )}

      {/* 底部浮动操作栏 */}
      {selected.size > 0 && (
        <div className="fixed inset-x-0 bottom-4 z-40 mx-auto w-fit max-w-[92vw]">
          <div className="flex flex-wrap items-center gap-3 rounded-card border border-gold/30 bg-popover/95 px-4 py-3 shadow-2xl backdrop-blur">
            <div className="flex max-w-96 flex-wrap items-center gap-1.5">
              {Array.from(selected).map((id) => (
                <span
                  key={id}
                  className="flex items-center gap-1 rounded-full bg-gold/10 px-2 py-0.5 text-[11px] text-gold"
                >
                  {nameOf(id)}
                  <button onClick={() => toggle(id)}>
                    <X className="size-3" />
                  </button>
                </span>
              ))}
            </div>
            <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
              <input
                type="checkbox"
                checked={requireAll}
                onChange={(e) => setRequireAll(e.target.checked)}
                className="accent-[var(--gold)]"
              />
              必须全部命中（更严苛）
            </label>
            <button
              disabled={scan.isPending}
              className={cn(
                'flex items-center gap-1.5 rounded-control bg-gold px-4 py-2 text-xs font-medium text-black/85',
                'transition-opacity hover:opacity-90 disabled:opacity-50',
              )}
              onClick={() => scan.mutate(undefined)}
            >
              {scan.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Play className="size-3.5" />
              )}
              开始选股（{selected.size} 个策略）
            </button>
          </div>
        </div>
      )}

      {explain && <StrategyExplainDialog strategy={explain} onClose={() => setExplain(null)} />}
      {builderOpen && (
        <CustomStrategyBuilder
          onClose={() => setBuilderOpen(false)}
          onRun={(condition) => scan.mutate(condition)}
        />
      )}
    </div>
  )
}
