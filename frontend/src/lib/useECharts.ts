/**
 * ECharts 封装 Hook：按需注册组件 + 容器自适应 + 主题色读取。
 *
 * 主题适配方案：图表颜色不写死，从 CSS 变量实时读取（chartColors），
 * 主题切换时组件重建 option 即可换肤，与"墨金终端"令牌保持单一来源。
 */
import { BarChart, CandlestickChart, LineChart, TreemapChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'

import { useThemeStore } from '@/stores/theme'

echarts.use([
  BarChart,
  LineChart,
  TreemapChart,
  CandlestickChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  CanvasRenderer,
])

/** 读取当前主题下的图表用色（CSS 变量是唯一颜色来源） */
export function chartColors() {
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(name).trim()
  return {
    up: v('--up'),
    down: v('--down'),
    flat: v('--flat'),
    gold: v('--gold'),
    text: v('--muted-foreground'),
    textMuted: v('--text-muted'),
    border: v('--border'),
    card: v('--popover'),
    foreground: v('--foreground'),
  }
}

/** 通用 tooltip 样式（深浅主题都跟随 popover 令牌） */
export function baseTooltip() {
  const c = chartColors()
  return {
    backgroundColor: c.card,
    borderColor: c.border,
    textStyle: { color: c.foreground, fontSize: 12 },
    padding: [8, 12] as [number, number],
  }
}

/** ECharts 点击事件回调参数（只暴露常用字段，避免引入全量类型） */
export interface EChartsClickParam {
  data?: unknown
  name?: string
  dataIndex?: number
  seriesIndex?: number
}

/** useECharts 额外选项：目前支持节点点击回调（如热力图点击跳板块详情） */
interface UseEChartsOptions {
  onClick?: (param: EChartsClickParam) => void
}

/**
 * 把 option 渲染到 div。mode 变化时重建（换肤），容器尺寸变化自动 resize。
 * buildOption 用函数而不是对象：保证每次重建时重新读取 CSS 变量。
 * opts.onClick：绑定图表点击事件（用最新闭包，避免过期回调）。
 */
export function useECharts(
  buildOption: () => echarts.EChartsCoreOption,
  deps: unknown[],
  opts: UseEChartsOptions = {},
) {
  const ref = useRef<HTMLDivElement>(null)
  const mode = useThemeStore((s) => s.mode)
  // onClick 存进 ref：事件回调始终读到最新函数，又不必让它进重建依赖
  const onClickRef = useRef(opts.onClick)
  onClickRef.current = opts.onClick

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const chart = echarts.init(el)
    chart.setOption(buildOption())
    chart.on('click', (param) => onClickRef.current?.(param as EChartsClickParam))

    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(el)
    return () => {
      observer.disconnect()
      chart.dispose()
    }
    // mode 进依赖：主题切换重建图表换肤
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, mode])

  return ref
}
