import { Outlet } from 'react-router'

import { Sidebar } from '@/components/layout/Sidebar'
import { Topbar } from '@/components/layout/Topbar'
import { DisclaimerDialog } from '@/components/common/DisclaimerDialog'

/** 应用骨架：左侧导航 + 顶栏 + 路由内容区，外加首启免责声明 */
export function AppLayout() {
  return (
    <div className="flex h-full">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
      <DisclaimerDialog />
    </div>
  )
}
