import type { InputHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/** 输入框：统一 9 等高、暗色融合背景、聚焦金色描边 */
export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'h-9 w-full rounded-[10px] border bg-background px-3 text-[13px] text-foreground',
        'placeholder:text-text-muted',
        'focus:border-ring focus:outline-none',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}
