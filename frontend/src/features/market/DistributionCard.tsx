import type { MarketOverview } from '@/lib/api'
import { baseTooltip, chartColors, useECharts } from '@/lib/useECharts'

/**
 * 涨跌分布卡：11 桶柱状图（跌停→涨停），颜色按红涨绿跌渐进。
 * "市场温度"一眼可见：右侧柱子高=普涨，左侧高=普跌。
 */
export function DistributionCard({ overview }: { overview: MarketOverview }) {
  const ref = useECharts(() => {
    const c = chartColors()
    const buckets = overview.buckets
    // 前 5 桶是下跌（绿），中间平盘（灰），后 5 桶上涨（红）
    const colorOf = (i: number) => (i < 5 ? c.down : i === 5 ? c.flat : c.up)
    return {
      tooltip: { ...baseTooltip(), trigger: 'axis' },
      grid: { left: 8, right: 8, top: 24, bottom: 4, containLabel: true },
      xAxis: {
        type: 'category',
        data: buckets.map((b) => b.label),
        axisLine: { lineStyle: { color: c.border } },
        axisTick: { show: false },
        axisLabel: { color: c.textMuted, fontSize: 9, interval: 0, rotate: 38 },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: c.border } },
        axisLabel: { color: c.textMuted, fontSize: 10 },
      },
      series: [
        {
          type: 'bar',
          data: buckets.map((b, i) => ({
            value: b.count,
            itemStyle: { color: colorOf(i), borderRadius: [3, 3, 0, 0] },
          })),
          barWidth: '62%',
          label: {
            show: true,
            position: 'top',
            color: c.textMuted,
            fontSize: 9,
            formatter: ({ value }: { value: number }) => (value > 0 ? String(value) : ''),
          },
        },
      ],
    }
  }, [overview])

  return <div ref={ref} className="h-full min-h-44 w-full" />
}
