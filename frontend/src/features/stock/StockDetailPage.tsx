import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowLeft, BrainCircuit, CalendarClock, Star, StarOff } from 'lucide-react'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'

import { DiagnosisFlow } from '@/features/diagnosis/DiagnosisFlow'
import { KlineChart } from '@/features/stock/KlineChart'
import { StockNewsSection } from '@/features/stock/StockNewsSection'
import { useWatchlistQuotes } from '@/features/watchlist/useWatchlistQuotes'
import { addWatchlist, fetchKline, fetchStockInfo, removeWatchlist } from '@/lib/api'
import { formatAmount, formatPct, formatPrice, pctColor } from '@/lib/format'
import { resampleBars, type KlinePeriod } from '@/lib/kline'
import { cn } from '@/lib/utils'

/** K 线周期切换选项 */
const PERIODS: { key: KlinePeriod; label: string }[] = [
  { key: 'day', label: '日K' },
  { key: 'week', label: '周K' },
  { key: 'month', label: '月K' },
]

/** 个股页头部的一格指标 */
function Metric({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="min-w-20">
      <div className="text-[10px] text-text-muted">{label}</div>
      <div className={cn('font-data text-sm', className)}>{value}</div>
    </div>
  )
}

/**
 * 个股详情页（M2）：报价头 + 前复权日 K（MA/VOL）+ 所属板块。
 * M5/M6 在此页扩展：新闻时间线、AI 诊断按钮。
 */
export function StockDetailPage() {
  const { symbol = '' } = useParams()
  const navigate = useNavigate()
  // AI 投研工作流弹层
  const [diagOpen, setDiagOpen] = useState(false)
  // 回溯诊断：日期选择弹层 + 选定的历史节点 + 回测工作流弹层
  const [backtestPickerOpen, setBacktestPickerOpen] = useState(false)
  const [backtestDate, setBacktestDate] = useState('')
  const [backtestAsOf, setBacktestAsOf] = useState('')
  // K 线周期：日/周/月（周月由日线客户端聚合）
  const [period, setPeriod] = useState<KlinePeriod>('day')

  const info = useQuery({
    queryKey: ['stock-info', symbol],
    queryFn: () => fetchStockInfo(symbol),
    enabled: Boolean(symbol),
  })
  const kline = useQuery({
    queryKey: ['kline', symbol],
    queryFn: () => fetchKline(symbol, 500),
    enabled: Boolean(symbol),
    staleTime: 60_000,
  })

  // 自选状态与一键加/删
  const { items, invalidate } = useWatchlistQuotes()
  const inWatchlist = items.some((it) => it.symbol === symbol)
  const toggleWatch = useMutation({
    mutationFn: () => (inWatchlist ? removeWatchlist(symbol) : addWatchlist(symbol).then(() => {})),
    onSuccess: invalidate,
  })

  const q = info.data?.quote
  const basic = info.data?.basic
  const fundamentals = info.data?.fundamentals

  // 代码不存在（后端 40404）：给一个明确的错误页，而不是残缺的报价头
  if (info.isError) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center gap-3 text-center">
        <p className="text-lg font-medium">未找到股票 {symbol}</p>
        <p className="text-sm text-text-muted">请检查代码是否正确，或用 Ctrl+K 搜索名称/拼音</p>
        <button
          onClick={() => navigate('/')}
          className="mt-2 rounded-control border border-gold/40 px-4 py-1.5 text-xs text-gold hover:bg-gold/10"
        >
          返回今日盘面
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* ---- 报价头 ---- */}
      <section className="rounded-card border bg-card p-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            onClick={() => navigate(-1)}
            className="flex size-8 items-center justify-center rounded-control border text-muted-foreground hover:text-foreground"
            title="返回"
          >
            <ArrowLeft className="size-4" />
          </button>

          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">{basic?.name ?? symbol}</h2>
              <span className="font-data text-xs text-text-muted">{symbol}</span>
              {basic && (
                <span className="rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {basic.market}
                </span>
              )}
              {basic?.is_st && (
                <span className="rounded bg-up/15 px-1.5 py-0.5 text-[10px] font-semibold text-up">
                  ST 风险
                </span>
              )}
            </div>
          </div>

          {q && (
            <div className="flex items-baseline gap-3">
              <span className={cn('font-data text-2xl font-semibold', pctColor(q.pct_change))}>
                {formatPrice(q.price)}
              </span>
              <span className={cn('font-data text-sm', pctColor(q.pct_change))}>
                {q.change > 0 ? '+' : ''}
                {q.change.toFixed(2)} ({formatPct(q.pct_change)})
              </span>
            </div>
          )}

          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => setDiagOpen(true)}
              className="flex items-center gap-1.5 rounded-control bg-gold px-3 py-1.5 text-xs font-semibold text-black transition-transform hover:scale-105"
              title="四位 AI 分析师并行研判 + 多空辩论 + 首席决策"
            >
              <BrainCircuit className="size-3.5" />
              AI 诊股
            </button>
            {/* 回溯诊断：指定历史交易日做诊断，再用后续真实走势校验 AI 准不准 */}
            <div className="relative">
              <button
                onClick={() => setBacktestPickerOpen((v) => !v)}
                className="flex items-center gap-1.5 rounded-control border border-gold/40 px-3 py-1.5 text-xs text-gold transition-colors hover:bg-gold/10"
                title="回溯模式：以某个历史交易日为「今天」诊断，数据严格截断到该日，再用后续走势检验"
              >
                <CalendarClock className="size-3.5" />
                回溯诊断
              </button>
              {backtestPickerOpen && (
                <div className="absolute right-0 top-full z-30 mt-1.5 w-64 rounded-card border bg-popover p-3 shadow-xl">
                  <div className="mb-1 text-xs font-medium">回溯到历史某日诊断</div>
                  <p className="mb-2 text-[11px] leading-relaxed text-text-muted">
                    AI 仅看该日（含）之前的数据做判断，再用之后的真实走势校验准确度。
                  </p>
                  <input
                    type="date"
                    value={backtestDate}
                    max={new Date(Date.now() - 86400000).toISOString().slice(0, 10)}
                    onChange={(e) => setBacktestDate(e.target.value)}
                    className="w-full rounded-control border bg-background px-2.5 py-1.5 font-data text-xs outline-none focus:border-ring/50"
                  />
                  <button
                    disabled={!backtestDate}
                    onClick={() => {
                      setBacktestAsOf(backtestDate)
                      setBacktestPickerOpen(false)
                    }}
                    className="mt-2 w-full rounded-control bg-gold py-1.5 text-xs font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    开始回溯诊断
                  </button>
                </div>
              )}
            </div>
            <button
              onClick={() => toggleWatch.mutate()}
              disabled={toggleWatch.isPending}
              className={cn(
                'flex items-center gap-1.5 rounded-control border px-3 py-1.5 text-xs transition-colors',
                inWatchlist
                  ? 'border-border text-muted-foreground hover:text-foreground'
                  : 'border-gold/40 text-gold hover:bg-gold/10',
              )}
            >
              {inWatchlist ? <StarOff className="size-3.5" /> : <Star className="size-3.5" />}
              {inWatchlist ? '移出自选' : '加自选'}
            </button>
          </div>
        </div>

        {/* 关键指标行 */}
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 border-t pt-3">
          {q && (
            <>
              <Metric label="今开" value={formatPrice(q.open)} className={pctColor(q.open - q.prev_close)} />
              <Metric label="最高" value={formatPrice(q.high)} className={pctColor(q.high - q.prev_close)} />
              <Metric label="最低" value={formatPrice(q.low)} className={pctColor(q.low - q.prev_close)} />
              <Metric label="昨收" value={formatPrice(q.prev_close)} />
              <Metric label="成交额" value={formatAmount(q.amount)} />
              <Metric label="换手率" value={`${q.turnover.toFixed(2)}%`} />
            </>
          )}
          {fundamentals && (
            <>
              <Metric label="市盈率TTM" value={fundamentals.pe_ttm > 0 ? fundamentals.pe_ttm.toFixed(2) : '亏损'} />
              <Metric label="市净率" value={fundamentals.pb.toFixed(2)} />
              <Metric label="总市值" value={formatAmount(fundamentals.total_mv)} />
              <Metric label="流通市值" value={formatAmount(fundamentals.circ_mv)} />
            </>
          )}
        </div>
      </section>

      {/* ---- K 线主图 ---- */}
      <section className="rounded-card border bg-card p-4">
        <header className="mb-2 flex items-center justify-between">
          <h3 className="text-[13px] font-medium text-muted-foreground">
            {PERIODS.find((p) => p.key === period)?.label} · 前复权 · MA5/10/20/60
          </h3>
          <div className="flex items-center gap-2">
            {/* 周期切换 */}
            <div className="flex items-center gap-0.5 rounded-control border p-0.5">
              {PERIODS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => setPeriod(p.key)}
                  className={cn(
                    'rounded-[7px] px-2 py-0.5 text-[11px] transition-colors',
                    period === p.key
                      ? 'bg-gold/15 text-gold'
                      : 'text-text-muted hover:text-foreground',
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <span className="font-data text-[10px] text-text-muted">
              {kline.data ? `${resampleBars(kline.data, period).length} 根` : ''}
            </span>
          </div>
        </header>
        {kline.isLoading ? (
          <div className="h-[420px] animate-pulse rounded-inner bg-muted/40" />
        ) : kline.isError ? (
          <div className="flex h-[420px] items-center justify-center text-sm text-text-muted">
            暂无 K 线数据（可能是新股或数据未同步）
          </div>
        ) : (
          <KlineChart bars={resampleBars(kline.data ?? [], period)} />
        )}
      </section>

      {/* ---- 所属板块 ---- */}
      {info.data && info.data.boards.length > 0 && (
        <section className="rounded-card border bg-card p-4">
          <h3 className="mb-2 text-[13px] font-medium text-muted-foreground">所属板块</h3>
          <div className="flex flex-wrap gap-2">
            {info.data.boards.map((b) => (
              <span
                key={b.code}
                className={cn(
                  'rounded-control border px-2.5 py-1 text-xs',
                  b.type === 'industry'
                    ? 'border-gold/30 text-gold'
                    : 'border-border text-muted-foreground',
                )}
              >
                {b.name}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* ---- 消息面：资金流 + AI 情绪 + 新闻（M5） ---- */}
      <StockNewsSection symbol={symbol} name={basic?.name} />

      {/* ---- 多角色 AI 投研工作流（M6） ---- */}
      <DiagnosisFlow
        symbol={symbol}
        name={basic?.name}
        open={diagOpen}
        onClose={() => setDiagOpen(false)}
      />
      {/* 回溯诊断工作流（带历史节点 as_of，完成后展示回测校验） */}
      {backtestAsOf && (
        <DiagnosisFlow
          symbol={symbol}
          name={basic?.name}
          open
          asOf={backtestAsOf}
          onClose={() => setBacktestAsOf('')}
        />
      )}
    </div>
  )
}
