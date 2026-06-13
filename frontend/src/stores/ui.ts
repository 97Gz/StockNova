import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/** 全局 UI 状态：只放界面偏好（侧栏开合等），业务数据一律走 TanStack Query */
interface UiState {
  sidebarCollapsed: boolean
  toggleSidebar: () => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
    }),
    { name: 'stocknova-ui' },
  ),
)
