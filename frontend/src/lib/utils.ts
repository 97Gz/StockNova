import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * 合并 className 的工具函数（shadcn/ui 约定）：
 * clsx 负责条件拼接，twMerge 负责去重冲突的 Tailwind 类（后者优先）。
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
