"""条件树引擎：JSON 条件树在因子宽表上的向量化求值。

条件树语法（自定义策略构建器与内置策略共用，也是 M7 自然语言选股的目标结构）：
    {"all": [子条件, ...]}                            -- AND
    {"any": [子条件, ...]}                            -- OR
    {"factor": "rsi14", "op": "<", "value": 25}       -- 因子 vs 常数
    {"factor": "ma5", "op": ">", "ref": "ma20"}       -- 因子 vs 因子
    {"factor": "pe_ttm", "op": "between", "value": [0, 15]}
    {"factor": "ma_bull_arrange", "op": "is_true"}    -- 布尔因子
"""

from typing import Any

import pandas as pd

from app.strategy.factors import FACTOR_META

OPS = {">", ">=", "<", "<=", "between", "is_true", "is_false"}


class ConditionError(ValueError):
    """条件树不合法（未知因子/操作符、结构错误）。"""


def _label(factor: str) -> str:
    meta = FACTOR_META.get(factor, {})
    return meta.get("label", factor)


def _fmt(v: float) -> str:
    """数值的人话格式：整数不带小数点，小数保留两位。"""
    if v is None or pd.isna(v):
        return "-"
    return f"{v:g}" if float(v).is_integer() else f"{v:.2f}"


def evaluate(node: dict[str, Any], table: pd.DataFrame) -> tuple[pd.Series, list[dict]]:
    """求值条件树。

    返回：
    - mask：布尔 Series（index=symbol），该节点是否满足
    - leaves：所有叶子的求值明细 [{"text", "mask", "factor"}]，
      供"原因明细"按股票回放（text 含因子中文名与阈值）。
    """
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        children = node[key]
        if not isinstance(children, list) or not children:
            raise ConditionError(f"'{key}' 必须是非空数组")
        masks, leaves = [], []
        for child in children:
            m, ls = evaluate(child, table)
            masks.append(m)
            leaves.extend(ls)
        combined = masks[0]
        for m in masks[1:]:
            combined = (combined & m) if key == "all" else (combined | m)
        return combined, leaves

    # ---- 叶子节点 ----
    factor = node.get("factor")
    op = node.get("op")
    if factor not in FACTOR_META:
        raise ConditionError(f"未知因子: {factor}")
    if op not in OPS:
        raise ConditionError(f"未知操作符: {op}")
    if factor not in table.columns:
        raise ConditionError(f"因子 {factor} 不在当前因子表中")

    col = table[factor]
    label = _label(factor)

    if op == "is_true":
        mask = col.fillna(False).astype(bool)
        text = label
    elif op == "is_false":
        mask = ~col.fillna(False).astype(bool)
        text = f"非{label}"
    elif op == "between":
        bounds = node.get("value")
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ConditionError(f"between 需要 [低, 高] 两个值: {factor}")
        lo, hi = float(bounds[0]), float(bounds[1])
        mask = col.between(lo, hi)
        text = f"{label}在 {_fmt(lo)}~{_fmt(hi)} 之间"
    elif "ref" in node:
        ref = node["ref"]
        if ref not in table.columns:
            raise ConditionError(f"未知参照因子: {ref}")
        other = table[ref]
        mask = _compare(col, other, op)
        text = f"{label} {op} {_label(ref)}"
    else:
        value = float(node.get("value", 0))
        mask = _compare(col, value, op)
        text = f"{label} {op} {_fmt(value)}"

    mask = mask.fillna(False)
    return mask, [{"text": text, "mask": mask, "factor": factor}]


def _compare(col: pd.Series, other: Any, op: str) -> pd.Series:
    if op == ">":
        return col > other
    if op == ">=":
        return col >= other
    if op == "<":
        return col < other
    return col <= other


def describe_hit(symbol: str, leaves: list[dict], table: pd.DataFrame) -> list[str]:
    """对单只命中股票生成人话原因：每个叶子附实际值与是否满足。"""
    reasons = []
    for leaf in leaves:
        ok = bool(leaf["mask"].get(symbol, False))
        factor = leaf["factor"]
        meta = FACTOR_META.get(factor, {})
        val = table.at[symbol, factor] if factor in table.columns else None
        if meta.get("kind") == "number" and val is not None and not pd.isna(val):
            unit = meta.get("unit", "")
            detail = f"{leaf['text']}（当前 {_fmt(float(val))}{unit}）"
        else:
            detail = leaf["text"]
        reasons.append(f"{'✓' if ok else '✗'} {detail}")
    return reasons
