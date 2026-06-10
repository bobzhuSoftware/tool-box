import { useState, useRef, useEffect, useCallback } from 'react'

/**
 * Custom hook for Server-Sent Events (SSE) streaming.
 *
 * Returns { progressLog, logContainerRef, addLog, streamSSE, loading }
 *
 * @param {Object} options
 * @param {Function} options.onEvent - Called for each parsed SSE event. Receives (event).
 *   Return value is ignored. The hook auto-logs events via addLog unless onEvent
 *   calls event.preventDefault (set event._suppress = true to skip auto-logging).
 */
export default function useSSEStream() {
  const [progressLog, setProgressLog] = useState([])
  const [loading, setLoading] = useState(false)
  const logContainerRef = useRef(null)

  useEffect(() => {
    const el = logContainerRef.current
    if (!el) return
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    if (isNearBottom) el.scrollTop = el.scrollHeight
  }, [progressLog])

  const addLog = useCallback((entry) => {
    if (entry.type === 'progress') {
      setProgressLog(prev => {
        const last = prev[prev.length - 1]
        if (last?.type === 'progress') return [...prev.slice(0, -1), entry]
        return [...prev, entry]
      })
    } else {
      setProgressLog(prev => [...prev, entry])
    }
  }, [])

  const clearLog = useCallback(() => setProgressLog([]), [])

  /**
   * Read an SSE response stream, parse events, and call onEvent for each.
   *
   * @param {Response} response - A fetch Response with a readable body stream.
   * @param {Function} onEvent - Callback receiving each parsed event object.
   */
  const readSSEStream = useCallback(async (response, onEvent) => {
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) {
        for (const line of part.split('\n')) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6))
              onEvent(event)
            } catch { /* ignore malformed SSE lines */ }
          }
        }
      }
    }
  }, [])

  /**
   * Full SSE streaming flow: set loading, clear log, execute fetch, stream events.
   *
   * @param {Function} fetchFn - Async function that returns a Response.
   *   Receives no arguments — capture request params in a closure.
   * @param {Object} handlers
   * @param {Function} handlers.onEvent - Called for each SSE event.
   * @param {Function} [handlers.onAuthError] - Called on 401 response.
   */
  const streamSSE = useCallback(async (fetchFn, { onEvent, onAuthError } = {}) => {
    setLoading(true)
    setProgressLog([])

    try {
      const res = await fetchFn()

      if (!res.ok) {
        if (res.status === 401 && onAuthError) { onAuthError(); return }
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `Server error (${res.status})`)
      }

      await readSSEStream(res, (event) => {
        if (onEvent) onEvent(event)
      })
    } catch (err) {
      addLog({ type: 'error', message: err.message || 'Something went wrong' })
    } finally {
      setLoading(false)
    }
  }, [addLog, readSSEStream])

  return { progressLog, logContainerRef, addLog, clearLog, loading, streamSSE, readSSEStream, setLoading }
}
