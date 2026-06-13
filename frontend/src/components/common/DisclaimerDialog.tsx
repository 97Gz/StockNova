import { useState } from 'react'

import { Portal } from '@/components/common/Portal'

const STORAGE_KEY = 'stocknova-disclaimer-ack'

/**
 * 首次启动免责声明（PRD 第 4 节合规要求）：
 * 用户确认一次后写入 localStorage，之后不再弹出。
 */
export function DisclaimerDialog() {
  const [acknowledged, setAcknowledged] = useState(() => localStorage.getItem(STORAGE_KEY) === '1')

  if (acknowledged) return null

  return (
    <Portal>
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-card border bg-popover p-6 shadow-2xl">
        <h2 className="mb-3 text-base font-semibold">使用前请知晓</h2>
        <div className="space-y-2 text-[13px] leading-6 text-muted-foreground">
          <p>1. 星智股是个人投资研究工具，所有数据来自公开渠道，可能存在延迟或误差。</p>
          <p>
            2. 本应用的策略信号、回测结果与 AI 分析均为<b>数据推演与多角度参考</b>，
            <b className="text-up">不构成任何投资建议</b>。
          </p>
          <p>3. 股市有风险，入市需谨慎。请独立判断并自行承担投资决策的全部责任。</p>
        </div>
        <button
          onClick={() => {
            localStorage.setItem(STORAGE_KEY, '1')
            setAcknowledged(true)
          }}
          className="mt-5 h-10 w-full rounded-[10px] bg-primary text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
        >
          我已知晓并同意
        </button>
      </div>
    </div>
    </Portal>
  )
}
