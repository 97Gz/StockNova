import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { Maximize2, X } from 'lucide-react'

import { Portal } from '@/components/common/Portal'
import { fetchBoardHeat } from '@/lib/api'
import { formatAmount } from '@/lib/format'
import { baseTooltip, chartColors, useECharts } from '@/lib/useECharts'

interface HeatNode {
  name: string
  value: number
  pct: number
  code: string
}

/** 涨跌幅 → 热力色（红涨绿跌，幅度越大颜色越饱和） */
function heatColor(pct: number, up: string, down: string, flat: string): string {
  if (Math.abs(pct) < 0.05) return flat
  const base = pct > 0 ? up : down
  // 透明度按 |pct| 在 0~5% 区间映射到 35%~100%（超过 5% 全饱和）
  const alpha = Math.min(1, 0.35 + (Math.abs(pct) / 5) * 0.65)
  const hex = Math.round(alpha * 255)
    .toString(16)
    .padStart(2, '0')
  return `${base}${hex}`
}

/** 构造 treemap option（卡片版与全屏版共用，差异只在标签字号与节点数） */
function buildHeatOption(nodes: HeatNode[], large: boolean) {
  const c = chartColors()
  return {
    tooltip: {
      ...baseTooltip(),
      formatter: (info: { data: HeatNode }) =>
        `${info.data.name}<br/>涨跌 ${info.data.pct > 0 ? '+' : ''}${info.data.pct}%` +
        `<br/>成交 ${formatAmount(info.data.value)}<br/><span style="opacity:.6">点击查看成分股 ›</span>`,
    },
    series: [
      {
        type: 'treemap',
        roam: false,
        nodeClick: false, // 自己处理点击 → 跳板块详情
        breadcrumb: { show: false },
        width: '100%',
        height: '100%',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        itemStyle: { borderColor: 'transparent', gapWidth: large ? 3 : 2, borderRadius: 4 },
        label: {
          show: true,
          formatter: (p: { data: HeatNode }) =>
            `${p.data.name}\n${p.data.pct > 0 ? '+' : ''}${p.data.pct}%`,
          fontSize: large ? 13 : 11,
          lineHeight: large ? 18 : 15,
          color: '#fff',
        },
        data: nodes.map((n) => ({
          ...n,
          itemStyle: { color: heatColor(n.pct, c.up, c.down, c.flat) },
        })),
      },
    ],
  }
}

/**
 * 行业热力图（treemap）：面积 = 当日成交额，颜色 = 涨跌方向与强度。
 * 资金都在哪个行业打仗，一张图说清楚。
 *
 * 交互：点击任一板块 → 板块详情页；右上角放大按钮 → 全屏大图（更多板块）。
 */
export function BoardHeatCard() {
  const navigate = useNavigate()
  const [full, setFull] = useState(false)
  const { data } = useQuery({
    queryKey: ['board-heat', 'industry'],
    queryFn: () => fetchBoardHeat('industry', 60),
    staleTime: 5 * 60_000,
  })

  // 卡片内只展示成交额前 16 的板块，避免窄空间里标签糊成一团
  const cardNodes: HeatNode[] = (data ?? []).slice(0, 16).map((b) => ({
    name: b.name,
    value: b.amount,
    pct: b.pct_change,
    code: b.code,
  }))

  const ref = useECharts(() => buildHeatOption(cardNodes, false), [cardNodes], {
    onClick: (p) => {
      const code = (p?.data as HeatNode | undefined)?.code
      if (code) navigate(`/board/${code}`)
    },
  })

  return (
    <div className="flex h-full flex-col">
      <div ref={ref} className="min-h-56 w-full flex-1" />
      <div className="mt-1 flex items-center justify-between text-[10px] text-text-muted">
        <span>面积=成交额 · 颜色=涨跌 · 点击板块看成分股</span>
        <button
          className="flex items-center gap-1 text-gold/80 transition-colors hover:text-gold"
          onClick={() => setFull(true)}
        >
          <Maximize2 className="size-3" /> 放大
        </button>
      </div>

      {full && (
        <BoardHeatFullscreen
          nodes={(data ?? []).map((b) => ({
            name: b.name,
            value: b.amount,
            pct: b.pct_change,
            code: b.code,
          }))}
          onClose={() => setFull(false)}
          onPick={(code) => {
            setFull(false)
            navigate(`/board/${code}`)
          }}
        />
      )}
    </div>
  )
}

/** 全屏热力图：占满视口的大图，板块更多、标签更清晰，点击直达详情。 */
function BoardHeatFullscreen({
  nodes,
  onClose,
  onPick,
}: {
  nodes: HeatNode[]
  onClose: () => void
  onPick: (code: string) => void
}) {
  const ref = useECharts(() => buildHeatOption(nodes, true), [nodes], {
    onClick: (p) => {
      const code = (p?.data as HeatNode | undefined)?.code
      if (code) onPick(code)
    },
  })
  return (
    <Portal>
      <div className="fixed inset-0 z-50 flex flex-col bg-background/95 backdrop-blur-md">
        <header className="flex shrink-0 items-center justify-between border-b bg-card/60 px-5 py-3">
          <div>
            <h2 className="text-sm font-semibold">行业热力图 · 全市场</h2>
            <p className="text-[11px] text-text-muted">
              面积=成交额，颜色=涨跌强度 · 点击任一板块查看成分股
            </p>
          </div>
          <button
            onClick={onClose}
            className="flex size-8 items-center justify-center rounded-control border text-muted-foreground transition-colors hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </header>
        <div ref={ref} className="min-h-0 flex-1 p-4" />
      </div>
    </Portal>
  )
}
