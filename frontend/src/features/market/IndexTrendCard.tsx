import { useQuery } from '@tanstack/react-query'

import { fetchIndexKline } from '@/lib/api'
import { baseTooltip, chartColors, useECharts } from '@/lib/useECharts'

/**
 * 上证指数走势卡：近 120 个交易日收盘面积线 + 成交额柱（双轴）。
 * 仪表盘的"主视觉"卡片——大盘趋势与量能配合一眼看清。
 */
export function IndexTrendCard() {
  const { data } = useQuery({
    queryKey: ['index-kline', '000001'],
    queryFn: () => fetchIndexKline('000001', 120),
    staleTime: 5 * 60_000,
  })

  const ref = useECharts(() => {
    const c = chartColors()
    const bars = data ?? []
    return {
      tooltip: { ...baseTooltip(), trigger: 'axis' },
      grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
      xAxis: {
        type: 'category',
        data: bars.map((b) => b.date),
        axisLine: { lineStyle: { color: c.border } },
        axisTick: { show: false },
        axisLabel: { color: c.textMuted, fontSize: 10 },
      },
      yAxis: [
        {
          type: 'value',
          scale: true,
          splitLine: { lineStyle: { color: c.border } },
          axisLabel: { color: c.textMuted, fontSize: 10 },
        },
        { type: 'value', show: false }, // 成交额轴隐藏，只看相对量
      ],
      series: [
        {
          name: '收盘',
          type: 'line',
          data: bars.map((b) => b.close),
          showSymbol: false,
          lineStyle: { color: c.gold, width: 1.6 },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: `${c.gold}44` },
                { offset: 1, color: `${c.gold}00` },
              ],
            },
          },
        },
        {
          name: '成交额',
          type: 'bar',
          yAxisIndex: 1,
          data: bars.map((b, i) => ({
            value: b.amount,
            itemStyle: {
              color: i > 0 && b.close >= bars[i - 1].close ? `${c.up}55` : `${c.down}55`,
            },
          })),
          barWidth: '55%',
        },
      ],
    }
  }, [data])

  return <div ref={ref} className="h-full min-h-56 w-full" />
}
