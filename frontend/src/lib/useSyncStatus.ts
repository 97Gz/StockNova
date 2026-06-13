import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import type { SyncStatus } from '@/lib/api'
import { fetchSyncStatus } from '@/lib/api'
import { useWsEvent } from '@/lib/ws'

/**
 * 同步任务状态 Hook：WebSocket 实时推送为主，REST 轮询(5s)兜底。
 * 侧边栏状态灯与设置中心进度条共用，保证两处显示永远一致。
 */
export function useSyncStatus(): {
  status: SyncStatus
  /** 手动让 REST 状态接管（执行控制动作后调用，避免显示旧的 WS 快照） */
  resetWsOverride: () => void
} {
  const queryClient = useQueryClient()
  const restQuery = useQuery({
    queryKey: ['sync-status'],
    queryFn: fetchSyncStatus,
    refetchInterval: 5000,
  })
  const [wsStatus, setWsStatus] = useState<SyncStatus | null>(null)

  useWsEvent(
    'sync_progress',
    useCallback(
      (event: Record<string, unknown>) => {
        setWsStatus(event as unknown as SyncStatus)
        if (['done', 'failed', 'cancelled'].includes(String(event.state))) {
          void queryClient.invalidateQueries({ queryKey: ['data-stats'] })
          void queryClient.invalidateQueries({ queryKey: ['sync-logs'] })
        }
      },
      [queryClient],
    ),
  )

  return {
    status: wsStatus ?? restQuery.data ?? { state: 'idle' },
    resetWsOverride: useCallback(() => setWsStatus(null), []),
  }
}
