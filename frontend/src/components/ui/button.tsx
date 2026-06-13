import type { ButtonHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/**
 * 按钮：墨金终端风格的四种形态。
 * primary=品牌金实底（关键动作）/ outline=描边 / ghost=无底 / danger=破坏性操作
 */
const VARIANTS = {
  primary: 'bg-primary text-primary-foreground hover:opacity-90 font-medium',
  outline: 'border bg-transparent text-foreground hover:bg-accent',
  ghost: 'text-muted-foreground hover:bg-accent hover:text-foreground',
  danger: 'border border-destructive/40 text-destructive hover:bg-destructive/10',
} as const

const SIZES = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-9 px-4 text-[13px]',
} as const

export function Button({
  variant = 'outline',
  size = 'md',
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof VARIANTS
  size?: keyof typeof SIZES
}) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-[10px] transition-colors',
        'disabled:pointer-events-none disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    />
  )
}
