import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'

import { router } from '@/app/router'
import { initTheme } from '@/stores/theme'

import './index.css'

// 在 React 渲染前应用主题，避免首屏闪白
initTheme()

// TanStack Query 全局实例：服务端数据的缓存/轮询/重试都由它统一管理
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 行情类接口各自覆盖；默认 30 秒内不重复请求
      staleTime: 30_000,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
