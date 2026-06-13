import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { Link } from 'react-router'
import { AlertTriangle, RefreshCw, Sparkles, TrendingUp } from 'lucide-react'

import { ApiError, fetchMarketSummary, generateMarketSummary } from '@/lib/api'
import { cn } from '@/lib/utils'

/** 情绪温度 → 语义色（A 股红涨绿跌：偏暖红、偏冷绿） */
function moodColor(score: number): string {
  if (score >= 55) return 'text-up'
  if (score < 45) return 'text-down'
  return 'text-gold'
}

/**
 * AI 盘面摘要卡（今日盘面页）。
 *
 * 把当日涨跌分布/成交额/板块表现/重点快讯交给 AI，生成散户能看懂的
 * 「人话版」大盘总结。一天生成一次（缓存当日），按钮触发。
 */
export function MarketSummaryCard() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['market-summary'],
    queryFn: fetchMarketSummary,
    staleTime: 10 * 60_000,
  })

  const generate = useMutation({
    mutationFn: generateMarketSummary,
    onSuccess: (result) => qc.setQueryData(['market-summary'], result),
  })

  if (isLoading) return <div className="h-full animate-pulse rounded-inner bg-muted/40" />

  // 尚未生成：引导按钮
  if (!data) {
    const err = generate.error as ApiError | null
    return (
      <div className="flex h-full min-h-32 flex-col items-center justify-center gap-3 text-center">
        <p className="max-w-60 text-xs leading-relaxed text-text-muted">
          让 AI 通读今日涨跌分布、量能、板块与快讯，生成一段人话版盘面总结
        </p>
        <button
          onClick={() => generate.mutate()}
          disabled={generate.isPending}
          className="flex items-center gap-1.5 rounded-control border border-gold/40 px-3.5 py-2 text-xs text-gold transition-colors hover:bg-gold/10 disabled:opacity-60"
        >
          {generate.isPending ? (
            <RefreshCw className="size-3.5 animate-spin" />
          ) : (
            <Sparkles className="size-3.5" />
          )}
          {generate.isPending ? 'AI 解读中…' : '生成今日摘要'}
        </button>
        {err && (
          <p className="max-w-64 text-[11px] text-destructive">
            {err.message}
            {err.code === 40050 && (
              <Link to="/settings" className="ml-1 underline">
                去配置
              </Link>
            )}
          </p>
        )}
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex h-full flex-col gap-2.5"
    >
      {/* 情绪温度行 */}
      <div className="flex items-center gap-3">
        <span className={cn('font-data text-2xl font-bold', moodColor(data.score))}>
          {data.score}°
        </span>
        <span
          className={cn(
            'rounded-full border px-2 py-0.5 text-[11px]',
            data.score >= 55
              ? 'border-up/30 bg-up/10 text-up'
              : data.score < 45
                ? 'border-down/30 bg-down/10 text-down'
                : 'border-gold/30 bg-gold/10 text-gold',
          )}
        >
          {data.label}
        </span>
        <span className="ml-auto text-[10px] text-text-muted">{data.date}</span>
      </div>

      {/* 总结正文 */}
      <p className="text-xs leading-relaxed text-muted-foreground">{data.summary}</p>

      {/* 亮点与警示 */}
      <div className="mt-auto space-y-1.5">
        {data.positive.slice(0, 2).map((p, i) => (
          <div key={`p${i}`} className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
            <TrendingUp className="mt-0.5 size-3 shrink-0 text-up/70" />
            {p}
          </div>
        ))}
        {data.negative.slice(0, 2).map((n, i) => (
          <div key={`n${i}`} className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
            <AlertTriangle className="mt-0.5 size-3 shrink-0 text-down/70" />
            {n}
          </div>
        ))}
      </div>
    </motion.div>
  )
}
