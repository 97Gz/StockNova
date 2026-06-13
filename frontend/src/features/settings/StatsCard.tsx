import { useQuery } from '@tanstack/react-query'

import { fetchDataStats } from '@/lib/api'

/** 行情库库存统计卡：直观告诉用户"库里现在有什么"。 */
export function StatsCard() {
  const stats = useQuery({
    queryKey: ['data-stats'],
    queryFn: fetchDataStats,
    refetchInterval: 30_000, // 同步进行时统计会持续变化
  })
  const d = stats.data

  const rows: { label: string; value: string }[] = [
    { label: '股票主档', value: fmt(d?.stocks) },
    { label: '有日线的股票', value: fmt(d?.symbols_with_bars) },
    { label: '日线总行数', value: fmt(d?.daily_bars) },
    { label: '指数日线', value: fmt(d?.index_daily) },
    { label: '板块 / 成分', value: d ? `${fmt(d.boards)} / ${fmt(d.board_members)}` : '—' },
    { label: '估值快照', value: fmt(d?.fundamentals_daily) },
    {
      label: '数据覆盖区间',
      value: d?.bar_date_min ? `${d.bar_date_min} ~ ${d.bar_date_max}` : '—',
    },
  ]

  return (
    <section className="col-span-12 rounded-card border bg-card p-5 lg:col-span-5">
      <h3 className="text-sm font-semibold">行情库库存</h3>
      <p className="mt-1 text-xs text-text-muted">DuckDB 列存库 · data/market.duckdb</p>
      <dl className="mt-4 space-y-2.5">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between text-[13px]">
            <dt className="text-muted-foreground">{row.label}</dt>
            <dd className="font-data text-foreground">{row.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function fmt(n: number | undefined): string {
  if (n === undefined) return '—'
  return n.toLocaleString('zh-CN')
}
