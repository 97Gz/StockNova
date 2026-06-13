import type { MarketOverview } from '@/lib/api'
import { baseTooltip, chartColors, useECharts } from '@/lib/useECharts'

/**
 * 两市成交额趋势（近 30 个交易日，单位亿元）。
 * 量能是情绪的体温计：持续放量 = 增量资金进场。
 */
export function AmountTrendCard({ overview }: { overview: MarketOverview }) {
  const ref = useECharts(() => {
    const c = chartColors()
    const trend = overview.amount_trend
    return {
      tooltip: {
        ...baseTooltip(),
        trigger: 'axis',
        formatter: (params: { name: string; value: number }[]) =>
          `${params[0].name}<br/>成交 ${(params[0].value / 1e4).toFixed(2)} 万亿`,
      },
      grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
      xAxis: {
        type: 'category',
        data: trend.map((t) => t.date.slice(5)),
        axisLine: { lineStyle: { color: c.border } },
        axisTick: { show: false },
        axisLabel: { color: c.textMuted, fontSize: 9 },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: c.border } },
        axisLabel: {
          color: c.textMuted,
          fontSize: 9,
          formatter: (v: number) => `${(v / 1e4).toFixed(1)}万亿`,
        },
      },
      series: [
        {
          type: 'bar',
          data: trend.map((t) => t.amount_yi),
          barWidth: '60%',
          itemStyle: { color: `${c.gold}99`, borderRadius: [2, 2, 0, 0] },
        },
      ],
    }
  }, [overview])

  return <div ref={ref} className="h-full min-h-36 w-full" />
}
