import { useEffect, useRef, useState } from 'react'

function fmtTime(s) {
  const m = Math.floor(s / 60).toString().padStart(2, '0')
  const sec = (s % 60).toString().padStart(2, '0')
  return `${m}:${sec}`
}

/**
 * Always-visible banner that surfaces an in-progress background recording on
 * every page, so a recording can never be silently forgotten. It polls the
 * server (the source of truth) and offers a Stop button reachable from any tool.
 */
export default function RecordingIndicator({ token, onOpen }) {
  const [active, setActive] = useState(null)   // { job_id, elapsed, format, mic, running }
  const [elapsed, setElapsed] = useState(0)
  const [stopping, setStopping] = useState(false)
  const [done, setDone] = useState(null)       // { job_id, seconds }
  const startedAtRef = useRef(null)

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  // Poll the active recording every few seconds.
  useEffect(() => {
    if (!token) return
    let cancelled = false
    const poll = async () => {
      try {
        const res = await fetch('/api/audio/active', { headers: authHeaders() })
        if (!res.ok || cancelled) return
        const list = await res.json()
        if (cancelled) return
        if (Array.isArray(list) && list.length > 0) {
          const job = list[0]
          setActive(job)
          const base = Date.now() - (job.elapsed || 0) * 1000
          if (startedAtRef.current == null || Math.abs(base - startedAtRef.current) > 2000) {
            startedAtRef.current = base
          }
        } else {
          setActive(null)
          startedAtRef.current = null
        }
      } catch { /* ignore transient errors */ }
    }
    poll()
    const id = setInterval(poll, 3000)
    return () => { cancelled = true; clearInterval(id) }
  }, [token]) // eslint-disable-line react-hooks/exhaustive-deps

  // Local 1s tick so the timer updates between polls.
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => {
      if (startedAtRef.current != null) {
        setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000))
      }
    }, 500)
    return () => clearInterval(id)
  }, [active])

  const handleStop = async () => {
    if (!active) return
    setStopping(true)
    try {
      const res = await fetch(`/api/audio/stop/${active.job_id}`, {
        method: 'POST', headers: authHeaders(),
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        setDone(data)
        setActive(null)
        startedAtRef.current = null
      }
    } catch { /* keep banner; user can retry */ }
    finally { setStopping(false) }
  }

  const wrap = {
    display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap',
    padding: '0.5rem 1rem', borderRadius: '10px', margin: '0 0 1rem',
    fontSize: '0.9rem', fontWeight: 500,
  }

  if (done) {
    const url = `/api/audio/download/${done.job_id}?token=${encodeURIComponent(token || '')}`
    return (
      <div style={{ ...wrap, background: '#f0f9eb', border: '1px solid #c2e7b0', color: '#3a7d1e' }}>
        <span>✓ 录音已保存（{fmtTime(Math.round(done.seconds || 0))}）</span>
        <a href={url} target="_blank" rel="noreferrer" style={{ color: '#3a7d1e', fontWeight: 600 }}>⬇ 下载音频</a>
        <button className="btn-outline btn-sm" onClick={() => setDone(null)}>知道了</button>
      </div>
    )
  }

  if (!active) return null

  return (
    <div style={{ ...wrap, background: '#fff1f0', border: '1px solid #ffccc7', color: '#a8071a' }}>
      <span style={{
        width: '10px', height: '10px', borderRadius: '50%', background: '#e53935',
        display: 'inline-block', animation: 'rec-pulse 1s infinite',
      }} />
      <span>
        正在后台录音 {fmtTime(elapsed)}
        {active.running === false ? '（已达上限自动停止，请保存）' : ''}
      </span>
      {onOpen && (
        <button className="btn-outline btn-sm" onClick={() => onOpen('audio')}>前往</button>
      )}
      <button className="btn-sm" style={{ background: '#e53935', borderColor: '#e53935', color: '#fff' }}
        onClick={handleStop} disabled={stopping}>
        {stopping ? '停止中…' : '■ 停止并保存'}
      </button>
      <style>{`@keyframes rec-pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.3 } }`}</style>
    </div>
  )
}
