import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Hammer, Loader2, Play, Plus, Save, Sparkles, Trash2, X } from 'lucide-react'
import { useState } from 'react'

import { Portal } from '@/components/common/Portal'
import type { FactorMeta } from '@/lib/api'
import {
  ApiError,
  aiParseStrategy,
  api,
  fetchFactors,
} from '@/lib/api'

/** 一行条件：因子 + 操作符 + 值（数值因子）/ 真假（布尔因子） */
interface ConditionRow {
  factor: string
  op: string
  value: string
  /** between 的第二个值 */
  value2: string
}

const NUMBER_OPS = [
  { op: '>', label: '大于' },
  { op: '>=', label: '大于等于' },
  { op: '<', label: '小于' },
  { op: '<=', label: '小于等于' },
  { op: 'between', label: '介于' },
]
const BOOL_OPS = [
  { op: 'is_true', label: '满足' },
  { op: 'is_false', label: '不满足' },
]

/** 已保存的自定义策略（列表项） */
interface CustomItem {
  id: number
  name: string
  condition: Record<string, unknown>
  created_at: string
}

function rowsToCondition(rows: ConditionRow[]): Record<string, unknown> {
  return {
    all: rows.map((r) => {
      if (r.op === 'is_true' || r.op === 'is_false') return { factor: r.factor, op: r.op }
      if (r.op === 'between')
        return { factor: r.factor, op: 'between', value: [Number(r.value), Number(r.value2)] }
      return { factor: r.factor, op: r.op, value: Number(r.value) }
    }),
  }
}

/**
 * 条件树 → 编辑行。仅支持「顶层 all + 简单叶子」的扁平结构（与行式编辑器对等），
 * 含嵌套 any/all 时返回 null，调用方退化为"整树直接扫描"模式。
 */
function conditionToRows(condition: Record<string, unknown>): ConditionRow[] | null {
  const all = condition.all
  if (!Array.isArray(all) || all.length === 0) return null
  const rows: ConditionRow[] = []
  for (const leaf of all) {
    if (typeof leaf !== 'object' || leaf === null) return null
    const node = leaf as Record<string, unknown>
    if ('all' in node || 'any' in node) return null
    const factor = String(node.factor ?? '')
    const op = String(node.op ?? '')
    if (!factor || !op) return null
    if (op === 'is_true' || op === 'is_false') {
      rows.push({ factor, op, value: '', value2: '' })
    } else if (op === 'between' && Array.isArray(node.value)) {
      rows.push({ factor, op, value: String(node.value[0]), value2: String(node.value[1]) })
    } else {
      rows.push({ factor, op, value: String(node.value ?? ''), value2: '' })
    }
  }
  return rows
}

/**
 * 自定义条件构建器（M3 简化版）：
 * 行式编辑「因子 + 操作符 + 值」，多行为 AND 关系；
 * 可直接执行扫描，也可起名保存成自己的策略（下次复用）。
 */
export function CustomStrategyBuilder({
  onClose,
  onRun,
}: {
  onClose: () => void
  /** 把条件树交给广场页执行扫描 */
  onRun: (condition: Record<string, unknown>) => void
}) {
  const queryClient = useQueryClient()
  const factors = useQuery({ queryKey: ['factors'], queryFn: fetchFactors, staleTime: Infinity })
  const customs = useQuery<CustomItem[]>({
    queryKey: ['custom-strategies'],
    queryFn: () => api.get('/strategies/custom'),
  })

  const [rows, setRows] = useState<ConditionRow[]>([
    { factor: 'pct_change', op: '>', value: '2', value2: '' },
  ])
  const [name, setName] = useState('')
  const [msg, setMsg] = useState('')
  // AI 解析：输入白话 → 条件树。结果能平铺就填进编辑行，嵌套结构则整树备用
  const [aiText, setAiText] = useState('')
  const [aiCondition, setAiCondition] = useState<Record<string, unknown> | null>(null)
  const [aiNote, setAiNote] = useState('')

  const aiParse = useMutation({
    mutationFn: () => aiParseStrategy(aiText.trim()),
    onSuccess: (data) => {
      const parsed = conditionToRows(data.condition)
      if (parsed) {
        // 简单结构：填入编辑行让用户微调，与手填体验一致
        setRows(parsed)
        setAiCondition(null)
      } else {
        // 嵌套结构（含 any）：行式编辑器表达不了，整树保存供直接扫描
        setAiCondition(data.condition)
      }
      setName(data.name)
      const parts = [`已解析：${data.summary || data.name}`]
      if (data.unmatched.length > 0) parts.push(`未能映射：${data.unmatched.join('；')}`)
      setAiNote(parts.join('　'))
    },
    onError: (e) => {
      setAiCondition(null)
      setAiNote(e instanceof ApiError ? e.message : 'AI 解析失败，请稍后重试')
    },
  })

  const factorOf = (n: string): FactorMeta | undefined => factors.data?.find((f) => f.name === n)

  // 生效条件树：AI 解析出的嵌套结构优先，否则取编辑行
  const activeCondition = () => aiCondition ?? rowsToCondition(rows)

  const save = useMutation({
    mutationFn: () => api.post('/strategies/custom', { name, condition: activeCondition() }),
    onSuccess: () => {
      setMsg(`已保存「${name}」`)
      setName('')
      queryClient.invalidateQueries({ queryKey: ['custom-strategies'] })
    },
    onError: (e) => setMsg(e instanceof ApiError ? e.message : '保存失败'),
  })

  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/strategies/custom/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['custom-strategies'] }),
  })

  const updateRow = (i: number, patch: Partial<ConditionRow>) =>
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))

  const valid = rows.every((r) => {
    if (r.op === 'is_true' || r.op === 'is_false') return true
    if (r.op === 'between') return r.value !== '' && r.value2 !== ''
    return r.value !== ''
  })

  return (
    <Portal>
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-card border bg-popover shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b bg-card/60 px-5 py-4">
          <div className="flex items-center gap-2">
            <Hammer className="size-4 text-gold" />
            <h3 className="text-base font-semibold">自定义选股条件</h3>
            <span className="text-[11px] text-text-muted">多行条件为「同时满足」关系</span>
          </div>
          <button
            className="rounded-control p-1.5 text-text-muted transition-colors hover:bg-accent"
            onClick={onClose}
          >
            <X className="size-4" />
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-auto px-5 py-4">
          {/* AI 白话解析：一句话描述选股想法，LLM 翻译成条件 */}
          <div className="rounded-inner border border-gold/25 bg-gold/4 p-3">
            <div className="flex items-center gap-1.5 text-xs font-medium text-gold">
              <Sparkles className="size-3.5" />
              AI 智能解析
              <span className="font-normal text-text-muted">
                用大白话描述想选什么股，AI 自动翻译成下方条件
              </span>
            </div>
            <div className="mt-2 flex gap-2">
              <input
                value={aiText}
                onChange={(e) => setAiText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && aiText.trim().length >= 2 && !aiParse.isPending)
                    aiParse.mutate()
                }}
                placeholder="例：尾盘半小时放量拉升、市盈率低于30、不要ST"
                maxLength={500}
                className="h-8 flex-1 rounded-control border bg-background px-2.5 text-xs outline-none placeholder:text-text-muted focus:border-gold/40"
              />
              <button
                disabled={aiText.trim().length < 2 || aiParse.isPending}
                className="flex shrink-0 items-center gap-1.5 rounded-control border border-gold/40 px-3 py-1.5 text-xs text-gold transition-colors hover:bg-gold/10 disabled:opacity-40"
                onClick={() => aiParse.mutate()}
              >
                {aiParse.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Sparkles className="size-3.5" />
                )}
                {aiParse.isPending ? '解析中…' : '解析'}
              </button>
            </div>
            {aiNote && <p className="mt-2 text-[11px] leading-relaxed text-text-muted">{aiNote}</p>}
            {aiCondition && (
              <div className="mt-2">
                <p className="text-[11px] text-gold">
                  该策略含「任一满足」嵌套结构，已整树就绪——可直接扫描或保存（下方编辑行不生效）
                </p>
                <pre className="mt-1 max-h-32 overflow-auto rounded-inner border bg-background p-2 font-data text-[10px] leading-relaxed text-text-muted">
                  {JSON.stringify(aiCondition, null, 2)}
                </pre>
                <button
                  className="mt-1 text-[11px] text-text-muted underline-offset-2 hover:underline"
                  onClick={() => {
                    setAiCondition(null)
                    setAiNote('')
                  }}
                >
                  放弃该结果，回到手动编辑
                </button>
              </div>
            )}
          </div>

          {/* 条件行编辑器 */}
          <div className="space-y-2">
            {rows.map((row, i) => {
              const meta = factorOf(row.factor)
              const isBool = meta?.kind === 'bool'
              const ops = isBool ? BOOL_OPS : NUMBER_OPS
              return (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <select
                    value={row.factor}
                    onChange={(e) => {
                      const next = factorOf(e.target.value)
                      // 切换因子时若 kind 变化，重置为该类型的默认操作符
                      const op =
                        next?.kind === 'bool'
                          ? 'is_true'
                          : row.op === 'is_true' || row.op === 'is_false'
                            ? '>'
                            : row.op
                      updateRow(i, { factor: e.target.value, op })
                    }}
                    className="h-8 min-w-44 rounded-control border bg-background px-2 text-xs outline-none"
                  >
                    {factors.data?.map((f) => (
                      <option key={f.name} value={f.name}>
                        {f.label}
                        {f.unit ? `（${f.unit}）` : ''}
                      </option>
                    ))}
                  </select>
                  <select
                    value={row.op}
                    onChange={(e) => updateRow(i, { op: e.target.value })}
                    className="h-8 rounded-control border bg-background px-2 text-xs outline-none"
                  >
                    {ops.map((o) => (
                      <option key={o.op} value={o.op}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                  {!isBool && (
                    <input
                      type="number"
                      value={row.value}
                      onChange={(e) => updateRow(i, { value: e.target.value })}
                      placeholder="数值"
                      className="h-8 w-24 rounded-control border bg-background px-2 font-data text-xs outline-none"
                    />
                  )}
                  {row.op === 'between' && (
                    <>
                      <span className="text-xs text-text-muted">~</span>
                      <input
                        type="number"
                        value={row.value2}
                        onChange={(e) => updateRow(i, { value2: e.target.value })}
                        placeholder="数值"
                        className="h-8 w-24 rounded-control border bg-background px-2 font-data text-xs outline-none"
                      />
                    </>
                  )}
                  {meta?.desc && (
                    <span className="max-w-56 truncate text-[10px] text-text-muted" title={meta.desc}>
                      {meta.desc}
                    </span>
                  )}
                  <button
                    disabled={rows.length === 1}
                    className="ml-auto rounded-control p-1.5 text-text-muted transition-colors hover:bg-up/10 hover:text-up disabled:opacity-30"
                    onClick={() => setRows((prev) => prev.filter((_, idx) => idx !== i))}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              )
            })}
            <button
              className="flex items-center gap-1 rounded-control border border-dashed px-2.5 py-1.5 text-[11px] text-muted-foreground transition-colors hover:border-gold/40 hover:text-gold"
              onClick={() =>
                setRows((prev) => [...prev, { factor: 'pct_change', op: '>', value: '', value2: '' }])
              }
            >
              <Plus className="size-3" />
              加一行条件
            </button>
          </div>

          {/* 保存/执行 */}
          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            <button
              disabled={!valid && !aiCondition}
              className="flex items-center gap-1.5 rounded-control bg-gold px-3.5 py-1.5 text-xs font-medium text-black/85 transition-opacity hover:opacity-90 disabled:opacity-40"
              onClick={() => {
                onRun(activeCondition())
                onClose()
              }}
            >
              <Play className="size-3.5" />
              立即扫描
            </button>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="策略起个名（保存后复用）"
              maxLength={50}
              className="h-8 w-48 rounded-control border bg-background px-2 text-xs outline-none placeholder:text-text-muted"
            />
            <button
              disabled={(!valid && !aiCondition) || !name.trim() || save.isPending}
              className="flex items-center gap-1.5 rounded-control border border-gold/40 px-3 py-1.5 text-xs text-gold transition-colors hover:bg-gold/10 disabled:opacity-40"
              onClick={() => save.mutate()}
            >
              {save.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Save className="size-3.5" />
              )}
              保存
            </button>
            {msg && <span className="text-[11px] text-gold">{msg}</span>}
          </div>

          {/* 已保存列表 */}
          {(customs.data?.length ?? 0) > 0 && (
            <div className="border-t pt-3">
              <h4 className="mb-2 text-[11px] text-text-muted">我的策略</h4>
              <ul className="space-y-1.5">
                {customs.data?.map((c) => (
                  <li
                    key={c.id}
                    className="flex items-center justify-between rounded-inner border bg-card px-3 py-2"
                  >
                    <span className="text-xs">{c.name}</span>
                    <span className="flex items-center gap-1">
                      <button
                        className="rounded-control px-2 py-1 text-[11px] text-gold transition-colors hover:bg-gold/10"
                        onClick={() => {
                          onRun(c.condition)
                          onClose()
                        }}
                      >
                        扫描
                      </button>
                      <button
                        className="rounded-control p-1.5 text-text-muted transition-colors hover:bg-up/10 hover:text-up"
                        onClick={() => del.mutate(c.id)}
                      >
                        <Trash2 className="size-3" />
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
    </Portal>
  )
}
