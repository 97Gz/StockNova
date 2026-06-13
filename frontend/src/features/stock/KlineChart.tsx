import { dispose, init, registerLocale } from 'klinecharts'
import { useEffect, useRef } from 'react'

import type { KlineBar } from '@/lib/api'
import { chartColors } from '@/lib/useECharts'
import { useThemeStore } from '@/stores/theme'

// KLineCharts 中文界面文案
registerLocale('zh-CN-app', {
  time: '时间：',
  open: '开：',
  high: '高：',
  low: '低：',
  close: '收：',
  volume: '量：',
  change: '涨幅：',
  turnover: '成交额：',
  second: '秒',
  minute: '分',
  hour: '时',
  day: '天',
  week: '周',
  month: '月',
  year: '年',
})

/**
 * 专业 K 线图（klinecharts 9）：日K + MA(5/10/20/60) + 成交量副图。
 * 颜色全部取自 CSS 设计令牌（红涨绿跌 + 墨金主题），主题切换自动换肤。
 */
export function KlineChart({ bars }: { bars: KlineBar[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mode = useThemeStore((s) => s.mode)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = init(el, { locale: 'zh-CN-app' })
    if (!chart) return

    const c = chartColors()
    chart.setStyles({
      grid: {
        horizontal: { color: c.border },
        vertical: { show: false },
      },
      candle: {
        bar: {
          upColor: c.up,
          downColor: c.down,
          noChangeColor: c.flat,
          upBorderColor: c.up,
          downBorderColor: c.down,
          noChangeBorderColor: c.flat,
          upWickColor: c.up,
          downWickColor: c.down,
          noChangeWickColor: c.flat,
        },
        priceMark: {
          high: { color: c.textMuted },
          low: { color: c.textMuted },
          last: { upColor: c.up, downColor: c.down, noChangeColor: c.flat },
        },
        tooltip: {
          text: { color: c.foreground },
        },
      },
      indicator: {
        tooltip: { text: { color: c.textMuted } },
        bars: [
          {
            upColor: `${c.up}B3`,
            downColor: `${c.down}B3`,
            noChangeColor: c.flat,
          },
        ],
      },
      xAxis: {
        axisLine: { color: c.border },
        tickText: { color: c.textMuted },
        tickLine: { color: c.border },
      },
      yAxis: {
        axisLine: { color: c.border },
        tickText: { color: c.textMuted },
        tickLine: { color: c.border },
      },
      separator: { color: c.border },
      crosshair: {
        horizontal: {
          line: { color: c.textMuted },
          text: { backgroundColor: c.gold, color: '#1a1206' },
        },
        vertical: {
          line: { color: c.textMuted },
          text: { backgroundColor: c.gold, color: '#1a1206' },
        },
      },
    })

    // 主图均线 + 副图成交量（A 股看盘标配：MA5/10/20/60）
    chart.createIndicator({ name: 'MA', calcParams: [5, 10, 20, 60] }, false, {
      id: 'candle_pane',
    })
    chart.createIndicator('VOL')

    chart.applyNewData(
      bars.map((b) => ({
        timestamp: new Date(`${b.date}T00:00:00+08:00`).getTime(),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
        turnover: b.amount,
      })),
    )

    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(el)
    return () => {
      observer.disconnect()
      dispose(el)
    }
  }, [bars, mode])

  return <div ref={containerRef} className="h-full min-h-[420px] w-full" />
}
