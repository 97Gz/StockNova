import { Info } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

import type { SnapshotResult } from '@/lib/api'
import { formatPct, formatPrice, pctColor } from '@/lib/format'
import { cn } from '@/lib/utils'

/** 单个持有期的汇总卡：胜率/平均/最好最差 + 沪深300对照 */
function PeriodCard({
  days,
  s,
  active,
  onClick,
}: {
  days: string
  s: SnapshotResult['summary'][string]
  active: boolean
  onClick: () => void
}) {
  const beatBenchmark =
    s.avg_pct != null && s.benchmark_pct != null ? s.avg_pct > s.benchmark_pct : null
  return (
    <button
      type="button"
      className={cn(
        'flex-1 rounded-card border bg-card p-4 text-left transition-[border-color,box-shadow]',
        active ? 'border-gold/50 shadow-[0_0_0_1px_var(--gold)_inset]' : 'hover:border-gold/25',
      )}
      onClick={onClick}
    >
      <p className="text-[11px] text-text-muted">持有 {days} 个交易日</p>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className={cn('font-data text-xl', s.avg_pct != null && pctColor(s.avg_pct))}>
          {s.avg_pct != null ? formatPct(s.avg_pct) : '--'}
        </span>
        <span className="text-[10px] text-text-muted">平均收益</span>
      </div>
      <div className="mt-2 space-y-1 text-[11px]">
        <div className="flex justify-between">
          <span className="text-text-muted">胜率（赚钱占比）</span>
          <span className="font-data">{s.win_rate != null ? `${s.win_rate}%` : '--'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-muted">中位数</span>
          <span className={cn('font-data', s.median_pct != null && pctColor(s.median_pct))}>
            {s.median_pct != null ? formatPct(s.median_pct) : '--'}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-muted">最好 / 最差</span>
          <span className="font-data">
            {s.best_pct != null ? formatPct(s.best_pct) : '--'}
            <span className="text-text-muted"> / </span>
            {s.worst_pct != null ? formatPct(s.worst_pct) : '--'}
          </span>
        </div>
        <div className="flex justify-between border-t border-border/60 pt-1">
          <span className="text-text-muted">同期沪深300</span>
          <span className="font-data">
            {s.benchmark_pct != null ? formatPct(s.benchmark_pct) : '--'}
            {beatBenchmark != null && (
              <span className={cn('ml-1', beatBenchmark ? 'text-up' : 'text-down')}>
                {beatBenchmark ? '跑赢' : '跑输'}
              </span>
            )}
          </span>
        </div>
      </div>
    </button>
  )
}

/**
 * 策略时光机结果：持有期汇总卡（点击切换明细排序列）+ 个股明细表。
 * 明细按所选持有期的收益降序，让"如果当时买了什么最赚"一目了然。
 */
export function SnapshotResultView({ result }: { result: SnapshotResult }) {
  const navigate = useNavigate()
  const periods = result.hold_days.map(String)
  const [activePeriod, setActivePeriod] = useState(periods[Math.min(1, periods.length - 1)])

  const sorted = useMemo(
    () =>
      [...result.details].sort(
        (a, b) => (b.returns[activePeriod]?.pct ?? -999) - (a.returns[activePeriod]?.pct ?? -999),
      ),
    [result.details, activePeriod],
  )

  return (
    <div className="space-y-4">
      {/* 概览行：命中与剔除统计 */}
      <p className="text-xs text-text-muted">
        {result.signal_date} 共命中 <span className="font-data text-gold">{result.total_hits}</span> 只
        {result.total_hits > result.evaluated + result.skipped_limit_up + result.skipped_suspended && (
          <span>（按共振与流动性取前列）</span>
        )}
        ，实际模拟买入 <span className="font-data text-gold">{result.evaluated}</span> 只
        {result.skipped_limit_up > 0 && (
          <span>
            ，<span className="font-data">{result.skipped_limit_up}</span> 只次日一字板买不进
          </span>
        )}
        {result.skipped_suspended > 0 && (
          <span>
            ，<span className="font-data">{result.skipped_suspended}</span> 只停牌跳过
          </span>
        )}
      </p>

      {/* 持有期汇总卡 */}
      <div className="flex flex-col gap-3 md:flex-row">
        {periods.map((p) => (
          <PeriodCard
            key={p}
            days={p}
            s={result.summary[p]}
            active={activePeriod === p}
            onClick={() => setActivePeriod(p)}
          />
        ))}
      </div>

      {/* 个股明细 */}
      <section className="overflow-hidden rounded-card border bg-card">
        <div className="border-b px-4 py-2.5 text-xs text-text-muted">
          个股明细（按持有 {activePeriod} 日收益排序，点击行查看K线）
        </div>
        <div className="max-h-[480px] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-card">
              <tr className="border-b text-left text-[11px] text-text-muted">
                <th className="w-10 px-4 py-2 font-medium">#</th>
                <th className="px-3 py-2 font-medium">名称 / 代码</th>
                <th className="px-3 py-2 text-right font-medium">信号日收盘</th>
                <th className="px-3 py-2 text-right font-medium">次日买入价</th>
                {periods.map((p) => (
                  <th key={p} className="px-3 py-2 text-right font-medium">
                    持有{p}日
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {sorted.map((d, idx) => (
                <tr
                  key={d.symbol}
                  className="cursor-pointer transition-colors hover:bg-accent/40"
                  onClick={() => navigate(`/stock/${d.symbol}`)}
                >
                  <td className="px-4 py-2 font-data text-xs text-text-muted">{idx + 1}</td>
                  <td className="px-3 py-2">
                    <span className="text-[13px]">{d.name}</span>
                    <span className="ml-2 font-data text-[10px] text-text-muted">{d.symbol}</span>
                  </td>
                  <td className="px-3 py-2 text-right font-data text-xs">
                    {formatPrice(d.signal_close)}
                  </td>
                  <td className="px-3 py-2 text-right font-data text-xs">
                    {formatPrice(d.buy_open)}
                  </td>
                  {periods.map((p) => {
                    const r = d.returns[p]
                    return (
                      <td key={p} className="px-3 py-2 text-right">
                        {r ? (
                          <span className={cn('font-data text-xs', pctColor(r.pct))}>
                            {formatPct(r.pct)}
                            {r.holding && <span className="ml-0.5 text-[9px] text-text-muted">持</span>}
                          </span>
                        ) : (
                          '--'
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={4 + periods.length} className="px-4 py-10 text-center text-xs text-text-muted">
                    该日没有可成交的命中股票（可能全部一字板或停牌）
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-text-muted">
        <Info className="mt-px size-3.5 shrink-0" />
        {result.note}（「持」= 距今交易日不足，按最新收盘价计浮动盈亏）
      </p>
    </div>
  )
}
