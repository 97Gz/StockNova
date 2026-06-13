import {
  Briefcase,
  FileText,
  History,
  LayoutDashboard,
  ListChecks,
  Newspaper,
  Settings,
  Sparkles,
  Star,
  type LucideIcon,
} from 'lucide-react'

/** 侧边栏导航配置：8 个一级页面（个股详情经搜索/列表进入，不在侧边栏） */
export interface NavItem {
  path: string
  label: string
  icon: LucideIcon
  /** 该页面由哪个里程碑交付（占位页展示用） */
  milestone: string
}

export const NAV_ITEMS: NavItem[] = [
  { path: '/', label: '今日盘面', icon: LayoutDashboard, milestone: 'M2' },
  { path: '/strategies', label: '策略广场', icon: Sparkles, milestone: 'M3' },
  { path: '/signals', label: '选股结果', icon: ListChecks, milestone: 'M3' },
  { path: '/watchlist', label: '自选股', icon: Star, milestone: 'M2' },
  { path: '/portfolio', label: '持仓诊断', icon: Briefcase, milestone: 'M6' },
  { path: '/research', label: 'AI 研报库', icon: FileText, milestone: 'M6' },
  { path: '/backtest', label: '历史推演', icon: History, milestone: 'M4' },
  { path: '/news', label: '消息中心', icon: Newspaper, milestone: 'M5' },
  { path: '/settings', label: '设置中心', icon: Settings, milestone: 'M1' },
]
