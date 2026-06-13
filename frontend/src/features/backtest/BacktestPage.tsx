import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, History, Loader2, Play, RefreshCcw, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { RebalanceResultView } from '@/features/backtest/RebalanceResultView'
import { SnapshotResultView } from '@/features/backtest/SnapshotResultView'
import { StrategyPicker } from '@/features/backtest/StrategyPicker'
import type { RebalanceResult, SnapshotResult } from '@/lib/api'
import {
  ApiError,
  deleteBacktestRun,
  fetchBacktestRun,
  fetchBacktestRuns,
  runRebalanceBacktest,
  runSnapshotBacktest,
} from '@/lib/api'
import { cn } from '@/lib/utils'

type Mode = 'snapshot' | 'rebalance'

const HOLD_CHOICES = [5, 10, 20, 30, 60]
const FREQ_CHOICES = [
  { v: 3, label: '每 3 日' },
  { v: 5, label: '每周（5日）' },
  { v: 10, label: '每两周（10日）' },
  { v: 20, label: '每月（20日）' },
]
const TOP_CHOICES = [5, 10, 20]

/**
 * 快速开始预设：把"我想验证什么"翻译成现成参数组合，
 * 新手不用理解每个参数也能跑出有意义的推演（点击即填参，可再微调）。
 */
const PRESETS: {
  id: string
  label: string
  desc: string
  apply: {
    mode: Mode
    strategyIds: string[]
    signalDaysAgo?: number
    holdDays?: number[]
    rangeDaysAgo?: number
    freqDays?: number
    topN?: number
  }
}[] = [
  {
    id: 'last_month_trend',
    label: '上月趋势启动买入',
    desc: '一个月前按「趋势启动」买入，持有 5/10/20 天各是什么结果',
    apply: { mode: 'snapshot', strategyIds: ['trend_start'], signalDaysAgo: 30, holdDays: [5, 10, 20] },
  },
  {
    id: 'gold_cross_machine',
    label: '金叉信号验证',
    desc: '45 天前按「MACD水上金叉」买入的真实战绩',
    apply: {
      mode: 'snapshot',
      strategyIds: ['macd_water_gold'],
      signalDaysAgo: 45,
      holdDays: [5, 10, 20],
    },
  },
  {
    id: 'half_year_weekly',
    label: '半年每周轮动',
    desc: '近半年每周按策略换一批（10只），看长期资金曲线能否跑赢沪深300',
    apply: { mode: 'rebalance', strategyIds: ['trend_start'], rangeDaysAgo: 183, freqDays: 5, topN: 10 },
  },
  {
    id: 'quarter_fast_rotate',
    label: '三个月短线轮动',
    desc: '近三个月每 3 日快速轮动（5只），检验短线策略的换手代价',
    apply: { mode: 'rebalance', strategyIds: ['volume_breakout'], rangeDaysAgo: 92, freqDays: 3, topN: 5 },
  },
]

/** 距今 N 天前的 yyyy-MM-dd（默认参数用，不必是交易日，后端会校验） */
function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

const inputCls =
  'h-8 rounded-control border border-border bg-background px-2.5 font-data text-xs ' +
  'outline-none transition-colors focus:border-gold/50'

/**
 * 历史推演页（M4）：
 * - 策略时光机：假如在历史某天按策略买入，持有 5/10/20 天各是什么结果
 * - 定期调仓：每隔 N 个交易日机械换仓，资金曲线对比沪深300
 * 共用策略多选；结果页所有指标白话化。
 */
export function BacktestPage() {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<Mode>('snapshot')
  const [strategyIds, setStrategyIds] = useState<string[]>(['trend_start'])
  const [requireAll, setRequireAll] = useState(false)

  // 时光机参数
  const [signalDate, setSignalDate] = useState(daysAgo(45))
  const [holdDays, setHoldDays] = useState<number[]>([5, 10, 20])

  // 调仓参数
  const [start, setStart] = useState(daysAgo(183))
  const [end, setEnd] = useState(daysAgo(1))
  const [freqDays, setFreqDays] = useState(5)
  const [topN, setTopN] = useState(10)
  const [initCashWan, setInitCashWan] = useState(10)

  const [snapshotResult, setSnapshotResult] = useState<SnapshotResult | null>(null)
  const [rebalanceResult, setRebalanceResult] = useState<RebalanceResult | null>(null)

  const runs = useQuery({ queryKey: ['backtest-runs'], queryFn: fetchBacktestRuns })

  const snapshot = useMutation({
    mutationFn: runSnapshotBacktest,
    onSuccess: (data) => {
      setSnapshotResult(data)
      queryClient.invalidateQueries({ queryKey: ['backtest-runs'] })
    },
  })
  const rebalance = useMutation({
    mutationFn: runRebalanceBacktest,
    onSuccess: (data) => {
      setRebalanceResult(data)
      queryClient.invalidateQueries({ queryKey: ['backtest-runs'] })
    },
  })
  const loadRun = useMutation({
    mutationFn: fetchBacktestRun,
    onSuccess: (row) => {
      if (row.kind === 'snapshot') {
        setMode('snapshot')
        setSnapshotResult(row.result as SnapshotResult)
      } else {
        setMode('rebalance')
        setRebalanceResult(row.result as RebalanceResult)
      }
    },
  })
  const removeRun = useMutation({
    mutationFn: deleteBacktestRun,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backtest-runs'] }),
  })

  const running = snapshot.isPending || rebalance.isPending
  const activeError = mode === 'snapshot' ? snapshot.error : rebalance.error

  function toggleHold(n: number) {
    setHoldDays((prev) =>
      prev.includes(n) ? prev.filter((x) => x !== n) : [...prev, n].sort((a, b) => a - b),
    )
  }

  /** 套用快速预设：填参不执行（留给用户确认/微调后自己点开始） */
  function applyPreset(p: (typeof PRESETS)[number]) {
    const a = p.apply
    setMode(a.mode)
    setStrategyIds(a.strategyIds)
    setRequireAll(false)
    if (a.mode === 'snapshot') {
      if (a.signalDaysAgo) setSignalDate(daysAgo(a.signalDaysAgo))
      if (a.holdDays) setHoldDays(a.holdDays)
    } else {
      if (a.rangeDaysAgo) {
        setStart(daysAgo(a.rangeDaysAgo))
        setEnd(daysAgo(1))
      }
      if (a.freqDays) setFreqDays(a.freqDays)
      if (a.topN) setTopN(a.topN)
    }
  }

  function run() {
    if (mode === 'snapshot') {
      snapshot.mutate({
        strategy_ids: strategyIds,
        require_all: requireAll,
        signal_date: signalDate,
        hold_days: holdDays,
      })
    } else {
      rebalance.mutate({
        strategy_ids: strategyIds,
        require_all: requireAll,
        start,
        end,
        freq_days: freqDays,
        top_n: topN,
        init_cash: initCashWan * 10000,
      })
    }
  }

  const canRun =
    strategyIds.length > 0 && !running && (mode === 'snapshot' ? holdDays.length > 0 : true)

  return (
    <div className="space-y-4">
      {/* 模式切换 */}
      <div className="flex items-center gap-2">
        {(
          [
            { id: 'snapshot', icon: CalendarClock, label: '策略时光机' },
            { id: 'rebalance', icon: RefreshCcw, label: '定期调仓' },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            type="button"
            className={cn(
              'flex items-center gap-1.5 rounded-control border px-3.5 py-2 text-[13px] transition-colors',
              mode === t.id
                ? 'border-gold/50 bg-gold/10 text-gold'
                : 'border-border text-muted-foreground hover:border-gold/30',
            )}
            onClick={() => setMode(t.id)}
          >
            <t.icon className="size-4" />
            {t.label}
          </button>
        ))}
        <p className="ml-2 hidden text-[11px] text-text-muted md:block">
          {mode === 'snapshot'
            ? '「假如那天我按策略买了」——固定某天买入，看持有 N 天的真实结果'
            : '「机械执行会怎样」——每隔几天按策略换一批股票，看长期资金曲线'}
        </p>
      </div>

      {/* 快速开始：场景化预设，一键填参 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-text-muted">快速开始</span>
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            title={p.desc}
            className="rounded-control border border-dashed px-2.5 py-1.5 text-[11px] text-muted-foreground transition-colors hover:border-gold/40 hover:text-gold"
            onClick={() => applyPreset(p)}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* 参数面板 */}
      <section className="rounded-card border bg-card p-4">
        <div className="space-y-3.5">
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-xs font-medium">参与策略（可多选共振）</p>
              <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-text-muted">
                <input
                  type="checkbox"
                  name="bt-require-all"
                  className="size-3 accent-gold"
                  checked={requireAll}
                  onChange={(e) => setRequireAll(e.target.checked)}
                />
                必须全部命中（AND）
              </label>
            </div>
            <StrategyPicker selected={strategyIds} onChange={setStrategyIds} />
          </div>

          {mode === 'snapshot' ? (
            <div className="flex flex-wrap items-end gap-4">
              <label className="space-y-1">
                <span className="block text-[11px] text-text-muted">信号日（收盘选股）</span>
                <input
                  type="date"
                  name="signal-date"
                  className={inputCls}
                  value={signalDate}
                  max={daysAgo(1)}
                  onChange={(e) => setSignalDate(e.target.value)}
                />
              </label>
              <div className="space-y-1">
                <span className="block text-[11px] text-text-muted">持有期（交易日，可多选）</span>
                <div className="flex gap-1.5">
                  {HOLD_CHOICES.map((n) => (
                    <button
                      key={n}
                      type="button"
                      className={cn(
                        'rounded-control border px-2.5 py-1.5 font-data text-xs transition-colors',
                        holdDays.includes(n)
                          ? 'border-gold/60 bg-gold/12 text-gold'
                          : 'border-border text-muted-foreground hover:border-gold/30',
                      )}
                      onClick={() => toggleHold(n)}
                    >
                      {n}日
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-end gap-4">
              <label className="space-y-1">
                <span className="block text-[11px] text-text-muted">开始日期</span>
                <input
                  type="date"
                  name="bt-start"
                  className={inputCls}
                  value={start}
                  max={end}
                  onChange={(e) => setStart(e.target.value)}
                />
              </label>
              <label className="space-y-1">
                <span className="block text-[11px] text-text-muted">结束日期</span>
                <input
                  type="date"
                  name="bt-end"
                  className={inputCls}
                  value={end}
                  min={start}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </label>
              <label className="space-y-1">
                <span className="block text-[11px] text-text-muted">调仓频率</span>
                <select
                  name="bt-freq"
                  className={inputCls}
                  value={freqDays}
                  onChange={(e) => setFreqDays(Number(e.target.value))}
                >
                  {FREQ_CHOICES.map((f) => (
                    <option key={f.v} value={f.v}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1">
                <span className="block text-[11px] text-text-muted">每期持股数</span>
                <select
                  name="bt-topn"
                  className={inputCls}
                  value={topN}
                  onChange={(e) => setTopN(Number(e.target.value))}
                >
                  {TOP_CHOICES.map((n) => (
                    <option key={n} value={n}>
                      {n} 只
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1">
                <span className="block text-[11px] text-text-muted">初始本金（万元）</span>
                <input
                  type="number"
                  name="bt-cash"
                  className={cn(inputCls, 'w-24')}
                  min={1}
                  max={10000}
                  value={initCashWan}
                  onChange={(e) => setInitCashWan(Number(e.target.value) || 10)}
                />
              </label>
            </div>
          )}

          <div className="flex items-center gap-3 border-t border-border/60 pt-3">
            <button
              type="button"
              disabled={!canRun}
              className={cn(
                'flex items-center gap-1.5 rounded-control bg-gold px-4 py-2 text-[13px] font-medium text-black/85',
                'transition-opacity hover:opacity-90 disabled:opacity-40',
              )}
              onClick={run}
            >
              {running ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
              {running ? '推演中（最长约 1 分钟）…' : '开始推演'}
            </button>
            {strategyIds.length === 0 && (
              <p className="text-[11px] text-text-muted">请先选择至少一个策略</p>
            )}
            {activeError != null && (
              <p className="text-[11px] text-up">
                {activeError instanceof ApiError ? activeError.message : '回测失败，请稍后重试'}
              </p>
            )}
          </div>
        </div>
      </section>

      {/* 结果区 */}
      {mode === 'snapshot' && snapshotResult && <SnapshotResultView result={snapshotResult} />}
      {mode === 'rebalance' && rebalanceResult && <RebalanceResultView result={rebalanceResult} />}
      {((mode === 'snapshot' && !snapshotResult) || (mode === 'rebalance' && !rebalanceResult)) &&
        !running && (
          <div className="rounded-card border border-dashed bg-card/40 px-4 py-14 text-center">
            <History className="mx-auto mb-2 size-6 text-text-muted/40" />
            <p className="text-xs leading-relaxed text-text-muted">
              选好策略和参数后点「开始推演」
              <br />
              回测会还原真实交易约束：次日开盘成交、一字板买不进、跌停卖不出、扣手续费
            </p>
          </div>
        )}

      {/* 历史记录 */}
      {(runs.data?.length ?? 0) > 0 && (
        <section className="overflow-hidden rounded-card border bg-card">
          <div className="border-b px-4 py-2.5 text-xs text-text-muted">
            历史推演记录（点击重新查看）
          </div>
          <div className="max-h-56 divide-y divide-border/60 overflow-y-auto">
            {runs.data?.map((r) => (
              <div
                key={r.id}
                className="flex cursor-pointer items-center justify-between px-4 py-2 transition-colors hover:bg-accent/40"
                onClick={() => loadRun.mutate(r.id)}
              >
                <div className="flex items-center gap-2.5 text-xs">
                  <span
                    className={cn(
                      'rounded-full px-2 py-0.5 text-[10px]',
                      r.kind === 'snapshot' ? 'bg-gold/10 text-gold' : 'bg-accent text-muted-foreground',
                    )}
                  >
                    {r.kind === 'snapshot' ? '时光机' : '定期调仓'}
                  </span>
                  <span className="font-data text-[11px] text-text-muted">
                    {r.kind === 'snapshot' ? r.signal_date : r.range}
                  </span>
                  {r.total_return_pct != null && (
                    <span
                      className={cn(
                        'font-data text-[11px]',
                        r.total_return_pct > 0 ? 'text-up' : r.total_return_pct < 0 ? 'text-down' : 'text-flat',
                      )}
                    >
                      {r.total_return_pct > 0 ? '+' : ''}
                      {r.total_return_pct}%
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-data text-[10px] text-text-muted">{r.created_at}</span>
                  <button
                    type="button"
                    title="删除该记录"
                    className="text-text-muted/60 transition-colors hover:text-up"
                    onClick={(e) => {
                      e.stopPropagation()
                      removeRun.mutate(r.id)
                    }}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
