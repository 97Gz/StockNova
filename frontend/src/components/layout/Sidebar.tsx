import { ChevronsLeft, ChevronsRight, Coffee } from 'lucide-react'
import { useState } from 'react'
import { NavLink } from 'react-router'

import { SponsorDialog } from '@/components/common/SponsorDialog'
import { NAV_ITEMS } from '@/app/nav'
import { useSyncStatus } from '@/lib/useSyncStatus'
import { cn } from '@/lib/utils'
import { useUiStore } from '@/stores/ui'

/** 同步状态 → 状态灯样式与文案 */
const SYNC_LIGHT: Record<string, { cls: string; label: string }> = {
  idle: { cls: 'bg-flat/50', label: '数据空闲' },
  running: { cls: 'bg-gold animate-pulse', label: '数据同步中' },
  paused: { cls: 'bg-flat', label: '同步已暂停' },
  done: { cls: 'bg-down', label: '同步完成' },
  failed: { cls: 'bg-up', label: '同步失败' },
  cancelled: { cls: 'bg-flat', label: '同步已取消' },
}

/**
 * 侧边栏：220px 展开 / 64px 收缩（UI 规范第 7 节）。
 * 当前激活页用金色左标线高亮（墨金终端风）；底部为收缩开关（数据同步状态灯 M1 接入）。
 */
export function Sidebar() {
  const collapsed = useUiStore((s) => s.sidebarCollapsed)
  const toggle = useUiStore((s) => s.toggleSidebar)
  const [sponsorOpen, setSponsorOpen] = useState(false)
  const { status } = useSyncStatus()
  const light = SYNC_LIGHT[status.state] ?? SYNC_LIGHT.idle
  const percent =
    status.state === 'running' && status.total
      ? ` ${Math.round(((status.done ?? 0) / status.total) * 100)}%`
      : ''

  return (
    <aside
      className={cn(
        'flex h-full shrink-0 flex-col border-r bg-card transition-[width] duration-200',
        collapsed ? 'w-16' : 'w-[220px]',
      )}
    >
      {/* Logo 区：应用图标 + 产品名 */}
      <div className="flex h-14 items-center gap-2.5 px-4">
        <img
          src="/app-icon.png"
          alt="星智股"
          className="size-8 shrink-0 rounded-[10px] ring-1 ring-gold/30"
        />
        {!collapsed && (
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-wide">星智股</div>
            <div className="font-data text-[9px] uppercase tracking-[0.18em] text-text-muted">
              StockNova
            </div>
          </div>
        )}
      </div>

      {/* 导航区 */}
      <nav className="flex-1 space-y-1 overflow-y-auto px-2.5 py-2">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            title={collapsed ? item.label : undefined}
            className={({ isActive }) =>
              cn(
                'relative flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-[13px] transition-colors',
                isActive
                  ? // 金色左标线 + 微亮底色：终端式的克制高亮，不用大色块渐变
                    'bg-accent font-medium text-gold before:absolute before:left-0 before:top-1/2 before:h-4 before:w-0.5 before:-translate-y-1/2 before:rounded-full before:bg-gold'
                  : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                collapsed && 'justify-center px-0 before:hidden',
              )
            }
          >
            <item.icon className="size-[18px] shrink-0" />
            {!collapsed && <span>{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* 底部：请喝咖啡（赞助） + 数据同步状态灯 + 收缩开关 */}
      <button
        onClick={() => setSponsorOpen(true)}
        title="请作者喝杯咖啡"
        className={cn(
          'flex h-9 items-center gap-2 border-t px-4 text-[11px] text-text-muted',
          'transition-colors hover:text-gold',
          collapsed && 'justify-center px-0',
        )}
      >
        <Coffee className="size-3.5 shrink-0" />
        {!collapsed && <span className="truncate">请作者喝咖啡</span>}
      </button>
      <SponsorDialog open={sponsorOpen} onClose={() => setSponsorOpen(false)} />

      <NavLink
        to="/settings"
        title={light.label + percent}
        className={cn(
          'flex h-9 items-center gap-2 border-t px-4 text-[11px] text-text-muted',
          'transition-colors hover:text-foreground',
          collapsed && 'justify-center px-0',
        )}
      >
        <span className={cn('size-1.5 shrink-0 rounded-full', light.cls)} />
        {!collapsed && (
          <span className="truncate">
            {light.label}
            <span className="font-data">{percent}</span>
          </span>
        )}
      </NavLink>
      <button
        onClick={toggle}
        className="flex h-11 items-center justify-center gap-2 border-t text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        {collapsed ? <ChevronsRight className="size-4" /> : <ChevronsLeft className="size-4" />}
        {!collapsed && <span>收起侧栏</span>}
      </button>
    </aside>
  )
}
