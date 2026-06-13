import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import { Fragment, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  BrainCircuit,
  Briefcase,
  Check,
  ChevronDown,
  Download,
  Pencil,
  Plus,
  ShieldAlert,
  Sparkles,
  Trash2,
  Upload,
  Wallet,
  X,
} from 'lucide-react'

import { Portal } from '@/components/common/Portal'
import { DiagnosisFlow } from '@/features/diagnosis/DiagnosisFlow'
import {
  addHolding,
  ApiError,
  diagnoseHolding,
  fetchHoldings,
  importHoldings,
  removeHolding,
  searchStocks,
  setTotalCapital,
  updateHolding,
  type DiagnosisMode,
  type HoldingAi,
  type HoldingItem,
  type PortfolioOverview,
  type SearchResult,
} from '@/lib/api'
import { downloadCsv, parseCsv } from '@/lib/csv'
import { formatAmount, formatPct, formatPrice, pctColor } from '@/lib/format'
import { cn } from '@/lib/utils'

/* ============================================================
 * 持仓诊断页（M6）
 *
 * 三块结构：
 *   1. 组合总览：总市值/总成本/总盈亏/今日盈亏（红涨绿跌的 A 股语义色）
 *   2. 持仓明细表：现价/涨跌/成本/股数/市值/浮动盈亏/今日盈亏/仓位占比
 *   3. 「AI 割守补」：复用多角色诊股工作流，把持仓成本与浮亏注入
 *      首席决策官，评级直接翻译成 割（卖）/ 守（持有）/ 补（加仓）
 * ============================================================ */

/** 评级 → 割守补操作语义（持仓者视角的翻译） */
const RATING_ACTION: Record<string, { text: string; cls: string }> = {
  强烈买入: { text: '补', cls: 'bg-up text-white' },
  买入: { text: '补', cls: 'bg-up/80 text-white' },
  持有: { text: '守', cls: 'bg-gold/80 text-black' },
  减仓: { text: '割', cls: 'bg-down/70 text-white' },
  卖出: { text: '割', cls: 'bg-down text-white' },
}

/** 割守补动作 → 配色（持仓表内联徽章） */
const ACTION_CLS: Record<string, string> = {
  补: 'bg-up text-white',
  守: 'bg-gold/80 text-black',
  割: 'bg-down text-white',
}

/** 风险闸门级别 → 文案与配色 */
const RISK_META: Record<string, { text: string; cls: string }> = {
  pass: { text: '风险可控', cls: 'text-down border-down/30' },
  warn: { text: '风险警示', cls: 'text-gold border-gold/40' },
  block: { text: '风险否决', cls: 'text-up border-up/40' },
}

export function PortfolioPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['holdings'],
    queryFn: fetchHoldings,
    refetchInterval: 30_000, // 盘中半分钟刷一次市值与盈亏
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['holdings'] })

  // 编辑/添加弹窗与 AI 诊断工作流的开关状态
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<HoldingItem | null>(null)
  const [diagTarget, setDiagTarget] = useState<{ symbol: string; name: string } | null>(null)
  // 当前展开 AI 详情的行 id（一次只展开一行）
  const [expandedId, setExpandedId] = useState<number | null>(null)
  // CSV 导入：隐藏 file input 引用 + 导入结果提示
  const fileRef = useRef<HTMLInputElement>(null)
  const [importMsg, setImportMsg] = useState('')

  const remove = useMutation({ mutationFn: removeHolding, onSuccess: invalidate })

  const items = data?.items ?? []
  const ov = data?.overview

  // 导出 CSV：含持仓基础字段 + AI 研判（目标/止损/操作建议）
  const exportCsv = () => {
    const header = [
      '代码',
      '名称',
      '股数',
      '成本价',
      '现价',
      '市值',
      '浮动盈亏',
      '盈亏比例',
      '仓位%',
      'AI操作',
      '目标价',
      '止损价',
      '备注',
    ]
    const rows = items.map((it) => [
      it.symbol,
      it.name,
      it.shares,
      it.cost_price,
      it.price ?? '',
      it.market_value,
      it.pnl ?? '',
      it.pnl_pct ?? '',
      it.weight,
      it.ai?.action || '',
      it.ai?.target_price || '',
      it.ai?.stop_loss_price || '',
      it.note ?? '',
    ])
    downloadCsv(`持仓_${new Date().toISOString().slice(0, 10)}.csv`, [header, ...rows])
  }

  // 导入 CSV：解析「代码,股数,成本价,备注」→ 批量 upsert
  const onImportFile = async (file: File) => {
    setImportMsg('')
    try {
      const text = await file.text()
      const rows = parseCsv(text)
      const parsed = rows
        .map((r) => ({
          symbol: (r[0] ?? '').trim(),
          shares: Number(r[1] ?? 0),
          cost_price: Number(r[2] ?? 0),
          note: (r[3] ?? '').trim(),
        }))
        // 跳过表头行（首列不像 6 位代码）与空行
        .filter((r) => /^\d{6}$/.test(r.symbol) && r.shares > 0 && r.cost_price > 0)
      if (parsed.length === 0) {
        setImportMsg('未解析到有效行（格式：代码,股数,成本价,备注，代码须为6位数字）')
        return
      }
      const res = await importHoldings(parsed)
      invalidate()
      const failNote = res.failed.length ? `，失败 ${res.failed.length}` : ''
      setImportMsg(`导入完成：新增 ${res.added}，更新 ${res.updated}${failNote}`)
    } catch (e) {
      setImportMsg(e instanceof ApiError ? e.message : '导入失败，请检查文件格式')
    }
  }

  return (
    <div className="space-y-4">
      {/* ---- 账户资金与仓位（填写总资金后解锁现金/仓位分析，并喂给 AI） ---- */}
      <CapitalPanel ov={ov} onSaved={invalidate} />

      {/* ---- 组合总览 ---- */}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <OverviewCard label="总市值" value={ov ? formatAmount(ov.total_value) : '--'} />
        <OverviewCard label="总成本" value={ov ? formatAmount(ov.total_cost) : '--'} />
        <OverviewCard
          label="浮动盈亏"
          value={ov ? `${ov.total_pnl >= 0 ? '+' : '-'}${formatAmount(Math.abs(ov.total_pnl))}` : '--'}
          sub={ov ? formatPct(ov.total_pnl_pct) : undefined}
          tone={ov ? ov.total_pnl : 0}
        />
        <OverviewCard
          label="今日盈亏"
          value={ov ? `${ov.day_pnl >= 0 ? '+' : '-'}${formatAmount(Math.abs(ov.day_pnl))}` : '--'}
          tone={ov ? ov.day_pnl : 0}
        />
        <OverviewCard label="持仓数" value={ov ? String(ov.count) : '--'} sub="只" />
      </section>

      {/* ---- 工具行 ---- */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-text-muted">
          录入真实持仓后，AI 可结合你的成本价给出「割 / 守 / 补」推演 · 数据每 30 秒刷新
        </p>
        <div className="flex items-center gap-2">
          {importMsg && <span className="text-[11px] text-text-muted">{importMsg}</span>}
          {/* 隐藏的文件选择器（导入 CSV） */}
          <input
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) onImportFile(f)
              e.target.value = '' // 允许重复选同一文件
            }}
          />
          <button
            onClick={() => fileRef.current?.click()}
            title="导入 CSV（列：代码,股数,成本价,备注）"
            className="flex items-center gap-1.5 rounded-control border px-3 py-2 text-xs text-text-muted transition-colors hover:text-foreground"
          >
            <Upload className="size-3.5" /> 导入
          </button>
          <button
            onClick={exportCsv}
            disabled={items.length === 0}
            title="导出当前持仓为 CSV"
            className="flex items-center gap-1.5 rounded-control border px-3 py-2 text-xs text-text-muted transition-colors hover:text-foreground disabled:opacity-40"
          >
            <Download className="size-3.5" /> 导出
          </button>
          <button
            onClick={() => {
              setEditing(null)
              setFormOpen(true)
            }}
            className="flex items-center gap-1.5 rounded-control bg-gold px-3.5 py-2 text-xs font-medium text-black transition-opacity hover:opacity-90"
          >
            <Plus className="size-3.5" /> 录入持仓
          </button>
        </div>
      </div>

      {/* ---- 持仓明细表 ---- */}
      <section className="overflow-hidden rounded-card border bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-[11px] text-text-muted">
              <th className="w-8 px-1 py-2.5" />
              <th className="px-3 py-2.5 font-medium">名称 / 代码</th>
              <th className="px-3 py-2.5 text-right font-medium">现价</th>
              <th className="px-3 py-2.5 text-right font-medium">涨跌幅</th>
              <th className="hidden px-3 py-2.5 text-right font-medium md:table-cell">成本价</th>
              <th className="px-3 py-2.5 text-right font-medium">市值</th>
              <th className="px-3 py-2.5 text-right font-medium">浮动盈亏</th>
              <th className="hidden px-3 py-2.5 text-right font-medium lg:table-cell">仓位</th>
              {/* AI 内联字段：操作建议 + 目标/止损价 */}
              <th className="px-3 py-2.5 text-center font-medium text-gold/90">AI 研判</th>
              <th className="hidden px-3 py-2.5 text-right font-medium lg:table-cell">目标 / 止损</th>
              <th className="px-3 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {isLoading && (
              <tr>
                <td colSpan={11} className="px-4 py-10 text-center text-text-muted">
                  加载中…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td colSpan={11} className="px-4 py-14 text-center">
                  <Briefcase className="mx-auto mb-2 size-6 text-text-muted/40" />
                  <p className="text-xs text-text-muted">
                    还没有持仓记录 · 点击右上角「录入持仓」，让 AI 帮你诊断每只票该割该守还是该补
                  </p>
                </td>
              </tr>
            )}
            {items.map((it) => {
              const expanded = expandedId === it.id
              return (
                <Fragment key={it.id}>
                  <tr
                    className={cn(
                      'cursor-pointer transition-colors hover:bg-accent/40',
                      expanded && 'bg-accent/30',
                    )}
                    onClick={() => setExpandedId(expanded ? null : it.id)}
                  >
                    {/* 展开箭头 */}
                    <td className="px-1 py-2.5 text-center">
                      <ChevronDown
                        className={cn(
                          'mx-auto size-3.5 text-text-muted transition-transform',
                          expanded && 'rotate-180 text-gold',
                        )}
                      />
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="text-[13px]">{it.name}</div>
                      <div className="font-data text-[10px] text-text-muted">{it.symbol}</div>
                    </td>
                    <td className={cn('px-3 py-2.5 text-right font-data', pctColor(it.pct_change ?? 0))}>
                      {it.price != null ? formatPrice(it.price) : '--'}
                    </td>
                    <td className={cn('px-3 py-2.5 text-right font-data', pctColor(it.pct_change ?? 0))}>
                      {it.pct_change != null ? formatPct(it.pct_change) : '--'}
                    </td>
                    <td className="hidden px-3 py-2.5 text-right font-data text-xs text-muted-foreground md:table-cell">
                      {formatPrice(it.cost_price)}
                    </td>
                    <td className="px-3 py-2.5 text-right font-data text-xs">
                      {formatAmount(it.market_value)}
                    </td>
                    <td className={cn('px-3 py-2.5 text-right font-data', pctColor(it.pnl ?? 0))}>
                      {it.pnl != null ? (
                        <div>
                          <div className="text-[13px]">
                            {it.pnl >= 0 ? '+' : '-'}
                            {formatAmount(Math.abs(it.pnl))}
                          </div>
                          <div className="text-[10px] opacity-80">{formatPct(it.pnl_pct ?? 0)}</div>
                        </div>
                      ) : (
                        '--'
                      )}
                    </td>
                    <td className="hidden px-3 py-2.5 text-right font-data text-xs text-muted-foreground lg:table-cell">
                      {it.weight.toFixed(1)}%
                    </td>
                    {/* AI 操作建议（割/守/补 + 评分） */}
                    <td className="px-3 py-2.5 text-center">
                      <AiActionCell ai={it.ai} />
                    </td>
                    {/* 目标价 / 止损价 */}
                    <td className="hidden px-3 py-2.5 text-right font-data text-[11px] lg:table-cell">
                      {it.ai && (it.ai.target_price || it.ai.stop_loss_price) ? (
                        <div className="leading-tight">
                          <div className="text-up">{it.ai.target_price || '—'}</div>
                          <div className="text-down">{it.ai.stop_loss_price || '—'}</div>
                        </div>
                      ) : (
                        <span className="text-text-muted">--</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          title="AI 割守补推演（结合你的成本价）"
                          className="flex items-center gap-1 rounded-control border border-gold/40 px-2 py-1 text-[11px] text-gold transition-colors hover:bg-gold/10"
                          onClick={(e) => {
                            e.stopPropagation()
                            setDiagTarget({ symbol: it.symbol, name: it.name })
                          }}
                        >
                          <BrainCircuit className="size-3" /> AI 诊断
                        </button>
                        <button
                          title="编辑（加减仓后更新股数与摊薄成本）"
                          className="rounded-control p-1.5 text-text-muted transition-colors hover:bg-accent hover:text-foreground"
                          onClick={(e) => {
                            e.stopPropagation()
                            setEditing(it)
                            setFormOpen(true)
                          }}
                        >
                          <Pencil className="size-3.5" />
                        </button>
                        <button
                          title="删除（清仓后移除）"
                          className="rounded-control p-1.5 text-text-muted transition-colors hover:bg-up/10 hover:text-up"
                          onClick={(e) => {
                            e.stopPropagation()
                            remove.mutate(it.id)
                          }}
                        >
                          <Trash2 className="size-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {/* 展开行：AI 详情 + 被收起的次要字段 */}
                  {expanded && (
                    <tr className="bg-accent/20">
                      <td />
                      <td colSpan={10} className="px-3 pt-1 pb-3">
                        <HoldingDetail
                          it={it}
                          onDiagnose={() => setDiagTarget({ symbol: it.symbol, name: it.name })}
                          onOpenStock={() => navigate(`/stock/${it.symbol}`)}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </section>

      {/* ---- 录入/编辑弹窗 ---- */}
      <AnimatePresence>
        {formOpen && (
          <HoldingFormDialog
            editing={editing}
            onClose={() => setFormOpen(false)}
            onSaved={() => {
              setFormOpen(false)
              invalidate()
            }}
          />
        )}
      </AnimatePresence>

      {/* ---- AI 割守补工作流（带持仓上下文，快/深模式由工作流内选择） ---- */}
      {diagTarget && (
        <DiagnosisFlow
          symbol={diagTarget.symbol}
          name={diagTarget.name}
          open
          onClose={() => {
            setDiagTarget(null)
            invalidate() // 诊断后刷新持仓表的内联 AI 字段
          }}
          startFn={(mode: DiagnosisMode) => diagnoseHolding(diagTarget.symbol, mode)}
        />
      )}
    </div>
  )
}

/** 表格内联：AI 操作建议徽章（割/守/补 + 评分）。未诊断显示占位。 */
function AiActionCell({ ai }: { ai: HoldingAi | null }) {
  if (!ai || !ai.rating) {
    return <span className="text-[10px] text-text-muted/70">未诊断</span>
  }
  // action 为空（非持仓诊断）时回退用评级映射
  const action = ai.action || RATING_ACTION[ai.rating]?.text || '守'
  return (
    <div className="flex flex-col items-center gap-0.5">
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-control px-2 py-0.5 text-[11px] font-bold',
          ACTION_CLS[action] ?? 'bg-muted',
        )}
      >
        {action}
        {ai.risk_level === 'block' && <ShieldAlert className="size-3" />}
      </span>
      <span className="font-data text-[9px] text-text-muted">{ai.score} 分</span>
    </div>
  )
}

/** 距离百分比着色：到目标价为正（红/涨），到止损价为负（绿/跌）。 */
function distanceText(target: number, price: number | null): string {
  if (!target || !price) return '—'
  const pct = (target / price - 1) * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
}

/** 展开行：AI 完整研判 + 被收起的次要字段（持股数/今日盈亏/诊断时间）。 */
function HoldingDetail({
  it,
  onDiagnose,
  onOpenStock,
}: {
  it: HoldingItem
  onDiagnose: () => void
  onOpenStock: () => void
}) {
  const ai = it.ai
  return (
    <div className="space-y-3 rounded-card border border-border/60 bg-card/60 p-3">
      {/* 第一排：基础补充字段 */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
        <DetailField label="持股数量" value={`${it.shares.toLocaleString()} 股`} />
        <DetailField
          label="今日盈亏"
          value={
            it.day_pnl != null
              ? `${it.day_pnl >= 0 ? '+' : '-'}${formatAmount(Math.abs(it.day_pnl))}`
              : '--'
          }
          tone={it.day_pnl ?? 0}
        />
        <DetailField
          label={it.cap_weight != null ? '占持仓 / 占总资金' : '仓位占比'}
          value={
            it.cap_weight != null
              ? `${it.weight.toFixed(1)}% / ${it.cap_weight.toFixed(1)}%`
              : `${it.weight.toFixed(1)}%`
          }
        />
        <DetailField label="建议仓位" value={ai?.position_pct != null ? `${ai.position_pct}%` : '--'} />
      </div>

      {/* AI 研判区块 */}
      {ai && ai.rating ? (
        <div className="rounded-inner border border-gold/20 bg-gold/5 p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <BrainCircuit className="size-3.5 text-gold" />
            <span className="text-xs font-semibold text-gold">AI 研判</span>
            <span
              className={cn(
                'rounded-control px-2 py-0.5 text-[11px] font-bold',
                ACTION_CLS[ai.action || RATING_ACTION[ai.rating]?.text || '守'] ?? 'bg-muted',
              )}
            >
              {ai.action || RATING_ACTION[ai.rating]?.text}
            </span>
            <span className="text-[11px] text-muted-foreground">评级 {ai.rating}</span>
            {ai.risk_level && RISK_META[ai.risk_level] && (
              <span
                className={cn(
                  'rounded-full border px-1.5 py-0.5 text-[10px]',
                  RISK_META[ai.risk_level].cls,
                )}
              >
                {RISK_META[ai.risk_level].text}
              </span>
            )}
            <span className="ml-auto text-[10px] text-text-muted">
              {ai.mode === 'quick' ? '快速' : '深度'} · {ai.updated_at}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
            <DetailField label="综合评分" value={`${ai.score} 分`} accent />
            <DetailField
              label="目标价（空间）"
              value={
                ai.target_price
                  ? `${ai.target_price}（${distanceText(ai.target_price, it.price)}）`
                  : '--'
              }
              tone={1}
            />
            <DetailField
              label="止损价（空间）"
              value={
                ai.stop_loss_price
                  ? `${ai.stop_loss_price}（${distanceText(ai.stop_loss_price, it.price)}）`
                  : '--'
              }
              tone={-1}
            />
            <DetailField
              label="建议 vs 当前仓位"
              value={
                ai.position_pct != null ? `${ai.position_pct}% / ${it.weight.toFixed(0)}%` : '--'
              }
            />
          </div>
        </div>
      ) : (
        <div className="rounded-inner border border-dashed border-border bg-background/50 p-3 text-center text-xs text-text-muted">
          这只票还没有 AI 诊断记录 · 点下方「AI 诊断」生成割守补建议、目标价与止损价
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={onDiagnose}
          className="flex items-center gap-1.5 rounded-control bg-gold px-3 py-1.5 text-[11px] font-medium text-black transition-opacity hover:opacity-90"
        >
          <Sparkles className="size-3.5" /> {ai && ai.rating ? '重新诊断（可选快/深）' : 'AI 诊断'}
        </button>
        <button
          onClick={onOpenStock}
          className="ml-auto text-[11px] text-text-muted transition-colors hover:text-gold"
        >
          查看个股详情 →
        </button>
      </div>
      {it.note && <p className="text-[11px] text-text-muted">备注：{it.note}</p>}
    </div>
  )
}

/** 展开详情里的字段小卡 */
function DetailField({
  label,
  value,
  tone,
  accent,
}: {
  label: string
  value: string
  tone?: number
  accent?: boolean
}) {
  return (
    <div>
      <div className="text-[10px] text-text-muted">{label}</div>
      <div
        className={cn(
          'mt-0.5 font-data text-xs font-medium',
          accent && 'text-gold',
          tone !== undefined && pctColor(tone),
        )}
      >
        {value}
      </div>
    </div>
  )
}

/** 账户资金面板：填写总资金 → 解锁现金/仓位分析，并作为 AI 集中度判断的依据。
 *
 * 未设置：引导式输入；已设置：总资金 + 仓位进度条 + 可用现金，可随时改。
 * 仓位色：≤50% 中性、50~85% 偏黄、>85%（接近满仓/超配）偏红警示。 */
function CapitalPanel({
  ov,
  onSaved,
}: {
  ov: PortfolioOverview | undefined
  onSaved: () => void
}) {
  const hasCapital = ov?.total_capital != null && ov.total_capital > 0
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const save = useMutation({
    mutationFn: (v: number) => setTotalCapital(v),
    onSuccess: () => {
      setEditing(false)
      onSaved()
    },
  })

  // 万元口径输入更符合习惯：展示/输入用万元，落库用元
  const beginEdit = () => {
    setDraft(hasCapital ? String((ov!.total_capital as number) / 1e4) : '')
    setEditing(true)
  }
  const submit = () => {
    const wan = Number(draft)
    if (!Number.isFinite(wan) || wan < 0) return
    save.mutate(Math.round(wan * 1e4))
  }

  // 编辑态 / 未设置态：输入框引导
  if (editing || !hasCapital) {
    return (
      <section className="rounded-card border bg-card p-4">
        <div className="flex flex-wrap items-center gap-3">
          <Wallet className="size-5 text-gold" />
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold">账户总资金</h3>
            <p className="text-[11px] text-text-muted">
              填写你投入股市的资金总额，系统据此算出现金仓位与个股占总资金比例，
              并让 AI 结合仓位集中度给出加减仓建议
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <input
                autoFocus
                type="number"
                min={0}
                step={1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && submit()}
                placeholder="如 50"
                className="w-32 rounded-control border bg-background py-2 pr-10 pl-3 font-data text-sm outline-none placeholder:text-text-muted focus:border-ring/50"
              />
              <span className="absolute top-1/2 right-3 -translate-y-1/2 text-xs text-text-muted">
                万元
              </span>
            </div>
            <button
              onClick={submit}
              disabled={save.isPending || draft === ''}
              className="flex items-center gap-1 rounded-control bg-gold px-3 py-2 text-xs font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              <Check className="size-3.5" /> {save.isPending ? '保存中' : '保存'}
            </button>
            {hasCapital && (
              <button
                onClick={() => setEditing(false)}
                className="rounded-control border px-3 py-2 text-xs text-text-muted transition-colors hover:text-foreground"
              >
                取消
              </button>
            )}
          </div>
        </div>
      </section>
    )
  }

  // 已设置态：总资金 + 仓位进度 + 现金
  const invested = ov!.invested_ratio ?? 0
  const cash = ov!.cash ?? 0
  const barColor = invested > 85 ? 'bg-up' : invested > 50 ? 'bg-gold' : 'bg-foreground/40'
  const overAllocated = cash < 0
  return (
    <section className="rounded-card border bg-card p-4">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
        {/* 总资金 */}
        <div className="flex items-center gap-2">
          <Wallet className="size-5 text-gold" />
          <div>
            <div className="text-[11px] text-text-muted">账户总资金</div>
            <div className="font-data text-xl font-semibold">
              {formatAmount(ov!.total_capital as number)}
            </div>
          </div>
          <button
            onClick={beginEdit}
            title="修改总资金"
            className="ml-1 rounded-control p-1.5 text-text-muted transition-colors hover:bg-accent hover:text-foreground"
          >
            <Pencil className="size-3.5" />
          </button>
        </div>

        {/* 仓位进度条 */}
        <div className="min-w-[200px] flex-1">
          <div className="mb-1 flex items-center justify-between text-[11px]">
            <span className="text-text-muted">当前仓位</span>
            <span className="font-data font-medium">{invested.toFixed(0)}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className={cn('h-full rounded-full transition-all', barColor)}
              style={{ width: `${Math.min(100, invested)}%` }}
            />
          </div>
        </div>

        {/* 可用现金 */}
        <div>
          <div className="text-[11px] text-text-muted">可用现金</div>
          <div className={cn('font-data text-lg font-semibold', overAllocated && 'text-up')}>
            {overAllocated ? '-' : ''}
            {formatAmount(Math.abs(cash))}
          </div>
          {overAllocated && (
            <div className="text-[10px] text-up">持仓市值已超总资金，请核对</div>
          )}
        </div>
      </div>
    </section>
  )
}

/** 总览指标卡：tone>0 红（盈利）、<0 绿（亏损）——A 股语义色 */
function OverviewCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string
  value: string
  sub?: string
  tone?: number
}) {
  return (
    <div className="rounded-card border bg-card p-4">
      <div className="text-[11px] text-text-muted">{label}</div>
      <div
        className={cn(
          'mt-1 font-data text-xl font-semibold',
          tone !== undefined && pctColor(tone),
        )}
      >
        {value}
        {sub && <span className="ml-1 text-xs font-normal text-text-muted">{sub}</span>}
      </div>
    </div>
  )
}

/** 录入/编辑持仓弹窗：编辑时股票不可改（删了重录），只改股数/成本/备注 */
function HoldingFormDialog({
  editing,
  onClose,
  onSaved,
}: {
  editing: HoldingItem | null
  onClose: () => void
  onSaved: () => void
}) {
  // 股票选择（仅录入模式）：搜索下拉选定后锁定
  const [keyword, setKeyword] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [picked, setPicked] = useState<SearchResult | null>(
    editing ? { symbol: editing.symbol, name: editing.name, market: '', pinyin: '' } : null,
  )
  const [shares, setShares] = useState(editing ? String(editing.shares) : '')
  const [costPrice, setCostPrice] = useState(editing ? String(editing.cost_price) : '')
  const [note, setNote] = useState(editing?.note ?? '')
  const [error, setError] = useState('')

  const search = useMutation({ mutationFn: searchStocks, onSuccess: setResults })
  const save = useMutation({
    mutationFn: async () => {
      const body = {
        shares: Number(shares),
        cost_price: Number(costPrice),
        note: note.trim(),
      }
      if (editing) return updateHolding(editing.id, body)
      return addHolding({ symbol: picked!.symbol, ...body })
    },
    onSuccess: onSaved,
    onError: (e) => setError(e instanceof ApiError ? e.message : '保存失败'),
  })

  const valid = picked !== null && Number(shares) > 0 && Number(costPrice) > 0

  return (
    <Portal>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
        onClick={onClose}
      >
        <motion.div
          initial={{ scale: 0.95, y: 12 }}
          animate={{ scale: 1, y: 0 }}
          exit={{ scale: 0.95, y: 12 }}
          className="w-full max-w-md rounded-card border bg-card p-5 shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-semibold">{editing ? '编辑持仓' : '录入持仓'}</h3>
            <button
              onClick={onClose}
              className="flex size-7 items-center justify-center rounded-control border text-muted-foreground transition-colors hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          </div>

          <div className="space-y-3">
            {/* 股票选择 */}
            <div>
              <label className="mb-1 block text-[11px] text-text-muted">股票</label>
              {picked ? (
                <div className="flex items-center justify-between rounded-control border bg-background px-3 py-2 text-sm">
                  <span>
                    {picked.name}
                    <span className="ml-2 font-data text-xs text-text-muted">{picked.symbol}</span>
                  </span>
                  {!editing && (
                    <button
                      className="text-[11px] text-text-muted transition-colors hover:text-gold"
                      onClick={() => {
                        setPicked(null)
                        setKeyword('')
                      }}
                    >
                      重选
                    </button>
                  )}
                </div>
              ) : (
                <div className="relative">
                  <input
                    autoFocus
                    value={keyword}
                    onChange={(e) => {
                      const v = e.target.value
                      setKeyword(v)
                      if (v.trim()) search.mutate(v.trim())
                      else setResults([])
                    }}
                    placeholder="输入代码 / 拼音 / 名称搜索"
                    className="w-full rounded-control border bg-background px-3 py-2 text-sm outline-none placeholder:text-text-muted focus:border-ring/50"
                  />
                  {results.length > 0 && (
                    <ul className="absolute z-10 mt-1 max-h-52 w-full overflow-auto rounded-inner border bg-popover shadow-lg">
                      {results.map((r) => (
                        <li key={r.symbol}>
                          <button
                            className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-accent"
                            onClick={() => {
                              setPicked(r)
                              setResults([])
                            }}
                          >
                            <span>
                              {r.name}
                              <span className="ml-2 font-data text-xs text-text-muted">{r.symbol}</span>
                            </span>
                            <span className="text-[10px] text-text-muted">{r.market}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>

            {/* 股数 + 成本价 */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-[11px] text-text-muted">持股数量（股）</label>
                <input
                  type="number"
                  min={100}
                  step={100}
                  value={shares}
                  onChange={(e) => setShares(e.target.value)}
                  placeholder="如 1000"
                  className="w-full rounded-control border bg-background px-3 py-2 font-data text-sm outline-none placeholder:text-text-muted focus:border-ring/50"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] text-text-muted">摊薄成本价（元）</label>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={costPrice}
                  onChange={(e) => setCostPrice(e.target.value)}
                  placeholder="如 12.50"
                  className="w-full rounded-control border bg-background px-3 py-2 font-data text-sm outline-none placeholder:text-text-muted focus:border-ring/50"
                />
              </div>
            </div>

            {/* 备注 */}
            <div>
              <label className="mb-1 block text-[11px] text-text-muted">备注（可选）</label>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="如：2024-03 建仓，跌破 35 止损"
                className="w-full rounded-control border bg-background px-3 py-2 text-sm outline-none placeholder:text-text-muted focus:border-ring/50"
              />
            </div>

            {error && (
              <p className="rounded-control border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error}
              </p>
            )}

            <button
              disabled={!valid || save.isPending}
              onClick={() => save.mutate()}
              className="w-full rounded-control bg-gold py-2.5 text-sm font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {save.isPending ? '保存中…' : editing ? '保存修改' : '确认录入'}
            </button>
          </div>
        </motion.div>
      </motion.div>
    </Portal>
  )
}

/** 供外部使用的评级→割守补映射（诊断结果列表/历史页共用） */
export { RATING_ACTION }
