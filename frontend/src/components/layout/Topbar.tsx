import { useQuery } from '@tanstack/react-query'
import { Moon, Search, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useLocation } from 'react-router'

import { NAV_ITEMS } from '@/app/nav'
import { SearchPalette } from '@/components/common/SearchPalette'
import { fetchHealth } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/theme'

/**
 * 顶栏：当前页标题 + 全局搜索（Ctrl+K 命令面板）+ 后端状态灯 + 主题切换。
 * 背景做轻微毛玻璃（UI 规范允许玻璃拟态用于顶栏）。
 */
export function Topbar() {
  const { pathname } = useLocation()
  const mode = useThemeStore((s) => s.mode)
  const toggleMode = useThemeStore((s) => s.toggleMode)
  const [searchOpen, setSearchOpen] = useState(false)

  // 全局快捷键 Ctrl+K / Cmd+K 唤起搜索
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setSearchOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // 每 30 秒探测一次后端健康状态，驱动右侧状态灯
  const health = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
    retry: false,
  })

  const current = NAV_ITEMS.find((i) =>
    i.path === '/' ? pathname === '/' : pathname.startsWith(i.path),
  )

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-card/70 px-5 backdrop-blur">
      <h1 className="text-sm font-semibold">{current?.label ?? '星智股'}</h1>

      <div className="flex-1" />

      {/* 全局搜索入口 */}
      <button
        className="flex h-9 w-72 items-center gap-2 rounded-control border bg-background px-3 text-[13px] text-text-muted transition-colors hover:border-ring/40"
        title="全局搜索"
        onClick={() => setSearchOpen(true)}
      >
        <Search className="size-4 shrink-0" />
        <span className="whitespace-nowrap">搜索代码 / 拼音 / 名称</span>
        <kbd className="ml-auto shrink-0 rounded border px-1.5 py-0.5 font-data text-[10px]">
          Ctrl K
        </kbd>
      </button>
      <SearchPalette open={searchOpen} onClose={() => setSearchOpen(false)} />

      {/* 后端状态灯：绿 = 在线，红 = 失联 */}
      <div
        className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] text-muted-foreground"
        title={health.isSuccess ? `后端在线 v${health.data.version}` : '后端失联'}
      >
        <span
          className={cn(
            'size-2 rounded-full',
            health.isSuccess ? 'bg-down' : health.isError ? 'bg-up' : 'bg-flat',
          )}
        />
        {health.isSuccess ? '服务正常' : health.isError ? '服务失联' : '检测中'}
      </div>

      {/* 主题切换 */}
      <button
        onClick={toggleMode}
        className="flex size-9 items-center justify-center rounded-control border text-muted-foreground transition-colors hover:text-foreground"
        title={mode === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
      >
        {mode === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
      </button>
    </header>
  )
}
