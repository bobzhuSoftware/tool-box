import { useState, useEffect, useRef } from 'react'

function AudioRecorder({ token, onAuthError }) {
  const [format, setFormat] = useState('wav')   // 'wav' | 'mp3'
  const [mic, setMic] = useState(true)          // also record the microphone (for meetings)

  const [recording, setRecording] = useState(false)
  const [jobId, setJobId] = useState(null)
  const [elapsed, setElapsed] = useState(0)
  const [busy, setBusy] = useState(false)       // start/stop request in flight
  const [result, setResult] = useState(null)    // { job_id, seconds, format, bytes }
  const [error, setError] = useState('')

  const timerRef = useRef(null)
  const startedAtRef = useRef(null)   // epoch ms when capture actually began

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const handle401 = (res) => {
    if (res.status === 401 && onAuthError) { onAuthError(); return true }
    return false
  }

  // Recover an in-progress recording after a refresh or tool switch. The
  // recording runs in a backend subprocess, so the server is the source of
  // truth for whether one is still active.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await fetch('/api/audio/active', { headers: authHeaders() })
        if (res.ok) {
          const list = await res.json()
          if (!cancelled && Array.isArray(list) && list.length > 0) {
            const job = list[0]
            startedAtRef.current = Date.now() - (job.elapsed || 0) * 1000
            setJobId(job.job_id)
            setFormat(job.format || 'wav')
            setMic(!!job.mic)
            setElapsed(job.elapsed || 0)
            setRecording(true)
            return
          }
        }
        // No active recording — surface the most recent saved one so it can
        // always be downloaded, even if the user stopped it from the global
        // banner and navigated away without downloading.
        const lastRes = await fetch('/api/audio/last', { headers: authHeaders() })
        if (lastRes.ok && !cancelled) {
          const last = await lastRes.json()
          if (last && last.job_id) setResult(last)
        }
      } catch { /* ignore — nothing to recover */ }
    })()
    return () => { cancelled = true }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Tick the elapsed timer while recording, anchored to startedAtRef so it
  // stays correct across a state restore.
  useEffect(() => {
    if (recording) {
      if (startedAtRef.current == null) startedAtRef.current = Date.now()
      timerRef.current = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000))
      }, 250)
    } else {
      startedAtRef.current = null
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    }
    return () => { if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null } }
  }, [recording])

  // Warn before closing/refreshing the tab while a recording is in progress.
  useEffect(() => {
    if (!recording) return
    const handler = (e) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [recording])

  const fmtTime = (s) => {
    const m = Math.floor(s / 60).toString().padStart(2, '0')
    const sec = (s % 60).toString().padStart(2, '0')
    return `${m}:${sec}`
  }

  const handleStart = async () => {
    setError('')
    setResult(null)
    setBusy(true)
    try {
      const res = await fetch('/api/audio/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ format, mic }),
      })
      if (handle401(res)) return
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || '录制启动失败')
      setJobId(data.job_id)
      startedAtRef.current = Date.now()
      setElapsed(0)
      setRecording(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const handleStop = async () => {
    if (!jobId) return
    setBusy(true)
    try {
      const res = await fetch(`/api/audio/stop/${jobId}`, {
        method: 'POST',
        headers: { ...authHeaders() },
      })
      if (handle401(res)) return
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || '停止录制失败')
      setRecording(false)
      setResult(data)
    } catch (e) {
      setError(e.message)
      setRecording(false)
    } finally {
      setBusy(false)
    }
  }

  // While recording, poll the backend so an auto-stop (playback device
  // unplugged/changed, Bluetooth dropped, or the 2-hour cap) surfaces in the
  // UI without the user having to click stop. The recording runs in a backend
  // subprocess that finalizes itself on such events, so once it is no longer
  // reported as running we finalize the UI too (the /stop call is idempotent
  // server-side and returns this job's saved result).
  useEffect(() => {
    if (!recording || !jobId || busy) return
    let cancelled = false
    const id = setInterval(async () => {
      try {
        const res = await fetch('/api/audio/active', { headers: authHeaders() })
        if (!res.ok || cancelled) return
        const list = await res.json()
        const stillRunning = Array.isArray(list)
          && list.some((j) => j.job_id === jobId && j.running)
        if (!stillRunning && !cancelled) {
          clearInterval(id)
          handleStop()
        }
      } catch { /* ignore transient network errors */ }
    }, 2500)
    return () => { cancelled = true; clearInterval(id) }
  }, [recording, jobId, busy]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleDownload = () => {
    if (!result?.job_id) return
    const url = `/api/audio/download/${result.job_id}?token=${encodeURIComponent(token || '')}`
    window.open(url, '_blank')
  }

  return (
    <>
      <h2 className="tool-page-title">🎙️ 全声道录音</h2>

      <div className="input-section">
        <p className="tool-description">
          录制电脑扬声器输出的<strong>全部声音</strong>（含 Teams/会议、视频、音乐等），
          可同时混入麦克风，结束后导出为 WAV 或 MP3。仅在本机运行有效。
          录音在<strong>后台进行</strong>，刷新页面或切换到其他工具都不会中断；
          顶部会一直显示录音状态，最长 2 小时后自动停止并保存。
        </p>

        <label style={{
          display: 'flex', gap: '0.5rem', alignItems: 'center', cursor: 'pointer',
          marginBottom: '1rem',
        }}>
          <input type="checkbox" checked={mic}
            onChange={(e) => setMic(e.target.checked)} disabled={recording || busy} />
          <span>
            同时录制我的麦克风（适合会议——把对方声音和自己的发言混到一个文件）
          </span>
        </label>

        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginBottom: '1rem' }}>
          <span style={{ fontSize: '0.9rem', color: '#888' }}>导出格式</span>
          <label style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', cursor: 'pointer' }}>
            <input type="radio" name="audio-format" checked={format === 'wav'}
              onChange={() => setFormat('wav')} disabled={recording || busy} /> WAV（无损）
          </label>
          <label style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', cursor: 'pointer' }}>
            <input type="radio" name="audio-format" checked={format === 'mp3'}
              onChange={() => setFormat('mp3')} disabled={recording || busy} /> MP3（体积小）
          </label>
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
          {!recording ? (
            <button onClick={handleStart} disabled={busy}>
              {busy ? '启动中...' : '● 开始录制'}
            </button>
          ) : (
            <button onClick={handleStop} disabled={busy}
              style={{ background: '#e53935', borderColor: '#e53935' }}>
              {busy ? '停止中...' : '■ 停止录制'}
            </button>
          )}

          {recording && (
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 500 }}>
              <span style={{
                width: '10px', height: '10px', borderRadius: '50%', background: '#e53935',
                display: 'inline-block', animation: 'pulse 1s infinite',
              }} />
              录制中 {fmtTime(elapsed)}
            </span>
          )}
        </div>

        {error && <p style={{ color: '#e53935', marginTop: '1rem' }}>{error}</p>}

        {result && !recording && (
          <>
          <div style={{
            marginTop: '1.25rem', padding: '1rem 1.25rem', borderRadius: '10px',
            background: 'var(--accent-bg, #f0f4ff)', display: 'flex', alignItems: 'center',
            gap: '1rem', flexWrap: 'wrap',
          }}>
            <span>
              {result.recovered ? '📁 最近一次录音' : '✓ 录制完成'}，时长 <strong>{fmtTime(Math.round(result.seconds || 0))}</strong>
              （{(result.format || 'wav').toUpperCase()}
              {result.bytes ? `，${(result.bytes / 1024 / 1024).toFixed(2)} MB` : ''}
              {result.mic ? '，含麦克风' : ''}）
            </span>
            <button onClick={handleDownload}>⬇ 下载音频</button>
          </div>
          {result.interrupted && (
            <div style={{
              marginTop: '0.75rem', padding: '0.8rem 1rem', borderRadius: '10px',
              background: '#fffbe6', border: '1px solid #ffe58f', color: '#874d00',
              fontSize: '0.88rem', lineHeight: 1.5,
            }}>
              ⚠ 录音过程中检测到<strong>播放设备变化或断开</strong>（例如切换/拔出耳机、蓝牙掉线）。
              录音已在中断点自动结束并保存，你可以下载这段录音，然后重新开始录制。
            </div>
          )}
          {result.silent && (
            <div style={{
              marginTop: '0.75rem', padding: '0.8rem 1rem', borderRadius: '10px',
              background: '#fff1f0', border: '1px solid #ffccc7', color: '#a8071a',
              fontSize: '0.88rem', lineHeight: 1.5,
            }}>
              ⚠ 这次录到的音频<strong>电平接近静音</strong>（峰值 {result.peak ?? 0}）。
              请确认扬声器/默认播放设备确实在出声；如果你用的是耳机/蓝牙耳麦，
              会议声音可能走了非默认设备，请在 Windows 声音设置里把默认输出设为你正在听的设备。
            </div>
          )}
          </>
        )}
      </div>

      <style>{`@keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.3 } }`}</style>
    </>
  )
}

export default AudioRecorder
