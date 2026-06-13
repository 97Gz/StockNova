import { useQuery } from '@tanstack/react-query'
import { Check } from 'lucide-react'

import { fetchStrategies } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * 回测用策略多选器：以紧凑「胶囊」形式列出全部可回测策略。
 * 与策略广场的区别——这里排除掉不可用与板块类特殊策略（无法机械回测）。
 */
export function StrategyPicker({
  selected,
  onChange,
}: {
  selected: string[]
  onChange: (ids: string[]) => void
}) {
  const { data: strategies } = useQuery({ queryKey: ['strategies'], queryFn: fetchStrategies })

  const usable = (strategies ?? []).filter((s) => s.available && !s.special)

  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id])
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {usable.map((s) => {
        const on = selected.includes(s.id)
        return (
          <button
            key={s.id}
            type="button"
            title={s.summary}
            className={cn(
              'flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] transition-colors',
              on
                ? 'border-gold/60 bg-gold/12 text-gold'
                : 'border-border text-muted-foreground hover:border-gold/30 hover:text-foreground',
            )}
            onClick={() => toggle(s.id)}
          >
            {on && <Check className="size-3" />}
            {s.name}
          </button>
        )
      })}
      {usable.length === 0 && (
        <p className="text-xs text-text-muted">策略列表加载中…</p>
      )}
    </div>
  )
}
