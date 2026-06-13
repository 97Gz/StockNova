import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Loader2, RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router'

import { analyzeSentiment, ApiError, fetchSentiment, type SentimentResult } from '@/lib/api'
import { cn } from '@/lib/utils'

/** 情绪分 → 颜色语义（A 股习惯：利好红、利空绿） */
function scoreTone(score: number): string {
  if (score > 70) return 'text-up'
  if (score < 30) return 'text-down'
  return 'text-text-muted'
}

function labelBadge(label: string): string {
  if (label === '利好') return 'bg-up/15 text-up'
  if (label === '利空') return 'bg-down/15 text-down'
  return 'bg-muted text-muted-foreground'
}

/** 情绪结果展示（分数环 + 结论 + 要点） */
function SentimentView({ data }: { data: SentimentResult }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-4">
        {/* 分数环：conic-gradient 简单实现 */}
        <div
          className="relative flex size-16 shrink-0 items-center justify-center rounded-full"
          style={{
            background: `conic-gradient(${
              data.score > 70 ? 'var(--color-up)' : data.score < 30 ? 'var(--color-down)' : 'var(--color-gold)'
            } ${data.score * 3.6}deg, color-mix(in oklab, var(--color-border) 50%, transparent) 0deg)`,
          }}
        >
          <div className="flex size-[52px] flex-col items-center justify-center rounded-full bg-card">
            <span className={cn('font-data text-lg font-semibold leading-none', scoreTone(data.score))}>
              {data.score}
            </span>
            <span className="text-[9px] text-text-muted">情绪分</span>
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={cn('rounded px-1.5 py-0.5 text-[11px] font-semibold', labelBadge(data.label))}>
              {data.label}
            </span>
            <span className="text-[10px] text-text-muted">分析于 {data.analyzed_at}</span>
          </div>
          <p className="mt-1.5 text-[13px] leading-relaxed">{data.summary}</p>
        </div>
      </div>

      {(data.positive.length > 0 || data.negative.length > 0) && (
        <div className="grid gap-2 sm:grid-cols-2">
          {data.positive.length > 0 && (
            <div className="rounded-inner border border-up/20 bg-up/5 p-2.5">
              <div className="mb-1 text-[11px] font-medium text-up">利好要点</div>
              <ul className="space-y-1 text-xs text-muted-foreground">
                {data.positive.map((p) => (
                  <li key={p} className="flex gap-1.5">
                    <span className="text-up">+</span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.negative.length > 0 && (
            <div className="rounded-inner border border-down/20 bg-down/5 p-2.5">
              <div className="mb-1 text-[11px] font-medium text-down">利空/风险</div>
              <ul className="space-y-1 text-xs text-muted-foreground">
                {data.negative.map((p) => (
                  <li key={p} className="flex gap-1.5">
                    <span className="text-down">-</span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * AI 情绪诊断面板：读当日缓存 → 没有则显示"开始分析"按钮。
 * AI 未配置（40050）时引导去设置中心。
 */
export function SentimentPanel({ symbol, name }: { symbol: string; name?: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const cached = useQuery({
    queryKey: ['sentiment', symbol],
    queryFn: () => fetchSentiment(symbol),
    enabled: Boolean(symbol),
    staleTime: 60_000,
  })

  const analyze = useMutation({
    mutationFn: () => analyzeSentiment(symbol, name ?? ''),
    onSuccess: (data) => queryClient.setQueryData(['sentiment', symbol], data),
  })

  const notConfigured = analyze.error instanceof ApiError && analyze.error.code === 40050

  return (
    <div>
      <header className="mb-2 flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground">
          <Bot className="size-3.5 text-gold" />
          AI 消息面诊断
        </h3>
        {cached.data && (
          <span className="text-[10px] text-text-muted">当日缓存 · 次日自动失效</span>
        )}
      </header>

      {cached.isLoading ? (
        <div className="h-20 animate-pulse rounded-inner bg-muted/40" />
      ) : cached.data ? (
        <SentimentView data={cached.data} />
      ) : (
        <div className="flex flex-col items-start gap-2">
          <p className="text-xs leading-relaxed text-text-muted">
            让 AI 通读这只股票的近期新闻，输出情绪分（0~100）、利好利空要点与一句话结论。
          </p>
          <button
            onClick={() => analyze.mutate()}
            disabled={analyze.isPending}
            className="flex items-center gap-1.5 rounded-control border border-gold/40 px-3 py-1.5 text-xs text-gold transition-colors hover:bg-gold/10 disabled:opacity-60"
          >
            {analyze.isPending ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                AI 正在阅读新闻…（约 10~30 秒）
              </>
            ) : (
              <>
                <RefreshCw className="size-3.5" />
                开始 AI 诊断
              </>
            )}
          </button>
          {analyze.isError && (
            <div className="rounded-inner border border-down/30 bg-down/5 px-3 py-2 text-xs text-down">
              {(analyze.error as Error).message}
              {notConfigured && (
                <button
                  onClick={() => navigate('/settings')}
                  className="ml-2 underline underline-offset-2 hover:text-foreground"
                >
                  去设置中心配置
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
