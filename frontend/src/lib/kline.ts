import type { KlineBar } from '@/lib/api'

/**
 * K 线周期聚合：把日线在客户端重采样为周线/月线。
 *
 * 之所以在前端做：后端只落日线，周/月线由日线聚合即可（开=首日开、
 * 收=末日收、高=区间最高、低=区间最低、量额=区间求和），无需新增接口与存储。
 * 入参 bars 需按日期升序（fetchKline 返回即升序）。
 */
export type KlinePeriod = 'day' | 'week' | 'month'

/** 该日所在 ISO 周的周一（yyyy-MM-dd），作为周线分组键 */
function weekKey(dateStr: string): string {
  const d = new Date(`${dateStr}T00:00:00+08:00`)
  const offsetToMonday = (d.getDay() + 6) % 7 // 周一=0 … 周日=6
  d.setDate(d.getDate() - offsetToMonday)
  return d.toISOString().slice(0, 10)
}

export function resampleBars(bars: KlineBar[], period: KlinePeriod): KlineBar[] {
  if (period === 'day' || bars.length === 0) return bars

  const groups = new Map<string, KlineBar[]>()
  for (const b of bars) {
    const key = period === 'week' ? weekKey(b.date) : b.date.slice(0, 7) // 月线按 yyyy-MM
    const g = groups.get(key)
    if (g) g.push(b)
    else groups.set(key, [b])
  }

  const out: KlineBar[] = []
  for (const g of groups.values()) {
    const last = g[g.length - 1]
    out.push({
      date: last.date, // 用区间最后一个交易日代表该周期
      open: g[0].open,
      close: last.close,
      high: Math.max(...g.map((x) => x.high)),
      low: Math.min(...g.map((x) => x.low)),
      volume: g.reduce((s, x) => s + x.volume, 0),
      amount: g.reduce((s, x) => s + x.amount, 0),
      turnover: g.reduce((s, x) => s + x.turnover, 0),
      pct_change: 0, // 聚合后涨跌幅不参与绘图，置 0
    })
  }
  return out
}
