import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * 主题状态（Zustand + localStorage 持久化）。
 *
 * 设计要点（UI 规范 2.3 节）：
 * - mode：深色为默认主题
 * - colorMode：涨跌色方向。'cn' = 红涨绿跌（默认），'intl' = 绿涨红跌。
 *   切换时只交换 CSS 变量 --up/--down 的值，所有行情组件零改动。
 */
interface ThemeState {
  mode: 'dark' | 'light'
  colorMode: 'cn' | 'intl'
  toggleMode: () => void
  setColorMode: (m: 'cn' | 'intl') => void
}

/** 把主题应用到 <html>：.dark class 驱动全部 CSS 变量切换 */
function applyTheme(mode: 'dark' | 'light', colorMode: 'cn' | 'intl') {
  const root = document.documentElement
  root.classList.toggle('dark', mode === 'dark')

  // 涨跌色：国际模式交换红绿
  const up = colorMode === 'cn' ? '#f23645' : '#089981'
  const down = colorMode === 'cn' ? '#089981' : '#f23645'
  root.style.setProperty('--up', up)
  root.style.setProperty('--down', down)
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      mode: 'dark',
      colorMode: 'cn',
      toggleMode: () => {
        const next = get().mode === 'dark' ? 'light' : 'dark'
        set({ mode: next })
        applyTheme(next, get().colorMode)
      },
      setColorMode: (m) => {
        set({ colorMode: m })
        applyTheme(get().mode, m)
      },
    }),
    {
      name: 'stocknova-theme',
      // localStorage 恢复完成后立即应用，避免刷新后主题闪烁
      onRehydrateStorage: () => (state) => {
        applyTheme(state?.mode ?? 'dark', state?.colorMode ?? 'cn')
      },
    },
  ),
)

/** 应用启动时调用一次：首次访问（无持久化数据）也能正确套上默认深色主题 */
export function initTheme() {
  const { mode, colorMode } = useThemeStore.getState()
  applyTheme(mode, colorMode)
}
