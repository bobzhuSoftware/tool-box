import { useCallback, useEffect, useState } from 'react'

const LS_KEY = 'splitChunkMinutes'
export const MIN_SPLIT_MINUTES = 30
export const DEFAULT_SPLIT_MINUTES = 30
export const MAX_SPLIT_MINUTES = 600

export function clampMinutes(value) {
  const n = Math.round(Number(value))
  if (!Number.isFinite(n)) return DEFAULT_SPLIT_MINUTES
  return Math.min(Math.max(n, MIN_SPLIT_MINUTES), MAX_SPLIT_MINUTES)
}

/**
 * Per-user default split duration (minutes).
 * localStorage gives an instant initial value; the server copy (user_settings)
 * is authoritative and keeps the value across browsers/devices.
 */
export function useSplitMinutes(token) {
  const [minutes, setMinutes] = useState(() => clampMinutes(localStorage.getItem(LS_KEY)))

  useEffect(() => {
    if (!token) return
    let cancelled = false
    fetch('/api/preferences', { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data) return
        const v = clampMinutes(data.split_chunk_minutes)
        setMinutes(v)
        localStorage.setItem(LS_KEY, String(v))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [token])

  const saveMinutes = useCallback((value) => {
    const v = clampMinutes(value)
    setMinutes(v)
    localStorage.setItem(LS_KEY, String(v))
    if (token) {
      fetch('/api/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ split_chunk_minutes: v }),
      }).catch(() => {})
    }
    return v
  }, [token])

  return [minutes, saveMinutes]
}
