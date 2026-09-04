import { useEffect, useRef, useState } from 'react'
import type { EventItem } from './api'

/** Reconnect-safe event stream: on reconnect send last seq for incremental catch-up.
 *  Reconnects whenever `token` changes (login / logout). */
export function useEvents(onEvent?: (e: EventItem) => void, token = '') {
  const [events, setEvents] = useState<EventItem[]>([])
  const [connected, setConnected] = useState(false)
  const lastSeq = useRef(0)
  const handler = useRef(onEvent)
  handler.current = onEvent

  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let retry = 0

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/events?since=${lastSeq.current}&token=${encodeURIComponent(token)}`)
      ws.onopen = () => { setConnected(true); retry = 0 }
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data) as { events: EventItem[] }
          if (data.events?.length) {
            lastSeq.current = data.events[data.events.length - 1].seq
            setEvents((prev) => [...prev, ...data.events].slice(-300))
            data.events.forEach((e) => handler.current?.(e))
          }
        } catch { /* ignore malformed */ }
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) {
          const delay = Math.min(15000, 1000 * Math.pow(2, retry++)) // exponential backoff
          setTimeout(connect, delay)
        }
      }
      ws.onerror = () => ws?.close()
    }
    connect()
    return () => { closed = true; ws?.close() }
  }, [token])

  return { events, connected }
}
