/** 行情数字格式化工具（全站统一口径）。 */

/** 成交额：元 → "1.23万亿 / 4567亿 / 8.9亿" */
export function formatAmount(yuan: number): string {
  if (yuan >= 1e12) return `${(yuan / 1e12).toFixed(2)}万亿`
  if (yuan >= 1e8) return `${(yuan / 1e8).toFixed(yuan >= 1e11 ? 0 : 1)}亿`
  if (yuan >= 1e4) return `${(yuan / 1e4).toFixed(0)}万`
  return yuan.toFixed(0)
}

/** 市值：元 → "2.1万亿 / 345亿" */
export const formatMarketValue = formatAmount

/** 涨跌幅：带正负号的百分比 */
export function formatPct(pct: number): string {
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

/** 涨跌语义色类名（A 股红涨绿跌） */
export function pctColor(value: number): string {
  if (value > 0) return 'text-up'
  if (value < 0) return 'text-down'
  return 'text-flat'
}

/** 价格：保留 2 位（>1000 的指数也适用） */
export function formatPrice(price: number): string {
  return price.toFixed(2)
}
