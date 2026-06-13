import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'

/**
 * 浮层传送门：把弹窗/遮罩渲染到 document.body 下。
 * 为什么必须有：父级若带 backdrop-filter / transform / filter（如顶栏毛玻璃），
 * 会成为 position:fixed 的包含块，导致"全屏遮罩"被压进父容器里、高度错乱。
 * 所有 fixed 定位的浮层一律套上 <Portal>，从根上规避。
 */
export function Portal({ children }: { children: ReactNode }) {
  return createPortal(children, document.body)
}
