import { useMutation } from '@tanstack/react-query'
import { CornerDownLeft, Search } from 'lucide-react'
import { useCallback, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { Portal } from '@/components/common/Portal'
import type { SearchResult } from '@/lib/api'
import { searchStocks } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * 全局搜索命令面板（Ctrl+K）：
 * 输入代码 / 拼音首字母 / 名称 → 防抖搜索 → ↑↓ 选择 → Enter 进个股页。
 * 全键盘可达；遮罩点击或 Esc 关闭。
 * 关闭时整个面板卸载，重新打开即全新状态（无需手动重置）。
 * 用 Portal 渲染到 body——顶栏自带毛玻璃（backdrop-filter），
 * 若直接渲染在顶栏内，fixed 遮罩会被压进 56px 高的顶栏里。
 */
export function SearchPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null
  return (
    <Portal>
      <PaletteInner onClose={onClose} />
    </Portal>
  )
}

function PaletteInner({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const debounceRef = useRef<number>(undefined)
  const [keyword, setKeyword] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [active, setActive] = useState(0)

  const search = useMutation({
    mutationFn: searchStocks,
    onSuccess: (data) => {
      setResults(data)
      setActive(0)
    },
  })

  const handleInput = (value: string) => {
    setKeyword(value)
    window.clearTimeout(debounceRef.current)
    if (!value.trim()) {
      setResults([])
      return
    }
    // 200ms 防抖：拼音输入过程不打爆后端
    debounceRef.current = window.setTimeout(() => search.mutate(value.trim()), 200)
  }

  const goStock = useCallback(
    (symbol: string) => {
      onClose()
      navigate(`/stock/${symbol}`)
    },
    [navigate, onClose],
  )

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(a + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter' && results[active]) {
      goStock(results[active].symbol)
    } else if (e.key === 'Escape') {
      onClose()
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[18vh] backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-[560px] max-w-[92vw] overflow-hidden rounded-card border bg-popover shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b px-4 py-3">
          <Search className="size-4 text-text-muted" />
          <input
            autoFocus
            value={keyword}
            onChange={(e) => handleInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="代码 / 拼音首字母 / 名称，如 600519、gzmt、茅台"
            className="w-full bg-transparent text-sm outline-none placeholder:text-text-muted"
          />
          <kbd className="rounded border px-1.5 py-0.5 font-data text-[10px] text-text-muted">
            Esc
          </kbd>
        </div>

        <ul className="max-h-80 overflow-auto p-1.5">
          {results.length === 0 && (
            <li className="px-3 py-8 text-center text-xs text-text-muted">
              {keyword.trim()
                ? search.isPending
                  ? '搜索中…'
                  : '没有匹配的股票'
                : '支持 5800+ 只沪深京 A 股'}
            </li>
          )}
          {results.map((r, i) => (
            <li key={r.symbol}>
              <button
                className={cn(
                  'flex w-full items-center justify-between rounded-inner px-3 py-2.5 text-left text-sm',
                  i === active ? 'bg-accent text-foreground' : 'text-muted-foreground',
                )}
                onMouseEnter={() => setActive(i)}
                onClick={() => goStock(r.symbol)}
              >
                <span>
                  {r.name}
                  <span className="ml-2 font-data text-xs text-text-muted">{r.symbol}</span>
                  <span className="ml-2 font-data text-[10px] text-text-muted/70">{r.pinyin}</span>
                </span>
                <span className="flex items-center gap-2">
                  <span className="text-[10px] text-text-muted">{r.market}</span>
                  {i === active && <CornerDownLeft className="size-3 text-gold" />}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
