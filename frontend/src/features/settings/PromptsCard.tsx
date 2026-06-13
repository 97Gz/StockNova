import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BrainCircuit, Check, ChevronDown, Loader2, RotateCcw } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ApiError, fetchPrompts, resetPrompt, savePrompt, type PromptItem } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * AI 提示词管理卡（M6）：所有 LLM 角色的提示词都可在此查看与修改。
 * 改完立即生效（后端每次调用现读），无需重启。
 */
export function PromptsCard() {
  const prompts = useQuery({ queryKey: ['ai-prompts'], queryFn: fetchPrompts })

  return (
    <section className="col-span-12 rounded-card border bg-card p-5">
      <header className="flex items-center gap-2">
        <BrainCircuit className="size-4 text-gold" />
        <h3 className="text-sm font-semibold">AI 提示词</h3>
        <span className="text-xs text-text-muted">
          诊股分析师 / 多空辩手 / 首席决策 / 情绪分析 / 策略解析 —— 修改后立即生效
        </span>
      </header>

      {prompts.isLoading && (
        <div className="flex items-center gap-2 py-8 text-xs text-text-muted">
          <Loader2 className="size-4 animate-spin" /> 加载中…
        </div>
      )}

      <div className="mt-3 space-y-2">
        {prompts.data?.map((p) => <PromptRow key={p.id} prompt={p} />)}
      </div>
    </section>
  )
}

/** 单条提示词：折叠行，展开后是编辑器 */
function PromptRow({ prompt }: { prompt: PromptItem }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  // 草稿：null 表示未编辑（显示当前生效模板）
  const [draft, setDraft] = useState<string | null>(null)
  const [savedFlash, setSavedFlash] = useState(false)

  const save = useMutation({
    mutationFn: () => savePrompt(prompt.id, draft ?? prompt.template),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ai-prompts'] })
      setDraft(null)
      setSavedFlash(true)
      setTimeout(() => setSavedFlash(false), 1500)
    },
  })
  const reset = useMutation({
    mutationFn: () => resetPrompt(prompt.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ai-prompts'] })
      setDraft(null)
    },
  })

  const value = draft ?? prompt.template
  const dirty = draft !== null && draft !== prompt.template
  const placeholderKeys = Object.entries(prompt.placeholders)

  return (
    <div className="rounded-control border">
      {/* 折叠头 */}
      <button
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-accent/40"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronDown
          className={cn('size-3.5 shrink-0 text-text-muted transition-transform', open && 'rotate-180')}
        />
        <span className="text-[13px] font-medium">{prompt.label}</span>
        {prompt.customized && (
          <span className="rounded-full border border-gold/40 bg-gold/10 px-2 py-0.5 text-[10px] text-gold">
            已自定义
          </span>
        )}
        <span className="ml-auto truncate text-xs text-text-muted">{prompt.desc}</span>
      </button>

      {/* 编辑区 */}
      {open && (
        <div className="border-t px-3 py-3">
          {placeholderKeys.length > 0 && (
            <p className="mb-2 text-[11px] text-text-muted">
              可用占位符：
              {placeholderKeys.map(([k, desc]) => (
                <span key={k} className="ml-2">
                  <code className="rounded bg-muted px-1 py-0.5 font-data text-gold">${k}</code>
                  <span className="ml-1">{desc}</span>
                </span>
              ))}
            </p>
          )}
          <textarea
            value={value}
            onChange={(e) => setDraft(e.target.value)}
            rows={Math.min(18, Math.max(8, value.split('\n').length + 1))}
            spellCheck={false}
            className="w-full resize-y rounded-inner border bg-background p-3 font-data text-xs leading-relaxed outline-none focus:border-ring/50"
          />
          <div className="mt-2 flex items-center gap-2">
            <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : savedFlash ? (
                <span className="flex items-center gap-1">
                  <Check className="size-3.5" /> 已保存
                </span>
              ) : (
                '保存'
              )}
            </Button>
            {prompt.customized && (
              <Button
                size="sm"
                variant="ghost"
                disabled={reset.isPending}
                onClick={() => reset.mutate()}
                title="删除自定义版本，恢复系统默认提示词"
              >
                <RotateCcw className="mr-1 size-3.5" /> 恢复默认
              </Button>
            )}
            {dirty && <span className="text-[11px] text-gold">有未保存的修改</span>}
            {(save.isError || reset.isError) && (
              <span className="text-[11px] text-destructive">
                {((save.error ?? reset.error) as ApiError).message}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
