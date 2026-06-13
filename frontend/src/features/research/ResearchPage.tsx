import { useMutation, useQuery } from '@tanstack/react-query'
import { Download, FileText, Loader2, Play, Search, X } from 'lucide-react'
import { useState } from 'react'

import { Portal } from '@/components/common/Portal'
import { Button } from '@/components/ui/button'
import {
  diagnosisExportUrl,
  fetchDiagnosisLibrary,
  fetchDiagnosisRun,
  runReport,
  type DiagnosisRun,
} from '@/lib/api'
import { cn } from '@/lib/utils'

/* ============================================================
 * AI 研报库（顶级页）
 *
 * 定位：所有 AI 诊股记录的归档中心 —— 不论是个股页手动诊断、
 *      持仓诊断，还是盘后定时研报批量生成的，都沉淀在这里可回看。
 * 能力：分页 + 按股票代码/状态过滤；点开看完整决策；一键下载 MD。
 *      右上「立即生成研报」手动触发一次对自选+持仓的批量诊断。
 * ============================================================ */

const RATING_CLS: Record<string, string> = {
  强烈买入: 'bg-up text-white',
  买入: 'bg-up/80 text-white',
  持有: 'bg-muted text-foreground',
  减仓: 'bg-down/70 text-white',
  卖出: 'bg-down text-white',
}

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'done', label: '已完成' },
  { value: 'running', label: '进行中' },
  { value: 'failed', label: '失败' },
]

const PAGE_SIZE = 15

export function ResearchPage() {
  const [page, setPage] = useState(1)
  const [symbol, setSymbol] = useState('')
  const [symbolInput, setSymbolInput] = useState('')
  const [status, setStatus] = useState('')
  const [openId, setOpenId] = useState<number | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['diag-library', page, symbol, status],
    queryFn: () => fetchDiagnosisLibrary({ page, pageSize: PAGE_SIZE, symbol, status }),
    // 列表数据轻量，保持适度新鲜（手动生成研报后能较快看到）
    staleTime: 10_000,
  })

  // 手动触发定时研报（后台跑，提示用户稍后刷新）
  const trigger = useMutation({
    mutationFn: runReport,
    onSuccess: () => setTimeout(() => refetch(), 3000),
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const applySymbol = () => {
    setSymbol(symbolInput.trim())
    setPage(1)
  }

  return (
    <div className="space-y-4">
      {/* ---- 头部：标题 + 说明 + 手动生成 ---- */}
      <section className="flex flex-wrap items-end justify-between gap-3 rounded-card border bg-card p-5">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <FileText className="size-5 text-gold" /> AI 研报库
          </h1>
          <p className="mt-1 text-[12px] text-text-muted">
            所有 AI 诊股记录的归档中心 · 个股诊断 / 持仓诊断 / 盘后定时研报均沉淀于此
          </p>
        </div>
        <div className="flex items-center gap-2">
          {trigger.isSuccess && (
            <span className="text-[11px] text-up">已在后台生成，稍后自动刷新…</span>
          )}
          <Button
            size="sm"
            variant="primary"
            disabled={trigger.isPending}
            onClick={() => trigger.mutate()}
            title="立即对自选+持仓批量 AI 诊断，结果归档到本页"
          >
            {trigger.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Play className="size-3.5" />
            )}
            立即生成研报
          </Button>
        </div>
      </section>

      {/* ---- 过滤条 ---- */}
      <section className="flex flex-wrap items-center gap-2 rounded-card border bg-card px-4 py-3">
        <div className="flex items-center gap-1.5">
          <input
            value={symbolInput}
            onChange={(e) => setSymbolInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && applySymbol()}
            placeholder="按代码筛选，如 600519"
            className="h-8 w-44 rounded-control border bg-background px-2.5 font-data text-[12px] focus:border-ring focus:outline-none"
          />
          <button
            onClick={applySymbol}
            className="flex h-8 items-center gap-1 rounded-control border px-2.5 text-[12px] text-text-muted transition-colors hover:text-foreground"
          >
            <Search className="size-3.5" /> 筛选
          </button>
          {symbol && (
            <button
              onClick={() => {
                setSymbol('')
                setSymbolInput('')
                setPage(1)
              }}
              className="text-[11px] text-text-muted underline-offset-2 hover:underline"
            >
              清除
            </button>
          )}
        </div>
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value)
            setPage(1)
          }}
          className="h-8 cursor-pointer rounded-control border bg-background px-2.5 text-[12px] focus:border-ring focus:outline-none"
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <span className="ml-auto text-[11px] text-text-muted">共 {total} 条记录</span>
      </section>

      {/* ---- 记录表 ---- */}
      <section className="overflow-hidden rounded-card border bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-[11px] text-text-muted">
              <th className="px-4 py-2 font-medium">时间</th>
              <th className="px-3 py-2 font-medium">股票</th>
              <th className="px-3 py-2 text-center font-medium">模式</th>
              <th className="px-3 py-2 text-center font-medium">评级</th>
              <th className="px-3 py-2 text-right font-medium">评分</th>
              <th className="hidden px-3 py-2 text-right font-medium md:table-cell">仓位</th>
              <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">目标/止损</th>
              <th className="px-3 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {isLoading && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-text-muted">
                  加载中…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-12 text-center text-text-muted">
                  暂无诊断记录。可在个股页发起诊断，或点右上「立即生成研报」。
                </td>
              </tr>
            )}
            {items.map((r) => {
              const res = r.result ?? {}
              return (
                <tr
                  key={r.run_id}
                  className="cursor-pointer transition-colors hover:bg-accent/40"
                  onClick={() => r.status === 'done' && setOpenId(r.run_id)}
                >
                  <td className="px-4 py-2.5 font-data text-[11px] text-text-muted">
                    {r.created_at.slice(5, 16)}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="text-[13px]">{r.name || r.symbol}</div>
                    <div className="font-data text-[10px] text-text-muted">{r.symbol}</div>
                  </td>
                  <td className="px-3 py-2.5 text-center text-[11px] text-text-muted">
                    {r.result?.mode === 'deep' ? '深度' : r.result?.mode === 'quick' ? '快速' : '—'}
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    {r.status === 'done' && res.rating ? (
                      <span
                        className={cn(
                          'inline-block rounded-full px-2 py-0.5 text-[10px] font-medium',
                          RATING_CLS[res.rating] ?? 'bg-muted',
                        )}
                      >
                        {res.rating}
                      </span>
                    ) : (
                      <StatusBadge status={r.status} />
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right font-data">{res.score ?? '—'}</td>
                  <td className="hidden px-3 py-2.5 text-right font-data text-xs md:table-cell">
                    {res.position_pct != null ? `${res.position_pct}%` : '—'}
                  </td>
                  <td className="hidden px-3 py-2.5 text-right font-data text-[11px] lg:table-cell">
                    <span className="text-up">{res.target_price || '—'}</span>
                    <span className="text-text-muted"> / </span>
                    <span className="text-down">{res.stop_loss_price || '—'}</span>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {r.status === 'done' && (
                      <a
                        href={diagnosisExportUrl(r.run_id)}
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center gap-1 text-[11px] text-text-muted transition-colors hover:text-gold"
                        title="下载 Markdown 报告"
                      >
                        <Download className="size-3.5" />
                      </a>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        {/* 分页 */}
        <div className="flex items-center justify-between border-t px-4 py-2.5 text-[11px] text-text-muted">
          <span>
            第 {page} / {totalPages} 页
          </span>
          <div className="flex items-center gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="rounded-control border px-2.5 py-1 transition-colors hover:text-foreground disabled:opacity-40"
            >
              上一页
            </button>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="rounded-control border px-2.5 py-1 transition-colors hover:text-foreground disabled:opacity-40"
            >
              下一页
            </button>
          </div>
        </div>
      </section>

      {openId != null && <ReportModal runId={openId} onClose={() => setOpenId(null)} />}
    </div>
  )
}

/** 状态徽章（非 done 时显示） */
function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { text: string; cls: string }> = {
    running: { text: '进行中', cls: 'text-gold border-gold/40' },
    failed: { text: '失败', cls: 'text-down border-down/40' },
  }
  const m = map[status] ?? { text: status, cls: 'text-text-muted border-border' }
  return (
    <span className={cn('rounded-full border px-2 py-0.5 text-[10px]', m.cls)}>{m.text}</span>
  )
}

/* ============================================================
 * 研报详情弹窗：拉完整 run（含各阶段），渲染决策卡 + 分析师评分
 * ============================================================ */
function ReportModal({ runId, onClose }: { runId: number; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['diag-run', runId],
    queryFn: () => fetchDiagnosisRun(runId),
  })

  return (
    <Portal>
      <div
        className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm"
        onClick={onClose}
      >
        <div
          className="my-8 w-full max-w-3xl rounded-card border bg-card shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          {isLoading || !data ? (
            <div className="flex h-48 items-center justify-center text-text-muted">
              <Loader2 className="size-5 animate-spin" />
            </div>
          ) : (
            <ReportBody run={data} onClose={onClose} />
          )}
        </div>
      </div>
    </Portal>
  )
}

function ReportBody({ run, onClose }: { run: DiagnosisRun; onClose: () => void }) {
  const res = run.result ?? {}
  const stages = run.stages ?? {}
  const analysts: { key: keyof NonNullable<DiagnosisRun['stages']>; label: string }[] = [
    { key: 'tech', label: '技术面' },
    { key: 'fund', label: '资金面' },
    { key: 'news', label: '消息面' },
    { key: 'fundamental', label: '基本面' },
    { key: 'macro', label: '宏观择时' },
    { key: 'quant', label: '量化' },
    { key: 'sector', label: '板块同业' },
  ]

  return (
    <>
      {/* 头 */}
      <header className="flex items-start justify-between gap-3 border-b px-5 py-4">
        <div>
          <h2 className="text-lg font-semibold">
            {run.name}
            <span className="ml-2 font-data text-xs text-text-muted">{run.symbol}</span>
          </h2>
          <p className="mt-0.5 text-[11px] text-text-muted">
            {run.created_at} · {run.result?.mode === 'deep' ? '深度模式' : '快速模式'} · 模型{' '}
            {run.model || '—'} · 耗时 {run.cost_seconds}s
          </p>
        </div>
        <div className="flex items-center gap-2">
          <a
            href={diagnosisExportUrl(run.run_id)}
            className="flex items-center gap-1 rounded-control border px-2.5 py-1.5 text-[11px] text-text-muted transition-colors hover:text-gold"
          >
            <Download className="size-3.5" /> 下载 MD
          </a>
          <button
            onClick={onClose}
            className="rounded-control p-1.5 text-text-muted transition-colors hover:bg-accent hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>
      </header>

      <div className="max-h-[70vh] space-y-4 overflow-y-auto px-5 py-4">
        {/* 决策卡 */}
        <div className="rounded-card border bg-background p-4">
          <div className="flex flex-wrap items-center gap-3">
            <span
              className={cn(
                'rounded-full px-3 py-1 text-sm font-semibold',
                RATING_CLS[res.rating ?? ''] ?? 'bg-muted',
              )}
            >
              {res.rating ?? '—'}
            </span>
            {res.action && (
              <span className="rounded-full border border-gold/40 px-2.5 py-1 text-xs text-gold">
                持仓建议：{res.action}
              </span>
            )}
            <Metric label="综合评分" value={`${res.score ?? '—'}`} />
            <Metric label="置信度" value={`${res.confidence ?? '—'}`} />
            <Metric label="建议仓位" value={`${res.position_pct ?? '—'}%`} />
            <Metric label="操作周期" value={res.horizon || '—'} />
          </div>
          <div className="mt-3 grid grid-cols-3 gap-3 text-center">
            <PriceBox label="买入区间" value={res.buy_zone || '—'} />
            <PriceBox label="目标价" value={res.target_price ? `${res.target_price}` : '—'} cls="text-up" />
            <PriceBox
              label="止损价"
              value={res.stop_loss_price ? `${res.stop_loss_price}` : '—'}
              cls="text-down"
            />
          </div>
          {res.summary && <p className="mt-3 text-[13px] leading-6">{res.summary}</p>}
        </div>

        {/* 风险闸门 */}
        {stages.riskgate && stages.riskgate.flags.length > 0 && (
          <div className="rounded-card border border-down/30 bg-down/5 p-3">
            <h4 className="mb-1 text-xs font-medium text-down">
              风险闸门 [{stages.riskgate.level}]
            </h4>
            <ul className="space-y-0.5 text-[12px] text-muted-foreground">
              {stages.riskgate.flags.map((f, i) => (
                <li key={i}>· {f}</li>
              ))}
            </ul>
          </div>
        )}

        {/* 操作清单 */}
        {res.checklist && res.checklist.length > 0 && (
          <div className="rounded-card border bg-background p-3">
            <h4 className="mb-1.5 text-xs font-medium">操作清单</h4>
            <ul className="space-y-1 text-[12px]">
              {res.checklist.map((c, i) => (
                <li key={i} className="flex items-start gap-1.5">
                  <span className="mt-0.5 size-3 shrink-0 rounded-sm border" /> {c}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* 决策依据 / 风险 */}
        <div className="grid gap-3 sm:grid-cols-2">
          {res.reasons && res.reasons.length > 0 && (
            <ListCard title="决策依据" items={res.reasons} tone="up" />
          )}
          {res.risks && res.risks.length > 0 && (
            <ListCard title="风险盯防" items={res.risks} tone="down" />
          )}
        </div>

        {/* 分析师评分网格 */}
        <div>
          <h4 className="mb-2 text-xs font-medium text-text-muted">分析师评分</h4>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {analysts.map(({ key, label }) => {
              const a = stages[key] as { score?: number; stance?: string } | undefined
              if (!a) return null
              return (
                <div key={String(key)} className="rounded-control border bg-background p-2 text-center">
                  <div className="text-[10px] text-text-muted">{label}</div>
                  <div
                    className={cn(
                      'font-data text-lg font-semibold',
                      (a.score ?? 50) >= 60
                        ? 'text-up'
                        : (a.score ?? 50) <= 40
                          ? 'text-down'
                          : '',
                    )}
                  >
                    {a.score ?? '—'}
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* 理论附录 */}
        {res.theory_refs && res.theory_refs.length > 0 && (
          <div className="rounded-card border bg-background p-3">
            <h4 className="mb-1.5 text-xs font-medium text-text-muted">本次引用的理论框架</h4>
            <div className="flex flex-wrap gap-1.5">
              {res.theory_refs.map((t, i) => (
                <span key={i} className="rounded-full border px-2 py-0.5 text-[10px] text-muted-foreground">
                  {t}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <div className="text-[10px] text-text-muted">{label}</div>
      <div className="font-data text-sm font-semibold">{value}</div>
    </div>
  )
}

function PriceBox({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="rounded-control border bg-card p-2">
      <div className="text-[10px] text-text-muted">{label}</div>
      <div className={cn('font-data text-sm font-semibold', cls)}>{value}</div>
    </div>
  )
}

function ListCard({ title, items, tone }: { title: string; items: string[]; tone: 'up' | 'down' }) {
  return (
    <div className="rounded-card border bg-background p-3">
      <h4 className={cn('mb-1.5 text-xs font-medium', tone === 'up' ? 'text-up' : 'text-down')}>
        {title}
      </h4>
      <ul className="space-y-1 text-[12px] text-muted-foreground">
        {items.map((it, i) => (
          <li key={i}>· {it}</li>
        ))}
      </ul>
    </div>
  )
}
