import axios from 'axios'

/**
 * 后端统一响应包结构（backend/app/core/exceptions.py 的约定）：
 * code = 0 表示成功；非 0 为业务错误码，message 为可展示的错误信息。
 */
export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

/** 业务错误：拦截器在 code != 0 时抛出，组件层可读取 code/message 做提示 */
export class ApiError extends Error {
  /** 后端业务错误码（-1 表示网络层错误） */
  code: number

  constructor(code: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.code = code
  }
}

/**
 * 全局 axios 实例：
 * - baseURL 用相对路径 /api/v1，开发期由 Vite 代理到后端，生产期同源直达
 * - 响应拦截器自动拆掉统一包装，调用方直接拿到 data 字段
 */
export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
})

api.interceptors.response.use(
  (response) => {
    const envelope = response.data as ApiEnvelope<unknown>
    if (envelope.code !== 0) {
      throw new ApiError(envelope.code, envelope.message)
    }
    // 直接返回业务数据：调用方 const data = await api.get<T>(...) 拿到的就是 T
    return envelope.data as never
  },
  (error) => {
    // HTTP 层错误（网络断开/500 等）：尽量提取后端的统一包装信息
    const envelope = error.response?.data as ApiEnvelope<unknown> | undefined
    if (envelope?.message) {
      throw new ApiError(envelope.code ?? -1, envelope.message)
    }
    throw new ApiError(-1, error.message ?? '网络请求失败')
  },
)

/** 健康检查返回的数据结构 */
export interface HealthInfo {
  status: string
  app: string
  version: string
}

/** 探测后端是否在线（顶栏状态灯使用） */
export function fetchHealth(): Promise<HealthInfo> {
  return api.get('/health')
}

// ---------------- 设置中心 ----------------

/** 单个配置项（后端 settings_service.DEFAULTS 注册表的投影） */
export interface SettingItem {
  key: string
  value: unknown
  default: unknown
  label: string
  hint: string
  secret: boolean
  /** 值类型：bool / int / float / str（前端据此渲染开关/数字框/文本框） */
  type: 'bool' | 'int' | 'float' | 'str'
}

export function fetchSettings(): Promise<SettingItem[]> {
  return api.get('/settings')
}

export function saveSettings(values: Record<string, unknown>): Promise<void> {
  return api.put('/settings', { values })
}

/** 实时报价（测试报价源连通时返回） */
export interface QuoteData {
  symbol: string
  name: string
  price: number
  pct_change: number
  ts: string
}

export function testQuoteSource(): Promise<QuoteData[]> {
  return api.post('/settings/test-quote')
}

// ---------------- 数据任务 ----------------

/** 同步任务状态快照（REST 轮询兜底；实时更新走 WebSocket） */
export interface SyncStatus {
  state: 'idle' | 'running' | 'paused' | 'done' | 'failed' | 'cancelled'
  phase?: string
  total?: number
  done?: number
  failed?: number
  current?: string
  message?: string
}

export function fetchSyncStatus(): Promise<SyncStatus> {
  return api.get('/tasks/sync/status')
}

export function startInitHistory(rebuild = false): Promise<{ state: string }> {
  return api.post(`/tasks/sync/init?rebuild=${rebuild}`)
}

export function startDailySync(): Promise<{ state: string }> {
  return api.post('/tasks/sync/daily')
}

/** 立即同步今日：后台串行跑完整盘后流水线（日线→分钟→扩展→跑批→摘要→研报） */
export function syncToday(): Promise<{ state: string }> {
  return api.post('/tasks/sync/today')
}

export function pauseSync(): Promise<void> {
  return api.post('/tasks/sync/pause')
}

export function resumeSync(): Promise<void> {
  return api.post('/tasks/sync/resume')
}

export function cancelSync(): Promise<void> {
  return api.post('/tasks/sync/cancel')
}

/** 行情库库存统计（数据管理页顶部） */
export interface DataStats {
  stocks: number
  symbols_with_bars: number
  daily_bars: number
  index_daily: number
  boards: number
  board_members: number
  board_daily: number
  fundamentals_daily: number
  trade_calendar: number
  bar_date_min: string | null
  bar_date_max: string | null
}

export function fetchDataStats(): Promise<DataStats> {
  return api.get('/tasks/data/stats')
}

/** 同步历史记录 */
export interface SyncLogItem {
  id: number
  task_type: string
  status: string
  started_at: string
  finished_at: string
  total: number
  done: number
  failed: number
  message: string
}

/** 通用分页响应（后端 logs_paged 等统一结构） */
export interface Paged<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export function fetchSyncLogs(page = 1, pageSize = 10): Promise<Paged<SyncLogItem>> {
  return api.get(`/tasks/sync/logs?page=${page}&page_size=${pageSize}`)
}

// ---------------- 行情中心（M2） ----------------

/** 指数实时行情 */
export interface IndexQuote {
  symbol: string
  name: string
  price: number
  change: number
  pct_change: number
  amount: number
  ts: string
}

export function fetchIndices(): Promise<IndexQuote[]> {
  return api.get('/market/indices')
}

/** 涨跌分布桶 */
export interface DistributionBucket {
  label: string
  count: number
}

/** 盘面总览（最新交易日 EOD 统计） */
export interface MarketOverview {
  trade_date: string | null
  buckets: DistributionBucket[]
  up: number
  down: number
  flat: number
  limit_up: number
  limit_down: number
  total_amount: number
  amount_trend: { date: string; amount_yi: number }[]
}

export function fetchMarketOverview(): Promise<MarketOverview> {
  return api.get('/market/overview')
}

/** 板块热力项 */
export interface BoardHeatItem {
  code: string
  name: string
  pct_change: number
  amount: number
}

export function fetchBoardHeat(type: 'industry' | 'concept', limit = 40): Promise<BoardHeatItem[]> {
  return api.get(`/market/board-heat?type=${type}&limit=${limit}`)
}

/** 板块成分股行情 */
export interface BoardMember {
  symbol: string
  name: string
  is_st: boolean
  close: number | null
  pct_change: number | null
  amount: number | null
  turnover: number | null
  main_net: number | null
}

/** 板块详情（概览 + 走势 + 成分股） */
export interface BoardDetail {
  code: string
  name: string
  type: 'industry' | 'concept'
  trade_date: string | null
  pct_change: number | null
  amount: number | null
  trend: { date: string; pct_change: number }[]
  members: BoardMember[]
}

export function fetchBoardDetail(code: string): Promise<BoardDetail> {
  return api.get(`/market/board/${code}`)
}

/** 搜索结果项 */
export interface SearchResult {
  symbol: string
  name: string
  market: string
  pinyin: string
}

export function searchStocks(q: string): Promise<SearchResult[]> {
  return api.get(`/market/search?q=${encodeURIComponent(q)}`)
}

/** K 线（前复权） */
export interface KlineBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  pct_change: number
  turnover: number
}

export function fetchKline(symbol: string, limit = 500): Promise<KlineBar[]> {
  return api.get(`/market/kline/${symbol}?limit=${limit}`)
}

export function fetchIndexKline(symbol: string, limit = 500): Promise<KlineBar[]> {
  return api.get(`/market/index-kline/${symbol}?limit=${limit}`)
}

/** 个股聚合信息 */
export interface StockInfo {
  basic: {
    symbol: string
    name: string
    exchange: string
    market: string
    pinyin: string
    is_st: boolean
    status: string
  }
  boards: { code: string; name: string; type: string }[]
  fundamentals: {
    trade_date: string
    pe_ttm: number
    pb: number
    total_mv: number
    circ_mv: number
  } | null
  quote: FullQuote | null
}

/** 完整报价（腾讯源字段） */
export interface FullQuote {
  symbol: string
  name: string
  price: number
  prev_close: number
  open: number
  high: number
  low: number
  volume: number
  amount: number
  pct_change: number
  change: number
  turnover: number
  pe_ttm: number
  pb: number
  total_mv: number
  circ_mv: number
  ts: string
}

export function fetchStockInfo(symbol: string): Promise<StockInfo> {
  return api.get(`/market/stock/${symbol}`)
}

// ---------------- 自选股（M2） ----------------

export interface WatchlistItem {
  id: number
  symbol: string
  name: string
  note: string
  created_at: string
  quote: FullQuote | null
  /** 最近一次 AI 诊断决策摘要（从未诊断为 null）；与持仓表共用结构 */
  ai?: HoldingAi | null
}

export function fetchWatchlist(): Promise<WatchlistItem[]> {
  return api.get('/watchlist')
}

export function addWatchlist(symbol: string): Promise<{ id: number; symbol: string }> {
  return api.post('/watchlist', { symbol })
}

export function removeWatchlist(symbol: string): Promise<void> {
  return api.delete(`/watchlist/${symbol}`)
}

// ---------------- 持仓诊断（M6） ----------------

/** 一条持仓明细（报价缺失时 price/pnl 等为 null，按成本价展示） */
export interface HoldingItem {
  id: number
  symbol: string
  name: string
  shares: number
  cost_price: number
  note: string
  created_at: string
  price: number | null
  pct_change: number | null
  /** 市值（无报价时按成本计） */
  market_value: number
  /** 浮动盈亏额 */
  pnl: number | null
  /** 浮动盈亏比例（%） */
  pnl_pct: number | null
  /** 今日盈亏额 */
  day_pnl: number | null
  /** 仓位占比（%，按持仓市值） */
  weight: number
  /** 占账户总资金比例（%，未设置总资金为 null） */
  cap_weight: number | null
  /** 最近一次 AI 诊断决策摘要（从未诊断为 null） */
  ai: HoldingAi | null
}

/** 持仓表内联的最近一次诊断摘要 */
export interface HoldingAi {
  rating: ChiefDecision['rating']
  action: '割' | '守' | '补' | ''
  score: number
  position_pct: number
  target_price: number
  stop_loss_price: number
  mode: DiagnosisMode | null
  risk_level: 'pass' | 'warn' | 'block' | null
  run_id: number
  updated_at: string
}

/** 组合总览汇总 */
export interface PortfolioOverview {
  count: number
  total_value: number
  total_cost: number
  total_pnl: number
  total_pnl_pct: number
  day_pnl: number
  /** 账户总资金（元，未设置为 null） */
  total_capital: number | null
  /** 可用现金 = 总资金 - 总市值（未设置为 null，可为负=满仓超配） */
  cash: number | null
  /** 仓位占比 = 总市值 / 总资金 ×100（未设置为 null） */
  invested_ratio: number | null
}

export function fetchHoldings(): Promise<{ items: HoldingItem[]; overview: PortfolioOverview }> {
  return api.get('/holdings')
}

/** 设置账户总资金（元，0=清除）。 */
export function setTotalCapital(totalCapital: number): Promise<{ total_capital: number }> {
  return api.put('/holdings/capital', { total_capital: totalCapital })
}

export function addHolding(body: {
  symbol: string
  shares: number
  cost_price: number
  note?: string
}): Promise<{ id: number; symbol: string }> {
  return api.post('/holdings', body)
}

export function updateHolding(
  id: number,
  body: { shares: number; cost_price: number; note?: string },
): Promise<void> {
  return api.put(`/holdings/${id}`, body)
}

export function removeHolding(id: number): Promise<void> {
  return api.delete(`/holdings/${id}`)
}

/** 批量导入持仓（CSV）：按代码 upsert，逐行容错 */
export interface ImportResult {
  added: number
  updated: number
  failed: { symbol: string; error: string }[]
}

export function importHoldings(
  items: { symbol: string; shares: number; cost_price: number; note?: string }[],
): Promise<ImportResult> {
  return api.post('/holdings/import', { items })
}

/** 发起带持仓上下文的 AI 诊断（评级落到割/守/补） */
export function diagnoseHolding(
  symbol: string,
  mode: DiagnosisMode = 'deep',
): Promise<{ run_id: number; reused: boolean }> {
  return api.post(`/holdings/${symbol}/diagnose?mode=${mode}`, undefined, { timeout: 60_000 })
}

// ---------------- 策略引擎（M3） ----------------

/** 策略广场卡片（后端 builtin.py 注册表的投影） */
export interface StrategyMeta {
  id: string
  /** 白话名（如「趋势启动」） */
  name: string
  /** 技术名（如「均线多头排列」） */
  tech_name: string
  category: string
  /** 适用周期：短线 / 波段 / 中线 */
  period: string
  /** 风险等级 1~5 */
  risk: number
  summary: string
  /** 大白话讲解（含适合场景与失效场景） */
  explain: string
  available: boolean
  unavailable_reason: string
  /** 板块类特殊策略：选股可用，但不支持机械历史回测 */
  special: boolean
}

export function fetchStrategies(): Promise<StrategyMeta[]> {
  return api.get('/strategies')
}

/** 因子注册表项（自定义条件构建器的数据源） */
export interface FactorMeta {
  name: string
  label: string
  kind: 'number' | 'bool'
  unit: string
  desc: string
}

export function fetchFactors(): Promise<FactorMeta[]> {
  return api.get('/strategies/factors')
}

/** 白话描述 AI 解析结果：可直接扫描/保存的条件树 */
export interface AiParsedStrategy {
  name: string
  summary: string
  condition: Record<string, unknown>
  /** 用户提到但系统没有对应因子的条件（诚实告知，不硬凑） */
  unmatched: string[]
}

export function aiParseStrategy(text: string): Promise<AiParsedStrategy> {
  return api.post('/strategies/ai-parse', { text })
}

/** 单只股票命中的某个策略与人话化理由 */
export interface StrategyHit {
  strategy_id: string
  name: string
  reasons: string[]
}

/** 扫描结果中的一只股票 */
export interface ScanItem {
  symbol: string
  name: string
  close: number | null
  pct_change: number | null
  turnover: number | null
  vol_ratio: number | null
  /** 共振评分：命中策略数 / 所选策略数 × 100 */
  score: number
  hit_count: number
  hits: StrategyHit[]
}

export interface ScanResult {
  trade_date: string
  total: number
  items: ScanItem[]
}

/** 执行选股扫描（多选共振；require_all=true 表示全部命中才入选） */
export function runStrategies(body: {
  strategy_ids: string[]
  custom_condition?: Record<string, unknown>
  require_all?: boolean
  limit?: number
}): Promise<ScanResult> {
  return api.post('/strategies/run', body)
}

/** 手动触发全策略跑批存档 */
export function runStrategyBatch(): Promise<{
  trade_date: string
  strategies: number
  signals: number
}> {
  return api.post('/strategies/batch')
}

/** 今日信号汇总（首页卡 + 选股结果页） */
export interface TodaySignals {
  trade_date: string | null
  items: {
    symbol: string
    name: string
    close: number | null
    hit_count: number
    strategies: string[]
  }[]
  /** 每个策略当日命中只数（选股结果页筛选器） */
  by_strategy: { id: string; name: string; count: number }[]
}

export function fetchTodaySignals(opts?: { top?: number; strategies?: string[] }): Promise<TodaySignals> {
  const params = new URLSearchParams()
  if (opts?.top) params.set('top', String(opts.top))
  if (opts?.strategies?.length) params.set('strategies', opts.strategies.join(','))
  const qs = params.toString()
  return api.get(`/strategies/today-signals${qs ? `?${qs}` : ''}`)
}

// ---------------- 历史推演 / 回测（M4） ----------------

/** 时光机：单只股票在某持有期的收益（holding=true 表示数据不足仍在持有） */
export interface SnapshotReturn {
  pct: number
  holding: boolean
}

/** 时光机：单只股票明细 */
export interface SnapshotDetail {
  symbol: string
  name: string
  hit_count: number
  signal_close: number
  buy_open: number
  returns: Record<string, SnapshotReturn>
}

/** 时光机：某持有期的汇总统计 */
export interface SnapshotSummary {
  trades: number
  win_rate: number | null
  avg_pct: number | null
  median_pct: number | null
  best_pct: number | null
  worst_pct: number | null
  /** 沪深300 同期收益（对照用） */
  benchmark_pct: number | null
}

export interface SnapshotResult {
  run_id: number
  signal_date: string
  hold_days: number[]
  total_hits: number
  evaluated: number
  skipped_limit_up: number
  skipped_suspended: number
  summary: Record<string, SnapshotSummary>
  details: SnapshotDetail[]
  note: string
}

/** 策略时光机：历史某天按策略买入，持有 N 天的成绩单（重计算，最长约 1 分钟） */
export function runSnapshotBacktest(body: {
  strategy_ids: string[]
  custom_condition?: Record<string, unknown>
  require_all?: boolean
  signal_date: string
  hold_days: number[]
}): Promise<SnapshotResult> {
  return api.post('/backtest/snapshot', body, { timeout: 300_000 })
}

/** 调仓回测：资金曲线点 */
export interface CurvePoint {
  date: string
  value: number
}

/** 调仓回测：单次换仓记录 */
export interface RebalanceTrade {
  signal_date: string
  exec_date: string
  buys: { symbol: string; name: string; price: number; shares: number }[]
  sells: { symbol: string; name: string; price: number }[]
  holdings_count: number
}

export interface RebalanceMetrics {
  total_return_pct: number
  annual_return_pct: number
  max_drawdown_pct: number
  sharpe: number
  period_win_rate: number | null
  benchmark_return_pct: number | null
  rebalance_count: number
}

export interface RebalanceResult {
  run_id: number
  start: string
  end: string
  freq_days: number
  top_n: number
  init_cash: number
  final_value: number
  metrics: RebalanceMetrics
  curve: CurvePoint[]
  benchmark_curve: CurvePoint[]
  trades: RebalanceTrade[]
  note: string
}

/** 定期调仓回测：每 N 个交易日按策略换仓的资金曲线模拟（重计算，最长约 1 分钟） */
export function runRebalanceBacktest(body: {
  strategy_ids: string[]
  custom_condition?: Record<string, unknown>
  require_all?: boolean
  start: string
  end: string
  freq_days: number
  top_n: number
  init_cash: number
}): Promise<RebalanceResult> {
  return api.post('/backtest/rebalance', body, { timeout: 300_000 })
}

/** 历史回测记录（摘要） */
export interface BacktestRunBrief {
  id: number
  kind: 'snapshot' | 'rebalance'
  created_at: string
  strategy_ids: string[]
  signal_date?: string
  evaluated?: number
  range?: string
  total_return_pct?: number
}

export function fetchBacktestRuns(): Promise<BacktestRunBrief[]> {
  return api.get('/backtest/runs')
}

export interface BacktestRunDetail {
  id: number
  kind: 'snapshot' | 'rebalance'
  params: Record<string, unknown>
  result: SnapshotResult | RebalanceResult
  created_at: string
}

export function fetchBacktestRun(id: number): Promise<BacktestRunDetail> {
  return api.get(`/backtest/runs/${id}`)
}

export function deleteBacktestRun(id: number): Promise<void> {
  return api.delete(`/backtest/runs/${id}`)
}

// ---------------- 消息中心（M5） ----------------

/** 一条新闻/快讯 */
export interface NewsItem {
  code: string
  title: string
  summary: string
  publish_time: string
  media: string
  url: string
  /** 关联的 A 股代码（快讯流才有） */
  stocks: string[] | null
}

export interface NewsFeed {
  items: NewsItem[]
  /** 翻更早内容的游标（传给下一次 fetchNewsFeed） */
  next_cursor: string
}

/** 7×24 快讯流。column: 102=全部 101=重点 */
export function fetchNewsFeed(cursor = '', column: '101' | '102' = '102'): Promise<NewsFeed> {
  return api.get('/news/feed', { params: { cursor, column } })
}

/** 个股新闻（按时间倒序，最多 20 条） */
export function fetchStockNews(symbol: string): Promise<NewsItem[]> {
  return api.get(`/news/stock/${symbol}`)
}

/** AI 情绪分析结果 */
export interface SentimentResult {
  symbol: string
  date: string
  /** 0~100：>70 偏多，<30 偏空 */
  score: number
  label: '利好' | '利空' | '中性'
  summary: string
  positive: string[]
  negative: string[]
  news_titles: string[]
  analyzed_at: string
  from_cache?: boolean
}

/** 读当日情绪缓存（null = 尚未分析过） */
export function fetchSentiment(symbol: string): Promise<SentimentResult | null> {
  return api.get(`/news/sentiment/${symbol}`)
}

/** 触发 AI 情绪分析（LLM 调用 5~30 秒；40050=AI 未配置） */
export function analyzeSentiment(symbol: string, name = ''): Promise<SentimentResult> {
  return api.post(`/news/sentiment/${symbol}`, undefined, {
    params: { name },
    timeout: 120_000,
  })
}

/** 批量读当日情绪缓存（自选页用） */
export function fetchSentimentBatch(symbols: string[]): Promise<Record<string, SentimentResult>> {
  return api.get('/news/sentiment-batch', { params: { symbols: symbols.join(',') } })
}

/** 测试 AI 连接（设置中心按钮） */
export function testAiConnection(): Promise<{ ok: boolean; reply: string }> {
  return api.post('/news/ai/test', undefined, { timeout: 60_000 })
}

/** 当日 AI 盘面摘要（复用情绪结果结构；label 为 普涨/偏暖/分化/偏冷/普跌） */
export function fetchMarketSummary(): Promise<SentimentResult | null> {
  return api.get('/news/market-summary')
}

/** 生成当日 AI 盘面摘要（已有缓存直接返回） */
export function generateMarketSummary(): Promise<SentimentResult> {
  return api.post('/news/market-summary', undefined, { timeout: 120_000 })
}

// ---------------- 扩展数据同步（M5） ----------------

export interface ExtTableStat {
  rows: number
  latest: string | null
}

export interface ExtSyncStatus {
  running: boolean
  last_run: Record<string, unknown> | null
  stats: {
    fund_flow: ExtTableStat
    dragon_tiger: ExtTableStat
    earnings: ExtTableStat
    popularity: ExtTableStat
    minute_bars: ExtTableStat
  }
  /** 5分钟线同步状态（独立服务，状态合并返回） */
  minute: {
    running: boolean
    progress: { trade_date: string; done: number; total: number } | null
    last_run: Record<string, unknown> | null
  }
}

export function fetchExtSyncStatus(): Promise<ExtSyncStatus> {
  return api.get('/tasks/ext/status')
}

/** 手动触发扩展数据同步（资金流/龙虎榜/业绩预告/人气榜，约 3~4 分钟） */
export function triggerExtSync(): Promise<{ state: string }> {
  return api.post('/tasks/ext/sync')
}

/** 手动触发当日 5 分钟线同步（全市场约 2~4 分钟） */
export function triggerMinuteSync(): Promise<{ state: string }> {
  return api.post('/tasks/ext/minute-sync')
}

/** 个股资金流历史（近 N 日） */
export interface FundFlowDay {
  date: string
  main_net: number
  main_pct: number
  net_3d: number
  net_5d: number
  net_10d: number
}

export function fetchStockFundFlow(symbol: string): Promise<FundFlowDay[]> {
  return api.get(`/market/fundflow/${symbol}`)
}

// ---------------- 多角色 AI 诊股（M6） ----------------

/** 诊断模式：deep=完整专业版工作流；quick=快速模式 */
export type DiagnosisMode = 'deep' | 'quick'

/** 单个分析师的报告 */
export interface AnalystReport {
  label: string
  score: number
  stance: 'bullish' | 'bearish' | 'neutral'
  summary: string
  points: string[]
  /** 引用的理论出处（如：缠论·背驰 / CANSLIM·RPS） */
  theory?: string
  /** 宏观分析师专属：择时系数（总仓位闸门 0.1~1.3） */
  timing_coef?: number
  /** 推理模型的思考过程（DeepSeek-R1 类；非推理模型为空） */
  thinking?: string
  failed?: boolean
}

/** 多空辩手的陈词 */
export interface DebateSpeech {
  argument: string
  key_points: string[]
  /** 推理模型的思考过程 */
  thinking?: string
  failed?: boolean
}

/** 风险闸门结果（Python 硬规则） */
export interface RiskGate {
  level: 'pass' | 'warn' | 'block'
  flags: string[]
  note: string
}

/** 研究总监结论 */
export interface ResearchConclusion {
  stance: 'bullish' | 'bearish' | 'neutral'
  conviction: number
  summary: string
  key_points: string[]
  thinking?: string
}

/** 交易员方案 */
export interface TraderPlan {
  buy_zone: string
  target_price: number
  stop_loss_price: number
  position_pct: number
  horizon: string
  summary: string
  theory?: string
  thinking?: string
}

/** 风控委员单方意见 */
export interface RiskMemberView {
  label: string
  stance: string
  position_adjust: number
  summary: string
  thinking?: string
}

/** 组合经理最终决策（含交易价位/操作清单/理论附录） */
export interface ChiefDecision {
  rating: '强烈买入' | '买入' | '持有' | '减仓' | '卖出'
  /** 持仓诊断时的「割/守/补」动作；普通诊股为空 */
  action?: '割' | '守' | '补' | ''
  score: number
  confidence: number
  position_pct: number
  horizon: string
  entry_note: string
  stop_loss_pct: number
  target_price?: number
  stop_loss_price?: number
  buy_zone?: string
  summary: string
  reasons: string[]
  risks: string[]
  checklist?: string[]
  theory_refs?: string[]
  mode?: DiagnosisMode
  timing_coef?: number
  risk_level?: 'pass' | 'warn' | 'block'
  risk_flags?: string[]
}

/** 一次诊股的完整记录 */
export interface DiagnosisRun {
  run_id: number
  symbol: string
  name: string
  status: 'running' | 'done' | 'failed'
  result: Partial<ChiefDecision>
  error: string
  cost_seconds: number
  model: string
  created_at: string
  stages?: {
    riskgate?: RiskGate
    tech?: AnalystReport
    fund?: AnalystReport
    news?: AnalystReport
    fundamental?: AnalystReport
    macro?: AnalystReport
    quant?: AnalystReport
    sector?: AnalystReport
    bull?: DebateSpeech
    bear?: DebateSpeech
    research?: ResearchConclusion
    trader?: TraderPlan
    risk_agg?: RiskMemberView
    risk_neu?: RiskMemberView
    risk_con?: RiskMemberView
    /** 组合经理阶段存档（决策字段 + thinking） */
    chief?: Partial<ChiefDecision> & { thinking?: string }
  }
}

/** 发起多角色诊股（后台执行，进度走 WS type="diagnosis"） */
export function startDiagnosis(
  symbol: string,
  mode: DiagnosisMode = 'deep',
  asOf?: string,
): Promise<{ run_id: number; reused: boolean }> {
  const q = asOf ? `?mode=${mode}&as_of=${asOf}` : `?mode=${mode}`
  return api.post(`/ai/diagnosis/${symbol}${q}`, undefined, { timeout: 60_000 })
}

/** 回测校验：as_of 之后真实走势 vs AI 当时判断（仅回溯诊断有效） */
export interface DiagnosisVerify {
  as_of: string
  base_price: number
  bars: number
  last_date: string
  /** 各持有窗口的真实涨跌幅（%，数据不足为 null） */
  windows: { d5: number | null; d10: number | null; d20: number | null; d60: number | null }
  /** 区间最大涨幅 / 最大跌幅（相对基准，%） */
  max_gain: number | null
  max_drop: number | null
  target_price: number | null
  stop_loss_price: number | null
  /** 目标价/止损价首次触及的交易日序号（未触及为 null） */
  target_hit_day: number | null
  stop_hit_day: number | null
  /** as_of 之后的真实日线（date/open/high/low/close/pct_change） */
  forward: { date: string; open: number; high: number; low: number; close: number; pct_change: number }[]
}

export function verifyDiagnosis(runId: number): Promise<DiagnosisVerify> {
  return api.get(`/ai/diagnosis/run/${runId}/verify`)
}

/** 查询一次诊股的完整状态（WS 断线时的轮询兜底） */
export function fetchDiagnosisRun(runId: number): Promise<DiagnosisRun> {
  return api.get(`/ai/diagnosis/run/${runId}`)
}

/** 某股最近一次诊股（null = 从未诊过） */
export function fetchLatestDiagnosis(symbol: string): Promise<DiagnosisRun | null> {
  return api.get(`/ai/diagnosis/latest/${symbol}`)
}

/** 诊股历史列表（不含阶段明细；result 含评级可做列表徽章） */
export function fetchDiagnosisHistory(symbol?: string, limit = 20): Promise<DiagnosisRun[]> {
  return api.get('/ai/diagnosis/history', { params: { symbol: symbol ?? '', limit } })
}

/** 诊股报告 Markdown 导出下载地址（浏览器直接打开触发下载） */
export function diagnosisExportUrl(runId: number): string {
  return `/api/v1/ai/diagnosis/run/${runId}/export`
}

/** AI 研报库：分页归档列表（可按股票/状态过滤） */
export function fetchDiagnosisLibrary(params: {
  page?: number
  pageSize?: number
  symbol?: string
  status?: string
}): Promise<Paged<DiagnosisRun>> {
  return api.get('/ai/diagnosis/library', {
    params: {
      page: params.page ?? 1,
      page_size: params.pageSize ?? 20,
      symbol: params.symbol ?? '',
      status: params.status ?? '',
    },
  })
}

/** 定时研报：手动触发一次（后台执行，结果进研报库） */
export function runReport(): Promise<{ started: boolean }> {
  return api.post('/ai/reports/run')
}

/** 最近一次定时研报运行摘要 */
export interface ReportLastRun {
  ran: boolean
  count: number
  reason: string
  generated_at: string
  push_results: { channel: string; ok: boolean; error: string }[]
}

export function fetchLastReport(): Promise<ReportLastRun | null> {
  return api.get('/ai/reports/last')
}

/** 测试推送通道：向所有已配置通道发测试消息 */
export interface NotifyTestResult {
  results: { channel: string; ok: boolean; error: string }[]
  message?: string
}

export function testNotify(): Promise<NotifyTestResult> {
  return api.post('/settings/test-notify', undefined, { timeout: 30_000 })
}

// ---------------- AI 提示词管理（M6） ----------------

export interface PromptItem {
  id: string
  label: string
  desc: string
  placeholders: Record<string, string>
  default_template: string
  template: string
  customized: boolean
}

export function fetchPrompts(): Promise<PromptItem[]> {
  return api.get('/ai/prompts')
}

export function savePrompt(id: string, template: string): Promise<{ saved: boolean }> {
  return api.put(`/ai/prompts/${id}`, { template })
}

export function resetPrompt(id: string): Promise<{ reset: boolean }> {
  return api.delete(`/ai/prompts/${id}`)
}
