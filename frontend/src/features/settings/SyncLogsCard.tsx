import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useState } from 'react'

import { fetchSyncLogs } from '@/lib/api'
import { cn } from '@/lib/utils'

/** 同步历史卡：每次初始化/增量任务的留痕（验收标准：增量同步有记录可查）。
 *
 * 任务3：列表加分页 + 滚动条——历史记录会越积越多，一次全拉既慢又挤，
 * 这里按页拉取（每页 10 条），表格区固定高度并允许纵向滚动。
 */

const PAGE_SIZE = 10

const TYPE_LABEL: Record<string, string> = {
  init_history: '历史初始化',
  daily: '每日增量',
}

const STATUS_META: Record<string, { label: string; cls: string }> = {
  running: { label: '进行中', cls: 'text-gold' },
  success: { label: '成功', cls: 'text-down' },
  failed: { label: '失败', cls: 'text-up' },
  cancelled: { label: '已取消', cls: 'text-flat' },
}

export function SyncLogsCard() {
  const [page, setPage] = useState(1)
  const logs = useQuery({
    queryKey: ['sync-logs', page],
    queryFn: () => fetchSyncLogs(page, PAGE_SIZE),
    refetchInterval: 30_000,
    // 翻页/刷新时保留上一页数据，避免表格闪烁空白
    placeholderData: keepPreviousData,
  })

  const items = logs.data?.items ?? []
  const total = logs.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <section className="col-span-12 rounded-card border bg-card p-5 lg:col-span-7">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">同步历史</h3>
        {total > 0 && <span className="text-xs text-text-muted">共 {total} 条</span>}
      </div>

      {total === 0 ? (
        <p className="mt-4 text-xs text-text-muted">还没有同步记录</p>
      ) : (
        <>
          {/* 固定高度滚动区：超过一页高度时出现纵向滚动条 */}
          <div className="mt-3 max-h-[420px] overflow-auto">
            <table className="w-full text-[13px]">
              <thead className="sticky top-0 z-10 bg-card">
                <tr className="border-b text-left text-xs text-text-muted">
                  <th className="py-2 pr-4 font-normal">任务</th>
                  <th className="py-2 pr-4 font-normal">状态</th>
                  <th className="py-2 pr-4 font-normal">开始时间</th>
                  <th className="py-2 pr-4 font-normal">结束时间</th>
                  <th className="py-2 pr-4 text-right font-normal">成功/总数</th>
                  <th className="py-2 font-normal">备注</th>
                </tr>
              </thead>
              <tbody>
                {items.map((log) => {
                  const meta = STATUS_META[log.status] ?? STATUS_META.running
                  return (
                    <tr key={log.id} className="border-b border-border/50 last:border-0">
                      <td className="py-2 pr-4">{TYPE_LABEL[log.task_type] ?? log.task_type}</td>
                      <td className={cn('py-2 pr-4 text-xs', meta.cls)}>{meta.label}</td>
                      <td className="py-2 pr-4 font-data text-xs text-muted-foreground">
                        {log.started_at}
                      </td>
                      <td className="py-2 pr-4 font-data text-xs text-muted-foreground">
                        {log.finished_at || '—'}
                      </td>
                      <td className="py-2 pr-4 text-right font-data text-xs">
                        {log.done.toLocaleString()} / {log.total.toLocaleString()}
                        {log.failed > 0 && <span className="text-up">（失败 {log.failed}）</span>}
                      </td>
                      <td
                        className="max-w-md truncate py-2 text-xs text-text-muted"
                        title={log.message}
                      >
                        {log.message || '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* 分页器：上一页 / 页码 / 下一页 */}
          <div className="mt-3 flex items-center justify-end gap-3 text-xs text-text-muted">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="flex items-center gap-1 rounded-md border px-2 py-1 transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft className="size-3.5" />
              上一页
            </button>
            <span className="font-data">
              {page} / {totalPages}
            </span>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="flex items-center gap-1 rounded-md border px-2 py-1 transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
            >
              下一页
              <ChevronRight className="size-3.5" />
            </button>
          </div>
        </>
      )}
    </section>
  )
}
