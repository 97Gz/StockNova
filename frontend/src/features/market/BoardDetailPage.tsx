import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ArrowLeft, Crown, TrendingUp } from 'lucide-react'
import { useNavigate, useParams } from 'react-router'

import { fetchBoardDetail, type BoardMember } from '@/lib/api'
import { formatAmount, formatPct, formatPrice, pctColor } from '@/lib/format'
import { cn } from '@/lib/utils'

/* ============================================================
 * 板块详情页（点击首页热力图板块进入）
 *
 * 结构：板块概览（当日涨跌/成交/近10日迷你走势）
 *      → 成分股表（按涨幅/资金/成交/换手排序，前三标龙头）
 * 懒人视角：一眼看清这个板块今天谁领涨、主力在买哪只。
 * ============================================================ */

type SortKey = 'pct_change' | 'main_net' | 'amount' | 'turnover'

const SORT_LABEL: Record<SortKey, string> = {
  pct_change: '涨跌幅',
  main_net: '主力净额',
  amount: '成交额',
  turnover: '换手率',
}

const PAGE_SIZE = 20

export function BoardDetailPage() {
  const { code = '' } = useParams()
  const navigate = useNavigate()
  const [sortKey, setSortKey] = useState<SortKey>('pct_change')
  const [page, setPage] = useState(1)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['board-detail', code],
    queryFn: () => fetchBoardDetail(code),
    enabled: Boolean(code),
    staleTime: 60_000,
  })

  // 成分股排序（数值缺失沉底）
  const sorted = useMemo(() => {
    const members = data?.members ?? []
    return [...members].sort((a, b) => (toNum(b[sortKey]) ?? -Infinity) - (toNum(a[sortKey]) ?? -Infinity))
  }, [data, sortKey])

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const pageItems = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
  // 龙头：按涨幅排名的前 3 只（始终基于涨幅，与当前排序无关）
  const leaders = useMemo(
    () => [...(data?.members ?? [])].sort((a, b) => (b.pct_change ?? -1e9) - (a.pct_change ?? -1e9)).slice(0, 3),
    [data],
  )

  if (isError) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center gap-3 text-center">
        <p className="text-lg font-medium">未找到板块 {code}</p>
        <button
          onClick={() => navigate('/')}
          className="rounded-control border border-gold/40 px-4 py-1.5 text-xs text-gold hover:bg-gold/10"
        >
          返回今日盘面
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* ---- 返回 + 概览 ---- */}
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-1 text-xs text-text-muted transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> 返回
      </button>

      <section className="rounded-card border bg-card p-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold">{data?.name ?? '加载中…'}</h1>
              <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-text-muted">
                {data?.type === 'concept' ? '概念板块' : '行业板块'}
              </span>
              <span className="font-data text-[11px] text-text-muted">{data?.code}</span>
            </div>
            <p className="mt-1 text-[11px] text-text-muted">
              成分股 {data?.members.length ?? 0} 只 · 数据日 {data?.trade_date ?? '--'}
            </p>
          </div>
          <div className="flex items-center gap-6">
            <Stat label="板块涨跌" value={data ? formatPct(data.pct_change ?? 0) : '--'} cls={pctColor(data?.pct_change ?? 0)} big />
            <Stat label="板块成交额" value={data ? formatAmount(data.amount ?? 0) : '--'} />
          </div>
        </div>

        {/* 近 10 日迷你走势条 */}
        {data && data.trend.length > 0 && (
          <div className="mt-4">
            <div className="mb-1 text-[10px] text-text-muted">近 {data.trend.length} 日板块涨跌</div>
            <div className="flex items-end gap-1" style={{ height: 40 }}>
              {data.trend.map((t) => {
                const h = Math.min(100, Math.abs(t.pct_change) * 12 + 6)
                return (
                  <div key={t.date} className="group relative flex-1" title={`${t.date} ${formatPct(t.pct_change)}`}>
                    <div
                      className={cn('w-full rounded-sm', t.pct_change >= 0 ? 'bg-up/70' : 'bg-down/70')}
                      style={{ height: `${h}%`, minHeight: 3 }}
                    />
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </section>

      {/* ---- 龙头三甲 ---- */}
      {leaders.length > 0 && (
        <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {leaders.map((m, i) => (
            <button
              key={m.symbol}
              onClick={() => navigate(`/stock/${m.symbol}`)}
              className="flex items-center gap-3 rounded-card border bg-card p-3 text-left transition-colors hover:border-gold/40"
            >
              <div
                className={cn(
                  'flex size-8 shrink-0 items-center justify-center rounded-control text-xs font-bold',
                  i === 0 ? 'bg-gold/20 text-gold' : 'bg-muted text-text-muted',
                )}
              >
                {i === 0 ? <Crown className="size-4" /> : i + 1}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px]">{m.name}</div>
                <div className="font-data text-[10px] text-text-muted">{m.symbol}</div>
              </div>
              <div className={cn('text-right font-data', pctColor(m.pct_change ?? 0))}>
                <div className="text-sm">{m.pct_change != null ? formatPct(m.pct_change) : '--'}</div>
                <div className="text-[10px] opacity-80">{m.close != null ? formatPrice(m.close) : '--'}</div>
              </div>
            </button>
          ))}
        </section>
      )}

      {/* ---- 成分股表 ---- */}
      <section className="overflow-hidden rounded-card border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
          <h3 className="flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground">
            <TrendingUp className="size-3.5" /> 成分股
          </h3>
          {/* 排序切换 */}
          <div className="flex items-center gap-1 text-[11px]">
            <span className="text-text-muted">排序：</span>
            {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => (
              <button
                key={k}
                onClick={() => {
                  setSortKey(k)
                  setPage(1)
                }}
                className={cn(
                  'rounded-control px-2 py-1 transition-colors',
                  sortKey === k ? 'bg-gold/15 text-gold' : 'text-text-muted hover:text-foreground',
                )}
              >
                {SORT_LABEL[k]}
              </button>
            ))}
          </div>
        </div>

        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-[11px] text-text-muted">
              <th className="px-4 py-2 font-medium">名称 / 代码</th>
              <th className="px-3 py-2 text-right font-medium">现价</th>
              <th className="px-3 py-2 text-right font-medium">涨跌幅</th>
              <th className="hidden px-3 py-2 text-right font-medium md:table-cell">主力净额</th>
              <th className="px-3 py-2 text-right font-medium">成交额</th>
              <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">换手率</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {isLoading && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-text-muted">
                  加载中…
                </td>
              </tr>
            )}
            {pageItems.map((m) => (
              <tr
                key={m.symbol}
                className="cursor-pointer transition-colors hover:bg-accent/40"
                onClick={() => navigate(`/stock/${m.symbol}`)}
              >
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-1.5 text-[13px]">
                    {m.name}
                    {m.is_st && (
                      <span className="rounded bg-down/15 px-1 text-[9px] text-down">ST</span>
                    )}
                  </div>
                  <div className="font-data text-[10px] text-text-muted">{m.symbol}</div>
                </td>
                <td className={cn('px-3 py-2.5 text-right font-data', pctColor(m.pct_change ?? 0))}>
                  {m.close != null ? formatPrice(m.close) : '--'}
                </td>
                <td className={cn('px-3 py-2.5 text-right font-data', pctColor(m.pct_change ?? 0))}>
                  {m.pct_change != null ? formatPct(m.pct_change) : '--'}
                </td>
                <td
                  className={cn(
                    'hidden px-3 py-2.5 text-right font-data text-xs md:table-cell',
                    pctColor(m.main_net ?? 0),
                  )}
                >
                  {m.main_net != null
                    ? `${m.main_net >= 0 ? '+' : '-'}${formatAmount(Math.abs(m.main_net))}`
                    : '--'}
                </td>
                <td className="px-3 py-2.5 text-right font-data text-xs">
                  {m.amount != null ? formatAmount(m.amount) : '--'}
                </td>
                <td className="hidden px-3 py-2.5 text-right font-data text-xs text-muted-foreground lg:table-cell">
                  {m.turnover != null ? `${m.turnover.toFixed(1)}%` : '--'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* 分页 */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between border-t px-4 py-2.5 text-[11px] text-text-muted">
            <span>
              共 {sorted.length} 只 · 第 {page} / {totalPages} 页
            </span>
            <div className="flex items-center gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="rounded-control border px-2.5 py-1 transition-colors hover:text-foreground disabled:opacity-40"
              >
                上一页
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                className="rounded-control border px-2.5 py-1 transition-colors hover:text-foreground disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}

/** 概览指标 */
function Stat({ label, value, cls, big }: { label: string; value: string; cls?: string; big?: boolean }) {
  return (
    <div className="text-right">
      <div className="text-[10px] text-text-muted">{label}</div>
      <div className={cn('font-data font-semibold', big ? 'text-2xl' : 'text-base', cls)}>{value}</div>
    </div>
  )
}

/** 取数值字段（兼容 null） */
function toNum(v: BoardMember[keyof BoardMember]): number | null {
  return typeof v === 'number' ? v : null
}
