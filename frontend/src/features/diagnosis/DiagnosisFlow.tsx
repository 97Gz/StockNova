import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import {
  Activity,
  AlertTriangle,
  Banknote,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  Compass,
  Crown,
  Database,
  Download,
  Flame,
  Gauge,
  History,
  Landmark,
  Loader2,
  Microscope,
  Network,
  Newspaper,
  Scale,
  ShieldAlert,
  ShieldCheck,
  Sigma,
  Sparkles,
  Swords,
  Target,
  X,
  Zap,
} from 'lucide-react'

import { Portal } from '@/components/common/Portal'
import {
  ApiError,
  diagnosisExportUrl,
  fetchDiagnosisHistory,
  fetchDiagnosisRun,
  fetchLatestDiagnosis,
  startDiagnosis,
  verifyDiagnosis,
  type AnalystReport,
  type ChiefDecision,
  type DebateSpeech,
  type DiagnosisMode,
  type DiagnosisRun,
  type DiagnosisVerify,
  type ResearchConclusion,
  type RiskGate,
  type RiskMemberView,
  type TraderPlan,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useWsEvent } from '@/lib/ws'

/* ============================================================
 * 多角色 AI 投研工作流 —— N8N 风格节点画布（专业版）
 *
 * 流程：数据装配 → 风险闸门 → 7 位分析师 → 多空辩论 → 研究总监
 *       → 交易员 → 风控委员会(激进/中性/保守) → 组合经理（最终决策）
 *
 * 双模式：深度（全角色）/ 快速（核心 4 分析师 + 组合经理），布局各自适配。
 * 进度双通道：WS type="diagnosis" 实时事件（含 thinking 增量流）+ 3 秒轮询兜底。
 * ============================================================ */

type StageKey =
  | 'data'
  | 'riskgate'
  | 'tech'
  | 'fund'
  | 'news'
  | 'fundamental'
  | 'macro'
  | 'quant'
  | 'sector'
  | 'bull'
  | 'bear'
  | 'research'
  | 'trader'
  | 'risk_agg'
  | 'risk_neu'
  | 'risk_con'
  | 'chief'
type StageStatus = 'pending' | 'running' | 'done' | 'failed'
type NodeGroup = 'io' | 'gate' | 'analyst' | 'debate' | 'research' | 'trader' | 'risk' | 'chief'

interface NodeMeta {
  label: string
  desc: string
  icon: typeof Activity
  group: NodeGroup
}

/** 节点静态元信息（与坐标解耦，深/快布局共用） */
const NODE_META: Record<StageKey, NodeMeta> = {
  data: { label: '数据装配', desc: 'K线·因子·资金·新闻·板块', icon: Database, group: 'io' },
  riskgate: { label: '风险闸门', desc: 'ST·一字板·流动性硬规则', icon: ShieldAlert, group: 'gate' },
  tech: { label: '技术面分析师', desc: '趋势·量价·动量·形态', icon: Activity, group: 'analyst' },
  fund: { label: '资金面分析师', desc: '主力·龙虎榜·人气', icon: Banknote, group: 'analyst' },
  news: { label: '消息面分析师', desc: '新闻·公告·催化剂', icon: Newspaper, group: 'analyst' },
  fundamental: { label: '基本面分析师', desc: '估值·业绩·股息', icon: Landmark, group: 'analyst' },
  macro: { label: '宏观择时分析师', desc: '大盘·风格·择时系数', icon: Compass, group: 'analyst' },
  quant: { label: '量化分析师', desc: '多因子分位·RPS排名', icon: Sigma, group: 'analyst' },
  sector: { label: '板块同业分析师', desc: '板块强弱·龙头地位', icon: Network, group: 'analyst' },
  bull: { label: '多方研究员', desc: '为上涨机会正名', icon: Swords, group: 'debate' },
  bear: { label: '空方研究员', desc: '打穿脆弱假设', icon: Swords, group: 'debate' },
  research: { label: '研究总监', desc: '裁决辩论·研究结论', icon: Microscope, group: 'research' },
  trader: { label: '交易员', desc: '买点·止盈·止损·仓位', icon: Target, group: 'trader' },
  risk_agg: { label: '风控·激进派', desc: '进攻视角', icon: Flame, group: 'risk' },
  risk_neu: { label: '风控·中性派', desc: '均衡视角', icon: Scale, group: 'risk' },
  risk_con: { label: '风控·保守派', desc: '本金安全第一', icon: ShieldCheck, group: 'risk' },
  chief: { label: '组合经理', desc: '最终决策·操作清单', icon: Crown, group: 'chief' },
}

interface NodePos {
  key: StageKey
  x: number
  y: number
  w: number
  h: number
}
interface Edge {
  from: StageKey
  to: StageKey
}
interface Layout {
  w: number
  h: number
  nodes: NodePos[]
  edges: Edge[]
}

/** 深度模式布局：16 节点分 7 层（左→右） */
const ANALYST_ORDER: StageKey[] = ['tech', 'fund', 'news', 'fundamental', 'macro', 'quant', 'sector']
const DEEP_LAYOUT: Layout = (() => {
  const nodes: NodePos[] = [
    { key: 'data', x: 12, y: 206, w: 190, h: 92 },
    { key: 'riskgate', x: 12, y: 330, w: 190, h: 92 },
    ...ANALYST_ORDER.map((key, i) => ({ key, x: 276, y: 6 + i * 90, w: 208, h: 80 })),
    { key: 'bull', x: 566, y: 150, w: 200, h: 118 },
    { key: 'bear', x: 566, y: 398, w: 200, h: 118 },
    { key: 'research', x: 812, y: 282, w: 200, h: 118 },
    { key: 'trader', x: 1058, y: 280, w: 212, h: 128 },
    { key: 'risk_agg', x: 1318, y: 190, w: 190, h: 92 },
    { key: 'risk_neu', x: 1318, y: 300, w: 190, h: 92 },
    { key: 'risk_con', x: 1318, y: 410, w: 190, h: 92 },
    { key: 'chief', x: 1556, y: 286, w: 198, h: 140 },
  ]
  const edges: Edge[] = [
    { from: 'data', to: 'riskgate' },
    ...ANALYST_ORDER.map((key) => ({ from: 'riskgate' as StageKey, to: key })),
    ...ANALYST_ORDER.flatMap((key) => [
      { from: key, to: 'bull' as StageKey },
      { from: key, to: 'bear' as StageKey },
    ]),
    { from: 'bull', to: 'research' },
    { from: 'bear', to: 'research' },
    { from: 'research', to: 'trader' },
    { from: 'trader', to: 'risk_agg' },
    { from: 'trader', to: 'risk_neu' },
    { from: 'trader', to: 'risk_con' },
    { from: 'risk_agg', to: 'chief' },
    { from: 'risk_neu', to: 'chief' },
    { from: 'risk_con', to: 'chief' },
  ]
  return { w: 1766, h: 660, nodes, edges }
})()

/** 快速模式布局：数据→风险门→核心4分析师→组合经理 */
const QUICK_ANALYSTS: StageKey[] = ['tech', 'fund', 'news', 'quant']
const QUICK_LAYOUT: Layout = {
  w: 900,
  h: 500,
  nodes: [
    { key: 'data', x: 20, y: 180, w: 190, h: 92 },
    { key: 'riskgate', x: 20, y: 300, w: 190, h: 92 },
    ...QUICK_ANALYSTS.map((key, i) => ({ key, x: 300, y: 28 + i * 110, w: 220, h: 92 })),
    { key: 'chief', x: 620, y: 200, w: 230, h: 150 },
  ],
  edges: [
    { from: 'data', to: 'riskgate' },
    ...QUICK_ANALYSTS.map((key) => ({ from: 'riskgate' as StageKey, to: key })),
    ...QUICK_ANALYSTS.map((key) => ({ from: key, to: 'chief' as StageKey })),
  ],
}

const STANCE_TEXT: Record<string, { text: string; cls: string }> = {
  bullish: { text: '看多', cls: 'bg-up/15 text-up border-up/30' },
  bearish: { text: '看空', cls: 'bg-down/15 text-down border-down/30' },
  neutral: { text: '中性', cls: 'bg-muted text-muted-foreground border-border' },
}

/** 评级 → 颜色（A 股习惯：红=多头操作，绿=空头操作） */
const RATING_CLS: Record<string, string> = {
  强烈买入: 'bg-up text-white',
  买入: 'bg-up/80 text-white',
  持有: 'bg-gold/80 text-black',
  减仓: 'bg-down/70 text-white',
  卖出: 'bg-down text-white',
}

/** 风险闸门级别 → 配色 */
const RISK_LEVEL_META: Record<string, { text: string; cls: string }> = {
  pass: { text: '通过', cls: 'text-down border-down/30 bg-down/10' },
  warn: { text: '警示', cls: 'text-gold border-gold/40 bg-gold/10' },
  block: { text: '否决', cls: 'text-up border-up/40 bg-up/10' },
}

type StagesMap = NonNullable<DiagnosisRun['stages']>

export function DiagnosisFlow({
  symbol,
  name,
  open,
  onClose,
  startFn,
  asOf,
}: {
  symbol: string
  name?: string
  open: boolean
  onClose: () => void
  /** 自定义发起函数（持仓诊断注入割/守/补上下文用）；缺省走普通诊股 */
  startFn?: (mode: DiagnosisMode) => Promise<{ run_id: number; reused: boolean }>
  /** 回溯模式历史节点（YYYY-MM-DD）：传入则以该日为"今天"做诊断并展示回测校验 */
  asOf?: string
}) {
  if (!open) return null
  return (
    <Portal>
      <FlowInner symbol={symbol} name={name} onClose={onClose} startFn={startFn} asOf={asOf} />
    </Portal>
  )
}

function FlowInner({
  symbol,
  name,
  onClose,
  startFn,
  asOf,
}: {
  symbol: string
  name?: string
  onClose: () => void
  startFn?: (mode: DiagnosisMode) => Promise<{ run_id: number; reused: boolean }>
  asOf?: string
}) {
  const [runId, setRunId] = useState<number | null>(null)
  const [run, setRun] = useState<DiagnosisRun | null>(null)
  const [mode, setMode] = useState<DiagnosisMode>('deep')
  const [stageStatus, setStageStatus] = useState<Partial<Record<StageKey, StageStatus>>>({})
  // WS 实时产出与思考流：独立于 run 存放（run 可能尚未拉到，依附其上会丢事件）
  const [liveStages, setLiveStages] = useState<StagesMap>({})
  const [liveResult, setLiveResult] = useState<Partial<ChiefDecision> | null>(null)
  const [thinking, setThinking] = useState<Partial<Record<StageKey, string>>>({})
  const [startError, setStartError] = useState('')
  const [starting, setStarting] = useState(false)
  const [checked, setChecked] = useState(false)
  // 右侧抽屉：节点详情 / 历史列表（互斥）
  const [selected, setSelected] = useState<StageKey | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)

  const isRunning = run?.status === 'running' || (runId !== null && run === null)
  // 画布布局：以已落库结果的 mode 为准，运行中用当前选择的 mode
  const effectiveMode: DiagnosisMode = run?.result?.mode ?? mode
  const layout = effectiveMode === 'quick' ? QUICK_LAYOUT : DEEP_LAYOUT

  // ---- 打开时拉最近一次记录：done 直接展示，running 接续监听 ----
  // 回溯模式始终发起全新诊断（不复用最新实时记录），故跳过此加载
  useEffect(() => {
    if (asOf) {
      setChecked(true)
      return
    }
    let cancelled = false
    fetchLatestDiagnosis(symbol)
      .then((latest) => {
        if (cancelled) return
        if (latest) {
          setRun(latest)
          setRunId(latest.run_id)
          if (latest.result?.mode) setMode(latest.result.mode)
          if (latest.status !== 'running') setStageStatus(statusFromRun(latest))
        }
        setChecked(true)
      })
      .catch(() => setChecked(true))
    return () => {
      cancelled = true
    }
  }, [symbol, asOf])

  // ---- WS 事件：节点状态 + 思考流 + 阶段产出 ----
  const onWsEvent = useCallback(
    (event: Record<string, unknown>) => {
      if (runId === null || event.run_id !== runId) return
      const stage = event.stage as StageKey | 'all'
      const status = event.status as StageStatus | 'thinking'
      const payload = (event.payload ?? {}) as Record<string, unknown>

      if (stage === 'all') {
        fetchDiagnosisRun(runId)
          .then((r) => {
            setRun(r)
            setStageStatus((prev) => ({ ...prev, ...statusFromRun(r) }))
          })
          .catch(() => {})
        return
      }
      if (status === 'thinking') {
        const delta = String(payload.delta ?? '')
        setStageStatus((prev) => (prev[stage] === 'running' ? prev : { ...prev, [stage]: 'running' }))
        setThinking((prev) => ({ ...prev, [stage]: ((prev[stage] ?? '') + delta).slice(-8000) }))
        return
      }
      setStageStatus((prev) => ({ ...prev, [stage]: status }))
      if (status === 'done' && Object.keys(payload).length > 0) {
        setLiveStages((prev) => ({ ...prev, [stage]: payload }))
        if (stage === 'chief') setLiveResult(payload as Partial<ChiefDecision>)
      }
    },
    [runId],
  )
  useWsEvent('diagnosis', onWsEvent)

  // ---- 轮询兜底：running 期间每 3s 拉一次完整状态 ----
  const pollRef = useRef<number>(undefined)
  useEffect(() => {
    if (runId === null || !isRunning) return
    pollRef.current = window.setInterval(() => {
      fetchDiagnosisRun(runId)
        .then((r) => {
          setRun(r)
          setStageStatus((prev) => {
            const derived = statusFromRun(r)
            const merged = { ...prev }
            for (const [k, v] of Object.entries(derived) as [StageKey, StageStatus][]) {
              if (v === 'done' || r.status !== 'running') merged[k] = v
            }
            return merged
          })
        })
        .catch(() => {})
    }, 3000)
    return () => window.clearInterval(pollRef.current)
  }, [runId, isRunning])

  // ---- 发起诊断 ----
  const start = async (useMode: DiagnosisMode) => {
    setStarting(true)
    setStartError('')
    setMode(useMode)
    try {
      const { run_id } = await (startFn
        ? startFn(useMode)
        : startDiagnosis(symbol, useMode, asOf))
      setRunId(run_id)
      setRun(null)
      setLiveStages({})
      setLiveResult(null)
      setThinking({})
      setSelected(null)
      setHistoryOpen(false)
      setStageStatus({ data: 'running' })
      fetchDiagnosisRun(run_id)
        .then(setRun)
        .catch(() => {})
    } catch (e) {
      setStartError((e as ApiError).message)
    } finally {
      setStarting(false)
    }
  }

  /** 从历史列表加载一次旧诊断回看 */
  const loadHistoryRun = async (id: number) => {
    try {
      const r = await fetchDiagnosisRun(id)
      setRunId(r.run_id)
      setRun(r)
      if (r.result?.mode) setMode(r.result.mode)
      setLiveStages({})
      setLiveResult(null)
      setThinking({})
      setStageStatus(statusFromRun(r))
      setHistoryOpen(false)
      setSelected(null)
    } catch {
      /* 列表项可能已被清理，静默忽略 */
    }
  }

  // Esc：先关抽屉，再关整个工作流
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (selected || historyOpen) {
        setSelected(null)
        setHistoryOpen(false)
      } else {
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, selected, historyOpen])

  // 展示数据 = 落库结果（run）与 WS 实时产出（live）合并，live 优先
  const stages: StagesMap = { ...run?.stages, ...liveStages }
  const mergedResult = liveResult ?? (run?.status === 'done' ? run.result : null)
  const decision =
    mergedResult && Object.keys(mergedResult).length > 0 ? (mergedResult as ChiefDecision) : null
  const showWelcome = checked && runId === null

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background/95 backdrop-blur-md">
      {/* ---- 顶栏 ---- */}
      <header className="flex shrink-0 items-center gap-3 border-b bg-card/60 px-5 py-3">
        <BrainCircuit className="size-5 text-gold" />
        <h2 className="text-sm font-semibold">AI 投研工作流</h2>
        <span className="font-data text-xs text-text-muted">
          {name ?? symbol}（{symbol}）
        </span>
        {runId !== null && (
          <span className="rounded-full border border-border bg-muted/50 px-2 py-0.5 text-[11px] text-muted-foreground">
            {effectiveMode === 'quick' ? '快速模式' : '深度模式'}
          </span>
        )}
        {asOf && (
          <span
            className="rounded-full border border-gold/40 bg-gold/10 px-2 py-0.5 text-[11px] text-gold"
            title="回溯模式：数据严格截断到该历史日期，用于检验 AI 当时判断 vs 后续真实走势"
          >
            回溯 @{asOf}
          </span>
        )}
        {isRunning && (
          <span className="flex items-center gap-1.5 rounded-full border border-gold/40 bg-gold/10 px-2.5 py-0.5 text-[11px] text-gold">
            <Loader2 className="size-3 animate-spin" /> 智能体协作中
          </span>
        )}
        {run?.status === 'done' && (
          <span className="rounded-full border border-down/40 bg-down/10 px-2.5 py-0.5 text-[11px] text-down">
            完成 · {run.cost_seconds}s · {run.model}
          </span>
        )}
        {run?.status === 'failed' && (
          <span className="rounded-full border border-destructive/40 bg-destructive/10 px-2.5 py-0.5 text-[11px] text-destructive">
            诊断失败
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {!asOf && (
            <button
              onClick={() => {
                setHistoryOpen((v) => !v)
                setSelected(null)
              }}
              title="历史诊断记录"
              className={cn(
                'flex items-center gap-1.5 rounded-control border px-3 py-1.5 text-xs transition-colors',
                historyOpen
                  ? 'border-gold/50 bg-gold/10 text-gold'
                  : 'text-muted-foreground hover:border-gold/30 hover:text-gold',
              )}
            >
              <History className="size-3.5" /> 历史
            </button>
          )}
          {run?.status === 'done' && (
            <a
              href={diagnosisExportUrl(run.run_id)}
              download
              title="下载 Markdown 报告（含完整思考过程）"
              className="flex items-center gap-1.5 rounded-control border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-gold/30 hover:text-gold"
            >
              <Download className="size-3.5" /> 导出报告
            </a>
          )}
          {run && !isRunning && (
            <div className="flex items-center overflow-hidden rounded-control border border-gold/40">
              <button
                onClick={() => start('quick')}
                disabled={starting}
                title="快速模式：核心4分析师+组合经理，秒级"
                className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-gold transition-colors hover:bg-gold/10"
              >
                <Zap className="size-3.5" /> 快速
              </button>
              <span className="h-4 w-px bg-gold/30" />
              <button
                onClick={() => start('deep')}
                disabled={starting}
                title="深度模式：全角色完整工作流"
                className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-gold transition-colors hover:bg-gold/10"
              >
                <Sparkles className="size-3.5" /> 深度
              </button>
            </div>
          )}
          <button
            onClick={onClose}
            className="flex size-8 items-center justify-center rounded-control border text-muted-foreground transition-colors hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>
      </header>

      {/* ---- 主体：画布 ---- */}
      <div className="relative flex-1 overflow-auto">
        {showWelcome && (
          <Welcome starting={starting} error={startError} mode={mode} setMode={setMode} onStart={start} />
        )}

        {runId !== null && (
          <div className="flex min-h-full items-center justify-center p-6">
            <Canvas
              layout={layout}
              status={stageStatus}
              stages={stages}
              thinking={thinking}
              decision={decision}
              failed={run?.status === 'failed'}
              onSelect={(k) => {
                setSelected(k)
                setHistoryOpen(false)
              }}
            />
          </div>
        )}

        {/* 失败横幅 */}
        {run?.status === 'failed' && (
          <div className="absolute inset-x-0 bottom-4 mx-auto w-fit rounded-card border border-destructive/40 bg-destructive/10 px-4 py-2 text-xs text-destructive backdrop-blur">
            <AlertTriangle className="mr-1.5 inline size-3.5" />
            {run.error || '诊断失败'}（可点右上角"重新诊断"重试）
          </div>
        )}

        {/* 回测校验：回溯模式诊断完成后，用 as_of 之后的真实走势对比 AI 判断 */}
        {asOf && run?.status === 'done' && runId !== null && (
          <div className="absolute inset-x-0 top-3 z-10 mx-auto w-fit max-w-[94%]">
            <BacktestVerifyPanel runId={runId} />
          </div>
        )}
      </div>

      {/* ---- 底部决策横幅 ---- */}
      <AnimatePresence>
        {decision && (
          <DecisionDock
            decision={decision}
            runId={run?.run_id ?? runId ?? 0}
            done={run?.status === 'done'}
            onDetail={() => setSelected('chief')}
          />
        )}
      </AnimatePresence>

      {/* ---- 右侧抽屉：节点详情 ---- */}
      <AnimatePresence>
        {selected && (
          <Drawer onClose={() => setSelected(null)}>
            <NodeDetail
              stageKey={selected}
              stages={stages}
              decision={decision}
              thinkingLive={thinking[selected]}
            />
          </Drawer>
        )}
      </AnimatePresence>

      {/* ---- 右侧抽屉：历史记录 ---- */}
      <AnimatePresence>
        {historyOpen && (
          <Drawer onClose={() => setHistoryOpen(false)}>
            <HistoryPanel symbol={symbol} activeRunId={runId} onPick={loadHistoryRun} />
          </Drawer>
        )}
      </AnimatePresence>
    </div>
  )
}

/** 回测校验面板：回溯诊断完成后，拉取 as_of 之后真实走势与 AI 判断对比。
 *
 * 展示：+5/10/20/60 日真实涨跌、区间最大涨/跌幅、AI 目标价/止损价是否被触及。
 * 这是回溯模式的"价值落点"——直观回答"AI 当时判断准不准"。 */
function BacktestVerifyPanel({ runId }: { runId: number }) {
  const [data, setData] = useState<DiagnosisVerify | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let cancel = false
    verifyDiagnosis(runId)
      .then((d) => !cancel && setData(d))
      .catch((e) => !cancel && setErr((e as ApiError).message))
    return () => {
      cancel = true
    }
  }, [runId])

  if (err || !data) return null

  const pctCls = (v: number | null | undefined) =>
    v == null ? 'text-text-muted' : v >= 0 ? 'text-up' : 'text-down'
  const pctStr = (v: number | null | undefined) =>
    v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
  const wins: { k: keyof DiagnosisVerify['windows']; label: string }[] = [
    { k: 'd5', label: '+5日' },
    { k: 'd10', label: '+10日' },
    { k: 'd20', label: '+20日' },
    { k: 'd60', label: '+60日' },
  ]

  return (
    <div className="rounded-card border border-gold/40 bg-card/95 px-4 py-3 shadow-xl backdrop-blur">
      <div className="mb-2 flex items-center gap-2">
        <Target className="size-4 text-gold" />
        <span className="text-xs font-semibold text-gold">回测校验</span>
        <span className="text-[11px] text-text-muted">
          基准 {data.base_price}（{data.as_of}收盘） · 已积累 {data.bars} 个交易日
          {data.bars > 0 ? ` → ${data.last_date}` : ''}
        </span>
      </div>

      {data.bars === 0 ? (
        <p className="text-[11px] text-text-muted">该历史节点之后暂无交易数据，过段时间再来看校验结果。</p>
      ) : (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {/* 各持有窗口真实涨跌 */}
          <div className="flex items-center gap-3">
            {wins.map((w) => (
              <div key={w.k} className="text-center">
                <div className="text-[10px] text-text-muted">{w.label}</div>
                <div className={cn('font-data text-sm font-semibold', pctCls(data.windows[w.k]))}>
                  {pctStr(data.windows[w.k])}
                </div>
              </div>
            ))}
          </div>
          <span className="h-8 w-px bg-border" />
          {/* 区间最大涨跌 */}
          <div className="flex items-center gap-3 text-[11px]">
            <span>
              区间最高 <span className={pctCls(data.max_gain)}>{pctStr(data.max_gain)}</span>
            </span>
            <span>
              最低 <span className={pctCls(data.max_drop)}>{pctStr(data.max_drop)}</span>
            </span>
          </div>
          {/* AI 目标价 / 止损价 是否被验证 */}
          {(data.target_price || data.stop_loss_price) && (
            <>
              <span className="h-8 w-px bg-border" />
              <div className="flex items-center gap-3 text-[11px]">
                {data.target_price ? (
                  <span className="text-up">
                    目标 {data.target_price}{' '}
                    {data.target_hit_day ? `✓第${data.target_hit_day}日达成` : '未达成'}
                  </span>
                ) : null}
                {data.stop_loss_price ? (
                  <span className="text-down">
                    止损 {data.stop_loss_price}{' '}
                    {data.stop_hit_day ? `⚠第${data.stop_hit_day}日触发` : '未触发'}
                  </span>
                ) : null}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** 从已落库的 run 推导节点状态（历史回看/轮询落定共用） */
function statusFromRun(r: DiagnosisRun): Partial<Record<StageKey, StageStatus>> {
  const st: Partial<Record<StageKey, StageStatus>> = { data: 'done' }
  const keys: StageKey[] = [
    'riskgate',
    'tech',
    'fund',
    'news',
    'fundamental',
    'macro',
    'quant',
    'sector',
    'bull',
    'bear',
    'research',
    'trader',
    'risk_agg',
    'risk_neu',
    'risk_con',
  ]
  for (const k of keys) {
    if (r.stages?.[k as keyof StagesMap]) st[k] = 'done'
  }
  st.chief = r.status === 'done' ? 'done' : r.status === 'failed' ? 'failed' : 'pending'
  return st
}

/* ============================================================
 * 启动欢迎页（含快/深模式选择）
 * ============================================================ */
function Welcome({
  starting,
  error,
  mode,
  setMode,
  onStart,
}: {
  starting: boolean
  error: string
  mode: DiagnosisMode
  setMode: (m: DiagnosisMode) => void
  onStart: (m: DiagnosisMode) => void
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex h-full flex-col items-center justify-center gap-6 text-center"
    >
      <div className="relative">
        <div className="absolute inset-0 animate-ping rounded-full bg-gold/20" />
        <div className="relative flex size-20 items-center justify-center rounded-full border border-gold/40 bg-gold/10">
          <BrainCircuit className="size-9 text-gold" />
        </div>
      </div>
      <div>
        <h3 className="text-lg font-semibold">多角色 AI 投研团队</h3>
        <p className="mx-auto mt-2 max-w-lg text-sm leading-relaxed text-text-muted">
          风险闸门把关 → 七位分析师（技术/资金/消息/基本面/宏观/量化/板块）并行研判 → 多空辩论
          → 研究总监裁决 → 交易员定买卖点与仓位 → 风控委三方评审 → 组合经理拍板。
          <br />
          支持 DeepSeek 深度思考——每个角色"在想什么"全程可见。
        </p>
      </div>

      {/* 模式分段选择 */}
      <div className="flex items-center gap-1 rounded-full border bg-card p-1">
        {(['deep', 'quick'] as DiagnosisMode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={cn(
              'flex items-center gap-1.5 rounded-full px-4 py-1.5 text-xs transition-colors',
              mode === m ? 'bg-gold text-black' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {m === 'deep' ? <Sparkles className="size-3.5" /> : <Zap className="size-3.5" />}
            {m === 'deep' ? '深度模式（全角色）' : '快速模式（秒级）'}
          </button>
        ))}
      </div>

      <button
        onClick={() => onStart(mode)}
        disabled={starting}
        className="flex items-center gap-2 rounded-full bg-gold px-6 py-2.5 text-sm font-semibold text-black transition-transform hover:scale-105 disabled:opacity-60"
      >
        {starting ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
        启动 AI 投研
      </button>
      {error && (
        <p className="max-w-md rounded-control border border-destructive/40 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {error}
        </p>
      )}
    </motion.div>
  )
}

/* ============================================================
 * 画布：SVG 连线层 + 绝对定位节点
 * ============================================================ */
function Canvas({
  layout,
  status,
  stages,
  thinking,
  decision,
  failed,
  onSelect,
}: {
  layout: Layout
  status: Partial<Record<StageKey, StageStatus>>
  stages: StagesMap
  thinking: Partial<Record<StageKey, string>>
  decision: ChiefDecision | null
  failed?: boolean
  onSelect: (k: StageKey) => void
}) {
  const nodeMap = Object.fromEntries(layout.nodes.map((n) => [n.key, n])) as Record<
    StageKey,
    NodePos
  >

  return (
    <div className="relative shrink-0" style={{ width: layout.w, height: layout.h }}>
      {/* 点阵背景（N8N 画布质感） */}
      <div
        className="absolute inset-0 rounded-card opacity-50"
        style={{
          backgroundImage: 'radial-gradient(circle, var(--border) 1px, transparent 1px)',
          backgroundSize: '22px 22px',
        }}
      />

      {/* 连线层 */}
      <svg className="absolute inset-0" width={layout.w} height={layout.h} fill="none">
        {layout.edges.map((e, i) => (
          <FlowEdge
            key={`${e.from}-${e.to}`}
            id={`edge-${i}`}
            from={nodeMap[e.from]}
            to={nodeMap[e.to]}
            fromStatus={status[e.from] ?? 'pending'}
            toStatus={status[e.to] ?? 'pending'}
          />
        ))}
      </svg>

      {/* 节点层 */}
      {layout.nodes.map((n, i) => (
        <FlowNode
          key={n.key}
          pos={n}
          index={i}
          status={failed && n.key === 'chief' ? 'failed' : status[n.key] ?? 'pending'}
          stages={stages}
          thinking={thinking[n.key]}
          decision={n.key === 'chief' ? decision : null}
          onClick={() => onSelect(n.key)}
        />
      ))}
    </div>
  )
}

/** 单条连线：贝塞尔曲线；激活时金色 + 粒子沿线流动 */
function FlowEdge({
  id,
  from,
  to,
  fromStatus,
  toStatus,
}: {
  id: string
  from?: NodePos
  to?: NodePos
  fromStatus: StageStatus
  toStatus: StageStatus
}) {
  if (!from || !to) return null
  const x1 = from.x + from.w
  const y1 = from.y + from.h / 2
  const x2 = to.x
  const y2 = to.y + to.h / 2
  const dx = Math.max(46, (x2 - x1) * 0.45)
  const path = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`

  const flowing = fromStatus === 'done' && (toStatus === 'running' || toStatus === 'pending')
  const complete = fromStatus === 'done' && toStatus === 'done'

  return (
    <g>
      <path
        d={path}
        strokeWidth={complete ? 1.8 : 1.4}
        className={cn(
          'transition-all duration-700',
          complete ? 'stroke-gold/55' : flowing ? 'stroke-gold/35' : 'stroke-border',
        )}
      />
      {flowing && (
        <>
          <path id={id} d={path} className="stroke-none" />
          <circle r="3" className="fill-gold">
            <animateMotion dur="1.6s" repeatCount="indefinite">
              <mpath href={`#${id}`} />
            </animateMotion>
          </circle>
          <circle r="2" className="fill-gold/50">
            <animateMotion dur="1.6s" begin="0.5s" repeatCount="indefinite">
              <mpath href={`#${id}`} />
            </animateMotion>
          </circle>
        </>
      )}
    </g>
  )
}

/** 画布节点卡：状态驱动外观；running 时展示思考流；完成后浮现结论摘要 */
function FlowNode({
  pos,
  index,
  status,
  stages,
  thinking,
  decision,
  onClick,
}: {
  pos: NodePos
  index: number
  status: StageStatus
  stages: StagesMap
  thinking?: string
  decision: ChiefDecision | null
  onClick: () => void
}) {
  const meta = NODE_META[pos.key]
  const Icon = meta.icon
  const sideCls = pos.key === 'bull' ? 'text-up' : pos.key === 'bear' ? 'text-down' : ''

  return (
    <motion.button
      type="button"
      initial={{ opacity: 0, scale: 0.92 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: index * 0.04, type: 'spring', stiffness: 200, damping: 22 }}
      onClick={onClick}
      className={cn(
        'absolute flex flex-col rounded-card border bg-card/95 p-3 text-left backdrop-blur transition-all',
        'hover:-translate-y-0.5 hover:shadow-lg',
        status === 'running' && 'border-gold/60 shadow-[0_0_28px_-4px] shadow-gold/40',
        status === 'done' && 'border-border hover:border-gold/40',
        status === 'pending' && 'border-border/60 opacity-65',
        status === 'failed' && 'border-destructive/50',
      )}
      style={{ left: pos.x, top: pos.y, width: pos.w, height: pos.h }}
    >
      {/* 头部：图标 + 名称 + 状态 */}
      <div className="flex items-center gap-2">
        <div
          className={cn(
            'relative flex size-7 shrink-0 items-center justify-center rounded-control border',
            status === 'done' && 'border-gold/40 bg-gold/10 text-gold',
            status === 'running' && 'border-gold/50 bg-gold/15 text-gold',
            status === 'pending' && 'border-border bg-muted/40 text-text-muted',
            status === 'failed' && 'border-destructive/40 bg-destructive/10 text-destructive',
          )}
        >
          {status === 'running' && (
            <motion.span
              className="absolute inset-0 rounded-control bg-gold/25"
              animate={{ scale: [1, 1.5], opacity: [0.7, 0] }}
              transition={{ repeat: Infinity, duration: 1.1 }}
            />
          )}
          <Icon className={cn('size-3.5', sideCls)} />
        </div>
        <div className="min-w-0 flex-1">
          <div className={cn('truncate text-xs font-semibold', sideCls)}>{meta.label}</div>
          <div className="truncate text-[10px] text-text-muted">{meta.desc}</div>
        </div>
        <NodeStatusDot status={status} />
      </div>

      {/* 主体：按状态切换 */}
      <div className="mt-2 min-h-0 flex-1 overflow-hidden">
        {status === 'running' && <ThinkingStream text={thinking} />}
        {status === 'done' && <NodeBody stageKey={pos.key} stages={stages} decision={decision} />}
        {status === 'pending' && <p className="text-[11px] text-text-muted/70">等待上游…</p>}
        {status === 'failed' && <p className="text-[11px] text-destructive">执行失败，可重新诊断</p>}
      </div>

      {(status === 'done' || (status === 'running' && thinking)) && (
        <div className="mt-1 flex items-center gap-0.5 text-[10px] text-text-muted/70">
          查看详情 <ChevronRight className="size-2.5" />
        </div>
      )}
    </motion.button>
  )
}

/** 节点完成态主体：按角色类别渲染不同摘要 */
function NodeBody({
  stageKey,
  stages,
  decision,
}: {
  stageKey: StageKey
  stages: StagesMap
  decision: ChiefDecision | null
}) {
  const meta = NODE_META[stageKey]

  if (stageKey === 'data') {
    return <p className="text-[11px] text-muted-foreground">数值已由系统算好，AI 只解读不算术</p>
  }
  if (stageKey === 'riskgate') {
    const gate = stages.riskgate
    const lv = RISK_LEVEL_META[gate?.level ?? 'pass'] ?? RISK_LEVEL_META.pass
    return (
      <div className="space-y-1">
        <span className={cn('inline-block rounded-full border px-2 py-px text-[10px]', lv.cls)}>
          {lv.text}
        </span>
        <p className="line-clamp-2 text-[11px] leading-snug text-muted-foreground">
          {gate?.flags?.length ? gate.flags.join('、') : '未触发结构性硬风险'}
        </p>
      </div>
    )
  }
  if (meta.group === 'analyst') {
    const report = stages[stageKey as keyof StagesMap] as AnalystReport | undefined
    if (!report) return null
    const stance = STANCE_TEXT[report.stance] ?? STANCE_TEXT.neutral
    return (
      <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="font-data text-lg font-bold text-gold">{report.score}</span>
          <span className={cn('rounded-full border px-1.5 py-px text-[10px]', stance.cls)}>
            {stance.text}
          </span>
          {report.timing_coef != null && (
            <span className="font-data text-[10px] text-text-muted">系数 {report.timing_coef}</span>
          )}
        </div>
        <p className="line-clamp-2 text-[11px] leading-snug text-muted-foreground">{report.summary}</p>
      </motion.div>
    )
  }
  if (meta.group === 'debate') {
    const speech = stages[stageKey as keyof StagesMap] as DebateSpeech | undefined
    return (
      <p className="line-clamp-4 text-[11px] leading-snug text-muted-foreground">
        {speech?.argument}
      </p>
    )
  }
  if (stageKey === 'research') {
    const r = stages.research
    if (!r) return null
    const stance = STANCE_TEXT[r.stance] ?? STANCE_TEXT.neutral
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className={cn('rounded-full border px-1.5 py-px text-[10px]', stance.cls)}>
            {stance.text}
          </span>
          <span className="font-data text-[11px] text-text-muted">置信 {r.conviction}</span>
        </div>
        <p className="line-clamp-3 text-[11px] leading-snug text-muted-foreground">{r.summary}</p>
      </div>
    )
  }
  if (stageKey === 'trader') {
    const t = stages.trader
    if (!t) return null
    return (
      <div className="space-y-0.5 text-[11px] text-muted-foreground">
        <div className="flex justify-between">
          <span>目标价</span>
          <span className="font-data text-up">{t.target_price || '—'}</span>
        </div>
        <div className="flex justify-between">
          <span>止损价</span>
          <span className="font-data text-down">{t.stop_loss_price || '—'}</span>
        </div>
        <div className="flex justify-between">
          <span>仓位</span>
          <span className="font-data">{t.position_pct}%</span>
        </div>
      </div>
    )
  }
  if (meta.group === 'risk') {
    const m = stages[stageKey as keyof StagesMap] as RiskMemberView | undefined
    if (!m) return null
    const adj = m.position_adjust
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-2 text-[11px]">
          <span className="font-medium">{m.stance}</span>
          <span
            className={cn('font-data', adj > 0 ? 'text-up' : adj < 0 ? 'text-down' : 'text-text-muted')}
          >
            {adj > 0 ? `+${adj}` : adj}
          </span>
        </div>
        <p className="line-clamp-2 text-[11px] leading-snug text-muted-foreground">{m.summary}</p>
      </div>
    )
  }
  if (stageKey === 'chief' && decision) {
    return (
      <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              'inline-block rounded-control px-2.5 py-1 text-sm font-bold',
              RATING_CLS[decision.rating] ?? 'bg-muted',
            )}
          >
            {decision.rating}
          </span>
          {decision.action && (
            <span className="rounded-control border border-gold/40 bg-gold/10 px-1.5 py-0.5 text-[10px] text-gold">
              {decision.action}
            </span>
          )}
        </div>
        <div className="flex gap-3 font-data text-[11px] text-muted-foreground">
          <span>评分 {decision.score}</span>
          <span>仓位 {decision.position_pct}%</span>
        </div>
      </motion.div>
    )
  }
  return null
}

function NodeStatusDot({ status }: { status: StageStatus }) {
  if (status === 'running') return <Loader2 className="size-3.5 shrink-0 animate-spin text-gold" />
  if (status === 'done') return <CheckCircle2 className="size-3.5 shrink-0 text-gold" />
  if (status === 'failed') return <AlertTriangle className="size-3.5 shrink-0 text-destructive" />
  return <span className="size-2 shrink-0 rounded-full bg-border" />
}

/** 思考流窗口：实时滚动展示模型的 reasoning 尾部，带光标闪烁 */
function ThinkingStream({ text }: { text?: string }) {
  if (!text) {
    return (
      <div className="flex items-center gap-1.5 text-[11px] text-gold/70">
        <motion.span
          animate={{ opacity: [0.4, 1, 0.4] }}
          transition={{ repeat: Infinity, duration: 1.4 }}
        >
          正在分析…
        </motion.span>
      </div>
    )
  }
  return (
    <div className="h-full rounded-inner border border-gold/15 bg-gold/4 px-2 py-1.5">
      <div className="mb-0.5 flex items-center gap-1 text-[9px] text-gold/70">
        <BrainCircuit className="size-2.5" /> 深度思考中
      </div>
      <p className="line-clamp-3 font-data text-[10px] leading-snug text-muted-foreground">
        …{text.slice(-160)}
        <motion.span
          className="ml-0.5 inline-block h-2.5 w-1 bg-gold/80 align-middle"
          animate={{ opacity: [1, 0] }}
          transition={{ repeat: Infinity, duration: 0.7 }}
        />
      </p>
    </div>
  )
}

/* ============================================================
 * 底部决策横幅：评级 + 关键指标 + 操作入口
 * ============================================================ */
function DecisionDock({
  decision,
  runId,
  done,
  onDetail,
}: {
  decision: ChiefDecision
  runId: number
  done?: boolean
  onDetail: () => void
}) {
  return (
    <motion.footer
      initial={{ y: 80, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      exit={{ y: 80, opacity: 0 }}
      transition={{ type: 'spring', stiffness: 220, damping: 26 }}
      className="shrink-0 border-t bg-card/85 px-5 py-3 backdrop-blur"
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2">
        <span
          className={cn(
            'rounded-control px-3.5 py-1.5 text-base font-bold tracking-wide',
            RATING_CLS[decision.rating] ?? 'bg-muted',
          )}
        >
          {decision.rating}
          {decision.action ? ` · ${decision.action}` : ''}
        </span>
        <DockMetric label="综合评分" value={String(decision.score)} />
        <DockMetric label="置信度" value={`${decision.confidence}%`} />
        <DockMetric label="建议仓位" value={`${decision.position_pct}%`} />
        {!!decision.target_price && (
          <DockMetric label="目标价" value={`${decision.target_price}`} accent="up" />
        )}
        {!!decision.stop_loss_price && (
          <DockMetric label="止损价" value={`${decision.stop_loss_price}`} accent="down" />
        )}
        <DockMetric label="周期" value={decision.horizon || '—'} />
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={onDetail}
            className="rounded-control border border-gold/40 px-3.5 py-1.5 text-xs text-gold transition-colors hover:bg-gold/10"
          >
            查看完整决策
          </button>
          {done && (
            <a
              href={diagnosisExportUrl(runId)}
              download
              className="flex items-center gap-1.5 rounded-control bg-gold px-3.5 py-1.5 text-xs font-medium text-black transition-opacity hover:opacity-90"
            >
              <Download className="size-3.5" /> 导出
            </a>
          )}
        </div>
        <p className="w-full text-[10px] text-text-muted">
          {decision.summary} · AI 生成内容仅供研究参考，不构成投资建议
        </p>
      </div>
    </motion.footer>
  )
}

function DockMetric({
  label,
  value,
  accent,
}: {
  label: string
  value: string
  accent?: 'up' | 'down'
}) {
  return (
    <div>
      <div className="text-[10px] text-text-muted">{label}</div>
      <div
        className={cn(
          'font-data text-sm font-semibold',
          accent === 'up' && 'text-up',
          accent === 'down' && 'text-down',
        )}
      >
        {value}
      </div>
    </div>
  )
}

/* ============================================================
 * 右侧抽屉容器
 * ============================================================ */
function Drawer({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-60 bg-black/30"
        onClick={onClose}
      />
      <motion.aside
        initial={{ x: '100%' }}
        animate={{ x: 0 }}
        exit={{ x: '100%' }}
        transition={{ type: 'spring', stiffness: 300, damping: 32 }}
        className="fixed inset-y-0 right-0 z-61 flex w-full max-w-[460px] flex-col border-l bg-card shadow-2xl"
      >
        <button
          onClick={onClose}
          className="absolute top-3 right-3 flex size-7 items-center justify-center rounded-control border text-muted-foreground transition-colors hover:text-foreground"
        >
          <X className="size-3.5" />
        </button>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </motion.aside>
    </>
  )
}

/* ============================================================
 * 抽屉内容：节点完整报告（含思考过程）
 * ============================================================ */
function NodeDetail({
  stageKey,
  stages,
  decision,
  thinkingLive,
}: {
  stageKey: StageKey
  stages: StagesMap
  decision: ChiefDecision | null
  thinkingLive?: string
}) {
  const meta = NODE_META[stageKey]
  const Icon = meta.icon
  const stageData = stages[stageKey as keyof StagesMap] as
    | (AnalystReport & DebateSpeech & ResearchConclusion & TraderPlan & RiskMemberView)
    | undefined
  const thinkingFull = (stageData?.thinking || decision_thinking(stages, stageKey) || thinkingLive) ?? ''

  return (
    <div className="space-y-4">
      <header className="flex items-center gap-2.5">
        <div className="flex size-9 items-center justify-center rounded-control border border-gold/30 bg-gold/10 text-gold">
          <Icon className="size-4" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{meta.label}</h3>
          <p className="text-[11px] text-text-muted">{meta.desc}</p>
        </div>
      </header>

      <NodeDetailBody stageKey={stageKey} stages={stages} decision={decision} />

      {/* 思考过程（推理模型才有） */}
      {thinkingFull && (
        <details className="group rounded-card border border-gold/20 bg-gold/4">
          <summary className="flex cursor-pointer items-center gap-1.5 px-3 py-2.5 text-xs text-gold">
            <BrainCircuit className="size-3.5" />
            深度思考过程（{thinkingFull.length} 字）
            <ChevronRight className="ml-auto size-3.5 transition-transform group-open:rotate-90" />
          </summary>
          <pre className="max-h-96 overflow-y-auto border-t border-gold/15 p-3 font-data text-[11px] leading-relaxed whitespace-pre-wrap text-muted-foreground">
            {thinkingFull}
          </pre>
        </details>
      )}
    </div>
  )
}

/** 抽屉正文：按角色类别渲染完整内容 */
function NodeDetailBody({
  stageKey,
  stages,
  decision,
}: {
  stageKey: StageKey
  stages: StagesMap
  decision: ChiefDecision | null
}) {
  const meta = NODE_META[stageKey]

  if (stageKey === 'data') {
    return (
      <p className="text-xs leading-relaxed text-muted-foreground">
        系统在本阶段装配该股的全部分析素材：近 10 根日K、全量技术/资金/基本面/盘中因子、
        近 10 日主力资金流、龙虎榜记录、业绩预告、所属板块与近期新闻。全部数值由 Python
        预先算好，AI 只负责解读——从根上消除大模型"算错数"的幻觉。
      </p>
    )
  }
  if (stageKey === 'riskgate') {
    const gate = stages.riskgate as RiskGate | undefined
    const lv = RISK_LEVEL_META[gate?.level ?? 'pass'] ?? RISK_LEVEL_META.pass
    return (
      <div className="space-y-3">
        <span className={cn('inline-block rounded-full border px-2.5 py-0.5 text-xs', lv.cls)}>
          风险等级：{lv.text}
        </span>
        {gate?.flags?.length ? (
          <ul className="space-y-1.5">
            {gate.flags.map((f, i) => (
              <li key={i} className="flex gap-1.5 text-xs text-muted-foreground">
                <AlertTriangle className="mt-0.5 size-3 shrink-0 text-gold/70" />
                {f}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">未触发结构性硬风险。</p>
        )}
        <p className="text-[10px] text-text-muted">{gate?.note}</p>
      </div>
    )
  }
  if (meta.group === 'analyst') {
    const report = stages[stageKey as keyof StagesMap] as AnalystReport | undefined
    if (!report) return <p className="text-xs text-text-muted">该角色尚未产出报告</p>
    const s = STANCE_TEXT[report.stance] ?? STANCE_TEXT.neutral
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <span className="font-data text-3xl font-bold text-gold">{report.score}</span>
          <span className={cn('rounded-full border px-2.5 py-0.5 text-xs', s.cls)}>{s.text}</span>
          {report.timing_coef != null && (
            <span className="font-data text-xs text-text-muted">择时系数 {report.timing_coef}</span>
          )}
        </div>
        <p className="text-[13px] leading-relaxed">{report.summary}</p>
        {report.points?.length > 0 && (
          <div>
            <h4 className="mb-1.5 text-xs font-medium text-text-muted">关键论据</h4>
            <ul className="space-y-1.5">
              {report.points.map((p, i) => (
                <li key={i} className="flex gap-1.5 text-xs text-muted-foreground">
                  <ChevronRight className="mt-0.5 size-3 shrink-0 text-gold/60" />
                  {p}
                </li>
              ))}
            </ul>
          </div>
        )}
        {report.theory && (
          <p className="rounded-inner border border-gold/15 bg-gold/5 px-2.5 py-1.5 text-[11px] text-muted-foreground">
            理论依据：{report.theory}
          </p>
        )}
      </div>
    )
  }
  if (meta.group === 'debate') {
    const speech = stages[stageKey as keyof StagesMap] as DebateSpeech | undefined
    if (!speech) return <p className="text-xs text-text-muted">尚未产出</p>
    return (
      <div className="space-y-3">
        <p className="text-[13px] leading-relaxed">{speech.argument}</p>
        {speech.key_points?.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {speech.key_points.map((p, i) => (
              <span
                key={i}
                className={cn(
                  'rounded-full border px-2 py-0.5 text-[10px]',
                  stageKey === 'bull' ? 'border-up/30 text-up' : 'border-down/30 text-down',
                )}
              >
                {p}
              </span>
            ))}
          </div>
        )}
      </div>
    )
  }
  if (stageKey === 'research') {
    const r = stages.research as ResearchConclusion | undefined
    if (!r) return <p className="text-xs text-text-muted">尚未产出</p>
    const s = STANCE_TEXT[r.stance] ?? STANCE_TEXT.neutral
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <span className={cn('rounded-full border px-2.5 py-0.5 text-xs', s.cls)}>{s.text}</span>
          <span className="font-data text-sm text-text-muted">研究置信度 {r.conviction}</span>
        </div>
        <p className="text-[13px] leading-relaxed">{r.summary}</p>
        {r.key_points?.length > 0 && (
          <ul className="space-y-1.5">
            {r.key_points.map((p, i) => (
              <li key={i} className="flex gap-1.5 text-xs text-muted-foreground">
                <ChevronRight className="mt-0.5 size-3 shrink-0 text-gold/60" />
                {p}
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }
  if (stageKey === 'trader') {
    const t = stages.trader as TraderPlan | undefined
    if (!t) return <p className="text-xs text-text-muted">尚未产出</p>
    return (
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Metric label="买入区间" value={t.buy_zone || '—'} />
          <Metric label="建议仓位" value={`${t.position_pct}%`} />
          <Metric label="目标价" value={t.target_price ? `${t.target_price} 元` : '—'} accent="up" />
          <Metric
            label="止损价"
            value={t.stop_loss_price ? `${t.stop_loss_price} 元` : '—'}
            accent="down"
          />
        </div>
        <p className="text-[13px] leading-relaxed">{t.summary}</p>
        {t.theory && <p className="text-[11px] text-text-muted">依据：{t.theory}</p>}
      </div>
    )
  }
  if (meta.group === 'risk') {
    const m = stages[stageKey as keyof StagesMap] as RiskMemberView | undefined
    if (!m) return <p className="text-xs text-text-muted">尚未产出</p>
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-3 text-sm">
          <span className="font-medium">{m.stance}</span>
          <span
            className={cn(
              'font-data',
              m.position_adjust > 0 ? 'text-up' : m.position_adjust < 0 ? 'text-down' : '',
            )}
          >
            仓位调整 {m.position_adjust > 0 ? `+${m.position_adjust}` : m.position_adjust}
          </span>
        </div>
        <p className="text-[13px] leading-relaxed">{m.summary}</p>
      </div>
    )
  }
  if (stageKey === 'chief') {
    return decision ? (
      <ChiefDetail decision={decision} />
    ) : (
      <p className="text-xs text-text-muted">组合经理决策尚未生成</p>
    )
  }
  return null
}

/** 取阶段思考文本（chief 存在 stages.chief.thinking） */
function decision_thinking(stages: StagesMap, key: StageKey): string | undefined {
  if (key === 'chief') return stages.chief?.thinking
  return undefined
}

function Metric({
  label,
  value,
  accent,
}: {
  label: string
  value: string
  accent?: 'up' | 'down'
}) {
  return (
    <div className="rounded-inner border bg-background p-2.5">
      <div className="text-[10px] text-text-muted">{label}</div>
      <div
        className={cn(
          'mt-0.5 font-data text-sm font-semibold',
          accent === 'up' && 'text-up',
          accent === 'down' && 'text-down',
        )}
      >
        {value}
      </div>
    </div>
  )
}

/** 组合经理决策完整视图（抽屉内） */
function ChiefDetail({ decision }: { decision: ChiefDecision }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4">
        <span
          className={cn(
            'rounded-control px-4 py-2 text-lg font-bold tracking-wide',
            RATING_CLS[decision.rating] ?? 'bg-muted',
          )}
        >
          {decision.rating}
          {decision.action ? ` · ${decision.action}` : ''}
        </span>
        <div className="relative size-16">
          <svg viewBox="0 0 64 64" className="size-16 -rotate-90">
            <circle cx="32" cy="32" r="27" fill="none" strokeWidth="6" className="stroke-muted" />
            <motion.circle
              cx="32"
              cy="32"
              r="27"
              fill="none"
              strokeWidth="6"
              strokeLinecap="round"
              className="stroke-gold"
              initial={{ pathLength: 0 }}
              animate={{ pathLength: decision.score / 100 }}
              transition={{ duration: 0.8, ease: 'easeOut' }}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="font-data text-base font-semibold">{decision.score}</span>
            <span className="text-[9px] text-text-muted">综合分</span>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-5 gap-y-1.5 text-xs">
          <DockMetric label="置信度" value={`${decision.confidence}%`} />
          <DockMetric label="建议仓位" value={`${decision.position_pct}%`} />
          <DockMetric label="周期" value={decision.horizon || '—'} />
          {decision.timing_coef != null && (
            <DockMetric label="择时系数" value={`${decision.timing_coef}`} />
          )}
        </div>
      </div>

      {/* 交易价位 */}
      {(decision.target_price || decision.stop_loss_price || decision.buy_zone) && (
        <div className="grid grid-cols-3 gap-2">
          <Metric label="买入区间" value={decision.buy_zone || '—'} />
          <Metric
            label="目标价"
            value={decision.target_price ? `${decision.target_price}` : '—'}
            accent="up"
          />
          <Metric
            label="止损价"
            value={decision.stop_loss_price ? `${decision.stop_loss_price}` : '—'}
            accent="down"
          />
        </div>
      )}

      <p className="rounded-inner border border-gold/20 bg-gold/5 p-3 text-[13px] leading-relaxed">
        {decision.summary}
      </p>
      {decision.entry_note && (
        <p className="text-xs text-muted-foreground">买卖点提示：{decision.entry_note}</p>
      )}

      {/* 操作清单（懒人照做） */}
      {decision.checklist && decision.checklist.length > 0 && (
        <div>
          <h5 className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-gold">
            <Gauge className="size-3.5" /> 操作清单
          </h5>
          <ul className="space-y-1">
            {decision.checklist.map((c, i) => (
              <li key={i} className="flex gap-1.5 text-xs text-muted-foreground">
                <span className="mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-full border border-gold/40 text-[8px] text-gold">
                  {i + 1}
                </span>
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h5 className="mb-1.5 text-xs font-medium text-up">支撑理由</h5>
        <ul className="space-y-1">
          {decision.reasons.map((r, i) => (
            <li key={i} className="flex gap-1.5 text-xs text-muted-foreground">
              <CheckCircle2 className="mt-0.5 size-3 shrink-0 text-up/70" />
              {r}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h5 className="mb-1.5 text-xs font-medium text-down">风险盯防</h5>
        <ul className="space-y-1">
          {decision.risks.map((r, i) => (
            <li key={i} className="flex gap-1.5 text-xs text-muted-foreground">
              <AlertTriangle className="mt-0.5 size-3 shrink-0 text-down/70" />
              {r}
            </li>
          ))}
        </ul>
      </div>

      {/* 理论附录 */}
      {decision.theory_refs && decision.theory_refs.length > 0 && (
        <div>
          <h5 className="mb-1.5 text-xs font-medium text-text-muted">本次引用的理论框架</h5>
          <div className="flex flex-wrap gap-1.5">
            {decision.theory_refs.map((t, i) => (
              <span
                key={i}
                className="rounded-full border border-gold/25 bg-gold/5 px-2 py-0.5 text-[10px] text-muted-foreground"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      <p className="text-[10px] text-text-muted">
        AI 生成内容仅供研究参考，不构成投资建议 · 入市有风险，决策需谨慎
      </p>
    </div>
  )
}

/* ============================================================
 * 抽屉内容：历史诊断记录
 * ============================================================ */
function HistoryPanel({
  symbol,
  activeRunId,
  onPick,
}: {
  symbol: string
  activeRunId: number | null
  onPick: (id: number) => void
}) {
  const [items, setItems] = useState<DiagnosisRun[] | null>(null)

  useEffect(() => {
    fetchDiagnosisHistory(symbol, 30)
      .then(setItems)
      .catch(() => setItems([]))
  }, [symbol])

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-2">
        <History className="size-4 text-gold" />
        <h3 className="text-sm font-semibold">历史诊断记录</h3>
      </header>

      {items === null && (
        <div className="flex justify-center py-10">
          <Loader2 className="size-5 animate-spin text-text-muted" />
        </div>
      )}
      {items?.length === 0 && <p className="py-8 text-center text-xs text-text-muted">暂无历史记录</p>}

      <div className="space-y-2">
        {items?.map((r) => {
          const result = r.result as Partial<ChiefDecision>
          const rating = result?.rating
          return (
            <button
              key={r.run_id}
              onClick={() => onPick(r.run_id)}
              className={cn(
                'flex w-full items-center gap-3 rounded-card border p-3 text-left transition-colors hover:border-gold/40',
                r.run_id === activeRunId && 'border-gold/50 bg-gold/5',
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-data text-xs">{r.created_at}</span>
                  {result?.mode && (
                    <span className="rounded-full bg-muted px-1.5 py-px text-[10px] text-muted-foreground">
                      {result.mode === 'quick' ? '快速' : '深度'}
                    </span>
                  )}
                  {r.status === 'failed' && (
                    <span className="rounded-full bg-destructive/10 px-1.5 py-px text-[10px] text-destructive">
                      失败
                    </span>
                  )}
                  {r.status === 'running' && (
                    <span className="rounded-full bg-gold/10 px-1.5 py-px text-[10px] text-gold">进行中</span>
                  )}
                </div>
                <div className="mt-0.5 text-[11px] text-text-muted">
                  {r.model} · {r.cost_seconds ? `${r.cost_seconds}s` : '—'}
                </div>
              </div>
              {rating && (
                <span
                  className={cn(
                    'rounded-control px-2 py-1 text-xs font-bold',
                    RATING_CLS[rating] ?? 'bg-muted',
                  )}
                >
                  {rating}
                </span>
              )}
              {r.status === 'done' && (
                <a
                  href={diagnosisExportUrl(r.run_id)}
                  download
                  title="下载报告"
                  onClick={(e) => e.stopPropagation()}
                  className="flex size-7 items-center justify-center rounded-control border text-muted-foreground transition-colors hover:text-gold"
                >
                  <Download className="size-3.5" />
                </a>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
