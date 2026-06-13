import { GraduationCap, X } from 'lucide-react'

import { Portal } from '@/components/common/Portal'
import { riskBadge } from '@/features/strategy/risk'
import type { StrategyMeta } from '@/lib/api'

/**
 * 策略白话讲解弹层：把 explain 文案按段落展示。
 * 访谈核心诉求——"小股民根本没研究过均线多头排列"，所以每个策略
 * 都能点开看"它在说什么 / 适合谁 / 什么时候会失效"。
 */
export function StrategyExplainDialog({
  strategy,
  onClose,
}: {
  strategy: StrategyMeta
  onClose: () => void
}) {
  const risk = riskBadge(strategy.risk)
  return (
    <Portal>
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-card border bg-popover shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between border-b bg-card/60 px-5 py-4">
          <div>
            <div className="flex items-center gap-2">
              <GraduationCap className="size-4 text-gold" />
              <h3 className="text-base font-semibold">{strategy.name}</h3>
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                {strategy.tech_name}
              </span>
            </div>
            <div className="mt-1.5 flex items-center gap-2 text-[10px]">
              <span className={`rounded-full px-2 py-0.5 ${risk.cls}`}>{risk.text}</span>
              <span className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
                适用：{strategy.period}
              </span>
              <span className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
                {strategy.category}
              </span>
            </div>
          </div>
          <button
            className="rounded-control p-1.5 text-text-muted transition-colors hover:bg-accent"
            onClick={onClose}
          >
            <X className="size-4" />
          </button>
        </header>
        <div className="max-h-[60vh] space-y-3 overflow-auto px-5 py-4">
          <p className="rounded-inner border border-gold/20 bg-gold/5 px-3 py-2 text-[13px] leading-relaxed">
            {strategy.summary}
          </p>
          {strategy.explain.split('\n').map((para, i) => (
            <p key={i} className="text-[13px] leading-relaxed text-muted-foreground">
              {para}
            </p>
          ))}
          <p className="pt-1 text-[11px] text-text-muted">
            策略信号仅为量化条件筛选结果，不构成投资建议。
          </p>
        </div>
      </div>
    </div>
    </Portal>
  )
}
