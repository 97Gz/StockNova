import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Database, Pause, Play, RotateCcw, Square } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  ApiError,
  cancelSync,
  fetchDataStats,
  pauseSync,
  resumeSync,
  startDailySync,
  startInitHistory,
  syncToday,
} from '@/lib/api'
import { useSyncStatus } from '@/lib/useSyncStatus'
import { cn } from '@/lib/utils'

/**
 * 数据管理卡：M1 验收的主舞台。
 * - 历史初始化（断点续传）/ 清库重建 / 手动增量
 * - 实时进度条（WebSocket 推送，REST 轮询兜底）
 * - 暂停 / 恢复 / 取消
 */

/** 任务状态 → 显示文案与颜色 */
const STATE_META: Record<string, { label: string; cls: string }> = {
  idle: { label: '空闲', cls: 'text-text-muted' },
  running: { label: '同步中', cls: 'text-gold' },
  paused: { label: '已暂停', cls: 'text-flat' },
  done: { label: '已完成', cls: 'text-down' },
  failed: { label: '失败', cls: 'text-up' },
  cancelled: { label: '已取消', cls: 'text-flat' },
}

export function DataManageCard() {
  const queryClient = useQueryClient()
  // WS 实时推送为主，REST 轮询兜底（与侧边栏状态灯共用同一 Hook）
  const { status, resetWsOverride } = useSyncStatus()
  const stats = useQuery({ queryKey: ['data-stats'], queryFn: fetchDataStats })

  const [confirmRebuild, setConfirmRebuild] = useState(false)
  const [actionError, setActionError] = useState('')

  /** 包一层错误捕获的动作执行器 */
  const runAction = (fn: () => Promise<unknown>) =>
    makeActionHandler(fn, setActionError, () => {
      void queryClient.invalidateQueries({ queryKey: ['sync-status'] })
      resetWsOverride() // 让 REST 的最新状态接管
    })

  const busy = status.state === 'running' || status.state === 'paused'
  const total = status.total ?? 0
  const done = status.done ?? 0
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  const meta = STATE_META[status.state] ?? STATE_META.idle

  return (
    <section className="col-span-12 rounded-card border bg-card p-5 lg:col-span-7">
      <header className="flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Database className="size-4 text-gold" />
          数据管理
        </h3>
        <span className={cn('font-data text-xs', meta.cls)}>● {meta.label}</span>
      </header>
      <p className="mt-1 text-xs text-text-muted">
        首次使用请先初始化历史数据（约 1~2 小时，可随时暂停，断点续传不怕中断）
      </p>

      {/* 进度区 */}
      <div className="mt-4 rounded-[12px] border bg-background p-4">
        <div className="flex items-center justify-between text-[13px]">
          <span className="text-muted-foreground">
            {status.phase ?? '—'}
            {status.current && (
              <span className="ml-2 font-data text-xs text-text-muted">{status.current}</span>
            )}
          </span>
          <span className="font-data text-xs text-muted-foreground">
            {total > 0 ? `${done} / ${total}（失败 ${status.failed ?? 0}）` : '—'}
          </span>
        </div>
        {/* 进度条 */}
        <div className="mt-2.5 h-2 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              'h-full rounded-full transition-[width] duration-500',
              status.state === 'failed' ? 'bg-up' : 'bg-gold',
              status.state === 'running' && percent === 0 && 'animate-pulse',
            )}
            style={{ width: `${percent}%` }}
          />
        </div>
        <div className="mt-2 flex items-center justify-between">
          <span className="text-xs text-text-muted">{status.message || ' '}</span>
          <span className="font-data text-xs text-muted-foreground">{percent}%</span>
        </div>
      </div>

      {/* 操作区 */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        {!busy && (
          <>
            <Button variant="primary" onClick={runAction(() => startInitHistory(false))}>
              <Play className="size-3.5" />
              {(stats.data?.daily_bars ?? 0) > 0 ? '继续初始化（断点续传）' : '初始化历史数据'}
            </Button>
            <Button
              variant="primary"
              onClick={runAction(syncToday)}
              title="串行补齐今日全部数据：日线→分钟线→扩展数据→策略跑批→盘面摘要→AI研报"
            >
              <CalendarClock className="size-3.5" /> 立即同步今日
            </Button>
            <Button
              onClick={runAction(startDailySync)}
              title="仅补当日日线增量（轻量）"
            >
              <RotateCcw className="size-3.5" /> 手动增量同步
            </Button>
            {confirmRebuild ? (
              <span className="flex items-center gap-2 rounded-[10px] border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs">
                清空全部行情数据重新拉取？
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => {
                    setConfirmRebuild(false)
                    runAction(() => startInitHistory(true))()
                  }}
                >
                  确认清库
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirmRebuild(false)}>
                  取消
                </Button>
              </span>
            ) : (
              <Button variant="danger" onClick={() => setConfirmRebuild(true)}>
                清库重建
              </Button>
            )}
          </>
        )}
        {status.state === 'running' && (
          <Button onClick={runAction(pauseSync)}>
            <Pause className="size-3.5" /> 暂停
          </Button>
        )}
        {status.state === 'paused' && (
          <Button variant="primary" onClick={runAction(resumeSync)}>
            <Play className="size-3.5" /> 恢复
          </Button>
        )}
        {busy && (
          <Button variant="danger" onClick={runAction(cancelSync)}>
            <Square className="size-3.5" /> 取消任务
          </Button>
        )}
      </div>
      {actionError && <p className="mt-2 text-xs text-destructive">{actionError}</p>}
    </section>
  )
}

/**
 * 轻量的"动作 → 错误提示"封装：
 * 返回一个 onClick 处理器，执行异步动作并把 ApiError 信息写入错误状态。
 */
function makeActionHandler(
  fn: () => Promise<unknown>,
  setError: (msg: string) => void,
  onSuccess: () => void,
) {
  return () => {
    setError('')
    fn()
      .then(onSuccess)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)))
  }
}
