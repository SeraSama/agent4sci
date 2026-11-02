// One WebSocket carrying every backend event; components subscribe by topic prefix.
import { useEffect, useRef } from 'react'
import { create } from 'zustand'
import { wsUrl } from './api'

export interface BackendEvent {
  id: number
  topic: string
  type: string
  data: Record<string, any>
  ts: number
}

type Listener = (event: BackendEvent) => void

interface ConnectionState {
  status: 'connecting' | 'open' | 'closed'
  setStatus: (status: ConnectionState['status']) => void
}

export const useConnection = create<ConnectionState>((set) => ({
  status: 'connecting',
  setStatus: (status) => set({ status }),
}))

const listeners = new Set<{ prefix: string; fn: Listener }>()
let socket: WebSocket | null = null
let retry = 0

export function connectEvents(): void {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return
  useConnection.getState().setStatus('connecting')
  const ws = new WebSocket(wsUrl())
  socket = ws
  ws.onopen = () => {
    retry = 0
    useConnection.getState().setStatus('open')
  }
  ws.onmessage = (msg) => {
    let event: BackendEvent
    try {
      event = JSON.parse(msg.data)
    } catch {
      return
    }
    for (const l of listeners) {
      if (!l.prefix || event.topic.startsWith(l.prefix)) {
        try {
          l.fn(event)
        } catch (err) {
          console.error(err)
        }
      }
    }
  }
  ws.onclose = () => {
    useConnection.getState().setStatus('closed')
    socket = null
    retry += 1
    setTimeout(connectEvents, Math.min(10_000, 500 * 2 ** Math.min(retry, 5)))
  }
}

export function reconnectEvents(): void {
  socket?.close()
}

export function subscribe(prefix: string, fn: Listener): () => void {
  const entry = { prefix, fn }
  listeners.add(entry)
  return () => listeners.delete(entry)
}

/** Subscribe for the lifetime of a component; the handler may change between renders. */
export function useEvents(prefix: string | null | undefined, handler: Listener): void {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    if (prefix === null || prefix === undefined) return
    return subscribe(prefix, (e) => ref.current(e))
  }, [prefix])
}
