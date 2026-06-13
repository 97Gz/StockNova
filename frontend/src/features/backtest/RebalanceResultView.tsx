import { ChevronDown, Info } from 'lucide-react'
import { useState } from 'react'

import type { RebalanceResult } from '@/lib/api'
import { formatPct, pctColor } from '@/lib/format'
import { baseTooltip, chartColors, useECharts } from '@/lib/useECharts'
import { cn } from '@/lib/utils'

/** 指标卡：数值 + 大白话解释 */
function MetricCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: string
  hint: string
  tone?: string
}) {
  return (
    <div className="rounded-card border bg-card p-3.5">
      <p className="text-[11px] text-text-muted">{label}</p>
      <p className={cn('mt-1 font-data text-lg', tone)}>{value}</p>
      <p className="mt-1 text-[10px] leading-relaxed text-text-muted">{hint}</p>
    </div>
  )
}

/** 资金曲线（策略 vs 沪深300，同一初始资金归一化） */
function EquityCurve({ result }: { result: RebalanceResult }) {
  const ref = useECharts(() => {
    const c = chartColors()
    return {
      tooltip: {
        ...baseTooltip(),
        trigger: 'axis',
        valueFormatter: (v: unknown) =>
          typeof v === 'number' ? `${(v / 10000).toFixed(2)}万` : '--',
      },
      grid: { left: 56, right: 16, top: 28, bottom: 40 },
      xAxis: {
        type: 'category',
        data: result.curve.map((p) => p.date),
        axisLine: { lineStyle: { color: c.border } },
        axisLabel: { color: c.textMuted, fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: {
          color: c.textMuted,
          fontSize: 10,
          formatter: (v: number) => `${(v / 10000).toFixed(1)}万`,
        },
        splitLine: { lineStyle: { color: c.border, opacity: 0.5 } },
      },
      dataZoom: [{ type: 'inside' }],
      series: [
        {
          name: '策略净值',
          type: 'line',
          data: result.curve.map((p) => p.value),
          showSymbol: false,
          lineStyle: { color: c.gold, width: 2 },
          areaStyle: { color: c.gold, opacity: 0.06 },
        },
        {
          name: '沪深300',
          type: 'line',
          data: result.benchmark_curve.map((p) => p.value),
          showSymbol: false,
          lineStyle: { color: c.textMuted, width: 1.2, type: 'dashed' },
        },
      ],
    }
  }, [result])

  return (
    <div className="rounded-card border bg-card p-4">
      <div className="mb-1 flex items-center justify-between">
        <h4 className="text-[13px] font-semibold">资金曲线</h4>
        <div className="flex items-center gap-3 text-[10px] text-text-muted">
          <span className="flex items-center gap-1">
            <span className="inline-block h-px w-4 bg-gold" /> 策略净值
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-px w-4 border-t border-dashed border-text-muted" />
            沪深300
          </span>
        </div>
      </div>
      <div ref={ref} className="h-72 w-full" />
    </div>
  )
}

/** 换仓记录（折叠列表，逐次展开看买卖明细） */
function TradesList({ trades }: { trades: RebalanceResult['trades'] }) {
  const [open, setOpen] = useState<number | null>(null)
  return (
    <div className="overflow-hidden rounded-card border bg-card">
      <div className="border-b px-4 py-2.5 text-xs text-text-muted">
        换仓记录（{trades.length} 次：信号日收盘选股 → 次日开盘成交）
      </div>
      <div className="max-h-80 divide-y divide-border/60 overflow-y-auto">
        {trades.map((t, i) => (
          <div key={`${t.exec_date}-${i}`}>
            <button
              type="button"
              className="flex w-full items-center justify-between px-4 py-2.5 text-left transition-colors hover:bg-accent/40"
              onClick={() => setOpen(open === i ? null : i)}
            >
              <span className="font-data text-xs">{t.exec_date}</span>
              <span className="flex items-center gap-3 text-[11px] text-text-muted">
                {t.buys.length > 0 && <span className="text-up">买 {t.buys.length}</span>}
                {t.sells.length > 0 && <span className="text-down">卖 {t.sells.length}</span>}
                <span>持仓 {t.holdings_count} 只</span>
                <ChevronDown
                  className={cn('size-3.5 transition-transform', open === i && 'rotate-180')}
                />
              </span>
            </button>
            {open === i && (
              <div className="space-y-1.5 bg-accent/20 px-4 py-2.5 text-[11px]">
                {t.sells.length > 0 && (
                  <p className="leading-relaxed">
                    <span className="text-down">卖出：</span>
                    {t.sells.map((s) => `${s.name}(${s.price})`).join('、')}
                  </p>
                )}
                {t.buys.length > 0 && (
                  <p className="leading-relaxed">
                    <span className="text-up">买入：</span>
                    {t.buys.map((b) => `${b.name} ${b.shares}股(${b.price})`).join('、')}
                  </p>
                )}
                {t.buys.length === 0 && t.sells.length === 0 && (
                  <p className="text-text-muted">本次无成交（停牌/涨跌停限制）</p>
                )}
              </div>
            )}
          </div>
        ))}
        {trades.length === 0 && (
          <p className="px-4 py-8 text-center text-xs text-text-muted">区间内没有发生任何换仓</p>
        )}
      </div>
    </div>
  )
}

/**
 * 定期调仓结果：六宫格白话指标 + 资金曲线 + 换仓记录。
 * 所有指标都配一句"人话"，让没学过金融的用户也能看懂好坏。
 */
export function RebalanceResultView({ result }: { result: RebalanceResult }) {
  const m = result.metrics
  const beat =
    m.benchmark_return_pct != null ? m.total_return_pct - m.benchmark_return_pct : null

  return (
    <div className="space-y-4">
      <p className="text-xs text-text-muted">
        {result.start} ~ {result.end} · 每 {result.freq_days} 个交易日调仓 · 每期买入共振前{' '}
        {result.top_n} 只 · 本金 {(result.init_cash / 10000).toFixed(0)} 万 → 期末{' '}
        <span className={cn('font-data', pctColor(m.total_return_pct))}>
          {(result.final_value / 10000).toFixed(2)} 万
        </span>
      </p>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricCard
          label="总收益"
          value={formatPct(m.total_return_pct)}
          tone={pctColor(m.total_return_pct)}
          hint="整个区间结束后，相对本金赚/亏的比例"
        />
        <MetricCard
          label="年化收益"
          value={formatPct(m.annual_return_pct)}
          tone={pctColor(m.annual_return_pct)}
          hint="折算成「按这个节奏跑一年」的收益口径"
        />
        <MetricCard
          label="最大回撤"
          value={`-${m.max_drawdown_pct.toFixed(2)}%`}
          tone="text-down"
          hint="净值从最高点最多跌了多少——最难受时的亏损深度"
        />
        <MetricCard
          label="夏普比率"
          value={m.sharpe.toFixed(2)}
          hint="每承担一份波动换到多少收益，大于 1 算优秀"
        />
        <MetricCard
          label="周期胜率"
          value={m.period_win_rate != null ? `${m.period_win_rate}%` : '--'}
          hint="赚钱的调仓周期占比，反映策略的稳定性"
        />
        <MetricCard
          label="vs 沪深300"
          value={beat != null ? formatPct(beat) : '--'}
          tone={beat != null ? pctColor(beat) : undefined}
          hint={
            m.benchmark_return_pct != null
              ? `同期大盘 ${formatPct(m.benchmark_return_pct)}，正数=跑赢`
              : '基准数据缺失'
          }
        />
      </div>

      <EquityCurve result={result} />
      <TradesList trades={result.trades} />

      <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-text-muted">
        <Info className="mt-px size-3.5 shrink-0" />
        {result.note}
      </p>
    </div>
  )
}
