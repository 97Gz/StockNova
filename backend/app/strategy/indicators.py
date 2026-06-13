"""技术指标库：全市场面板（panel）上的向量化计算。

核心约定
--------
所有函数的输入是一个"面板 DataFrame"：全市场所有股票的日线纵向堆叠，
已按 (symbol, trade_date) 排序。列至少包含：
    symbol, date, open, high, low, close, volume, amount, turnover, pct_change

性能设计
--------
对 170 万行逐股循环计算太慢，这里分两类向量化：
- 窗口类（rolling/shift）：对"整列"计算（C 速度），再把每只股票开头
  warmup 行置 NaN——跨股票污染只出现在组头部，掩码后与逐组计算完全一致；
- 平滑类（EMA/MACD/RSI/KDJ 的 ewm）：指数平均"无限记忆"，掩码无法清除
  跨组污染（实测 span=26 时 100 行后误差仍达 1e-2），必须用 pandas 的
  GroupBy.ewm（Cython 分组实现，结果与逐组计算严格一致，170 万行约百毫秒级）。
"""

import numpy as np
import pandas as pd

# ewm 指标的预热行数：上市初期的平滑值不稳定，统一掩码（与窗口类指标对齐口径）
EWM_WARMUP = 33


def group_pos(df: pd.DataFrame) -> np.ndarray:
    """每行在本股票内的序号（0 起）——掩码的基础。"""
    return df.groupby("symbol", sort=False).cumcount().to_numpy()


def _mask(series: pd.Series, pos: np.ndarray, warmup: int) -> pd.Series:
    """把每只股票开头 warmup 行置为 NaN（清掉跨组污染/不足窗口的值）。"""
    out = series.copy()
    out[pos < warmup] = np.nan
    return out


def group_ewm(col: pd.Series, symbols: pd.Series, **ewm_kwargs) -> pd.Series:
    """组感知的指数平滑（每只股票独立计算，无跨组污染）。

    ewm_kwargs 透传给 pandas（span= / alpha= / com=）。
    """
    out = col.groupby(symbols.to_numpy(), sort=False).ewm(adjust=False, **ewm_kwargs).mean()
    # GroupBy.ewm 返回 (组, 原索引) 双层索引；去掉组层后按原索引排序还原
    return out.reset_index(level=0, drop=True).sort_index()


def shift(col: pd.Series, pos: np.ndarray, k: int = 1) -> pd.Series:
    """组感知的 shift(k)。"""
    return _mask(col.shift(k), pos, k)


def sma(col: pd.Series, pos: np.ndarray, n: int) -> pd.Series:
    """简单移动平均 MA(n)。"""
    return _mask(col.rolling(n).mean(), pos, n - 1)


def rolling_max(col: pd.Series, pos: np.ndarray, n: int) -> pd.Series:
    return _mask(col.rolling(n).max(), pos, n - 1)


def rolling_min(col: pd.Series, pos: np.ndarray, n: int) -> pd.Series:
    return _mask(col.rolling(n).min(), pos, n - 1)


def rolling_sum(col: pd.Series, pos: np.ndarray, n: int) -> pd.Series:
    return _mask(col.rolling(n).sum(), pos, n - 1)


def ema(col: pd.Series, symbols: pd.Series, pos: np.ndarray, n: int) -> pd.Series:
    """指数移动平均 EMA(n)，span 语义与主流行情软件一致。"""
    return _mask(group_ewm(col, symbols, span=n), pos, EWM_WARMUP)


def macd(
    close: pd.Series, symbols: pd.Series, pos: np.ndarray
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD(12,26,9)：返回 (DIF, DEA, 柱体 MACD)。

    柱体 = (DIF - DEA) × 2，与同花顺/东财显示口径一致。
    """
    dif = group_ewm(close, symbols, span=12) - group_ewm(close, symbols, span=26)
    dea = group_ewm(dif, symbols, span=9)
    hist = (dif - dea) * 2
    return (
        _mask(dif, pos, EWM_WARMUP),
        _mask(dea, pos, EWM_WARMUP),
        _mask(hist, pos, EWM_WARMUP),
    )


def rsi(close: pd.Series, symbols: pd.Series, pos: np.ndarray, n: int = 14) -> pd.Series:
    """RSI(n)，Wilder 平滑（alpha=1/n），与同花顺口径一致。"""
    delta = close.diff()
    # 组头第一行的 diff 是跨股票的，置 0（不参与涨跌统计）
    delta[pos < 1] = 0.0
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = group_ewm(gain, symbols, alpha=1 / n)
    avg_loss = group_ewm(loss, symbols, alpha=1 / n)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # 全涨无跌时 avg_loss=0 → RSI=100
    out = out.where(avg_loss != 0, 100.0)
    return _mask(out, pos, EWM_WARMUP)


def kdj(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    symbols: pd.Series,
    pos: np.ndarray,
    n: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """KDJ(9,3,3)：返回 (K, D, J)。K/D 用 com=2 的 ewm（即 1/3 平滑），行业口径。"""
    hn = rolling_max(high, pos, n)
    ln = rolling_min(low, pos, n)
    rsv = (close - ln) / (hn - ln).replace(0, np.nan) * 100
    # RSV 前 n-1 行是 NaN，ewm 跳过 NaN 后从首个有效值开始平滑（与逐组一致）
    k = group_ewm(rsv, symbols, com=2)
    d = group_ewm(k, symbols, com=2)
    j = 3 * k - 2 * d
    warm = max(EWM_WARMUP, n)
    return _mask(k, pos, warm), _mask(d, pos, warm), _mask(j, pos, warm)


def boll(
    close: pd.Series, pos: np.ndarray, n: int = 20, k: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """布林带 BOLL(n,k)：返回 (上轨, 中轨, 下轨)。"""
    mid = sma(close, pos, n)
    std = _mask(close.rolling(n).std(ddof=0), pos, n - 1)
    return mid + k * std, mid, mid - k * std


def bias(close: pd.Series, pos: np.ndarray, n: int = 20) -> pd.Series:
    """乖离率 BIAS(n) = (收盘 - MA_n) / MA_n × 100%。"""
    ma_n = sma(close, pos, n)
    return (close - ma_n) / ma_n * 100


def volume_ratio(volume: pd.Series, pos: np.ndarray, n: int = 5) -> pd.Series:
    """量比（日线近似）：当日成交量 / 前 n 日平均成交量。

    严格定义的量比基于盘中分钟数据，日线场景用前 5 日均量替代，
    这是所有盘后选股工具的通行口径。
    """
    prev_avg = shift(sma(volume, pos, n), pos, 1)
    return volume / prev_avg.replace(0, np.nan)


def cross_above(a: pd.Series, b: pd.Series, pos: np.ndarray) -> pd.Series:
    """金叉：昨日 a<=b 且今日 a>b。返回布尔 Series。"""
    prev_a, prev_b = shift(a, pos, 1), shift(b, pos, 1)
    return (prev_a <= prev_b) & (a > b)


def within_last(cond: pd.Series, pos: np.ndarray, n: int) -> pd.Series:
    """近 n 日内（含今日）条件至少发生过一次。cond 为布尔 Series。"""
    hit = cond.fillna(False).astype(float).rolling(n, min_periods=1).max()
    return _mask(hit, pos, 0).fillna(0) > 0


def count_last(cond: pd.Series, pos: np.ndarray, n: int) -> pd.Series:
    """近 n 日内条件发生的次数。"""
    return cond.fillna(False).astype(float).rolling(n, min_periods=1).sum()


def streak_true(cond: pd.Series, pos: np.ndarray, n: int) -> pd.Series:
    """连续 n 日条件为真（含今日）。"""
    return cond.fillna(False).astype(float).rolling(n, min_periods=n).min().fillna(0) > 0
