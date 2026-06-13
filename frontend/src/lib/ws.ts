/**
 * WebSocket 客户端：连接后端 /ws，接收事件流（同步进度 / 实时报价）。
 *
 * 设计：
 * - 单例连接 + 按事件类型订阅（多个组件可同时监听不同类型）
 * - 断线自动重连（3 秒退避），组件无需关心连接状态
 * - useWsEvent Hook 封装订阅/退订的生命周期
 */
import { useEffect } from 'react'

type Listener = (event: Record<string, unknown>) => void

const listeners = new Map<string, Set<Listener>>()
let socket: WebSocket | null = null
let reconnectTimer: number | undefined

function connect() {
  // 开发期 Vite 把 /ws 代理到后端；生产期同源直连
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  socket = new WebSocket(`${protocol}://${location.host}/ws`)

  socket.onmessage = (msg) => {
    try {
      const event = JSON.parse(msg.data as string) as Record<string, unknown>
      const type = event.type as string
      listeners.get(type)?.forEach((fn) => fn(event))
    } catch {
      // 非 JSON 消息直接忽略
    }
  }
  socket.onclose = () => {
    socket = null
    // 还有订阅者才重连，避免空转
    if ([...listeners.values()].some((set) => set.size > 0)) {
      reconnectTimer = window.setTimeout(connect, 3000)
    }
  }
}

function ensureConnected() {
  if (socket === null) {
    window.clearTimeout(reconnectTimer)
    connect()
  }
}

/** 订阅某类事件；返回退订函数 */
export function subscribe(type: string, listener: Listener): () => void {
  let set = listeners.get(type)
  if (!set) {
    set = new Set()
    listeners.set(type, set)
  }
  set.add(listener)
  ensureConnected()
  return () => {
    set.delete(listener)
  }
}

/** 向服务端发送控制消息（如订阅报价代码列表） */
export function send(message: Record<string, unknown>) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message))
  }
}

/** React Hook：组件挂载时订阅事件，卸载自动退订 */
export function useWsEvent(type: string, listener: Listener) {
  useEffect(() => subscribe(type, listener), [type, listener])
}
