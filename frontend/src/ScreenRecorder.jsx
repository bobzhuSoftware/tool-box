import { useState, useEffect, useRef } from 'react'

function ScreenRecorder({ token, onAuthError }) {
  const [windows, setWindows] = useState([])      // [{ hwnd, pid, title, name }]
  const [hwnd, setHwnd] = useState(null)          // selected window handle
  const [loadingWindows, setLoadingWindows] = useState(false)
  const [mic, setMic] = useState(true)            // also record the microphone (for meetings)
  const [fps, setFps] = useState(25)

  const [recording, setRecording] = useState(false)
  const [jobId, setJobId] = useState(null)
  const [elapsed, setElapsed] = useState(0)
  const [busy, setBusy] = useState(false)         // start/stop request in flight
  const [result, setResult] = useState(null)      // { job_id, seconds, bytes, width, height, fps, mic }
  const [error, setError] = useState('')

  const timerRef = useRef(null)

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const handle401 = (res) => {
    if (res.status === 401 && onAuthError) { onAuthError(); return true }
    return false
  }

  const loadWindows = async () => {
    setError('')
    setLoadingWindows(true)
    try {
      const res = await fetch('/api/screen/windows', { headers: { ...authHeaders() } })
      if (handle401(res)) return
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || '枚举窗口失败')
      const list = data.windows || []
      setWindows(list)
      // Keep the current selection if still present, else pick the first window.
      setHwnd((prev) => {
        if (prev && list.some((w) => w.hwnd === prev)) return prev
        return list.length ? list[0].hwnd : null
      })
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingWindows(false)
    }
  }

  // Load the window list once on mount.
  useEffect(() => { loadWindows() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // Tick the elapsed timer while recording.
  useEffect(() => {
    if (recording) {
      const startedAt = Date.now()
      timerRef.current = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startedAt) / 1000))
      }, 250)
    } else if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    return () => { if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null } }
  }, [recording])

  const fmtTime = (s) => {
    const m = Math.floor(s / 60).toString().padStart(2, '0')
    const sec = (s % 60).toString().padStart(2, '0')
    return `${m}:${sec}`
  }

  const handleStart = async () => {
    if (!hwnd) { setError('请先选择要录制的窗口'); return }
    setError('')
    setResult(null)
    setBusy(true)
    try {
      const res = await fetch('/api/screen/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ hwnd, mic, fps }),
      })
      if (handle401(res)) return
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || '录屏启动失败')
      setJobId(data.job_id)
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
      const res = await fetch(`/api/screen/stop/${jobId}`, {
        method: 'POST',
        headers: { ...authHeaders() },
      })
      if (handle401(res)) return
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || '停止录屏失败')
      setRecording(false)
      setResult(data)
    } catch (e) {
      setError(e.message)
      setRecording(false)
    } finally {
      setBusy(false)
    }
  }

  const handleDownload = () => {
    if (!result?.job_id) return
    const url = `/api/screen/download/${result.job_id}?token=${encodeURIComponent(token || '')}`
    window.open(url, '_blank')
  }

  return (
    <>
      <h2 className="tool-page-title">🎬 窗口录屏</h2>

      <div className="input-section">
        <p className="tool-description">
          录制<strong>单个窗口</strong>的画面，并同时录入电脑扬声器输出的<strong>全部声音</strong>
          （含 Teams/会议、视频等），可混入麦克风，结束后导出为带声音的 MP4。仅在本机运行有效。
        </p>

        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '1rem' }}>
          <span style={{ fontSize: '0.9rem', color: '#888', whiteSpace: 'nowrap' }}>录制窗口</span>
          <select
            value={hwnd ?? ''}
            onChange={(e) => setHwnd(Number(e.target.value))}
            disabled={recording || busy || loadingWindows}
            style={{ flex: 1, minWidth: 0, padding: '0.4rem 0.5rem', borderRadius: '8px' }}
          >
            {windows.length === 0 && <option value="">（未找到窗口）</option>}
            {windows.map((w) => (
              <option key={w.hwnd} value={w.hwnd}>
                {w.title}{w.name ? ` — ${w.name}` : ''}
              </option>
            ))}
          </select>
          <button onClick={loadWindows} disabled={recording || busy || loadingWindows}
            title="刷新窗口列表">
            {loadingWindows ? '刷新中…' : '⟳ 刷新'}
          </button>
        </div>

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
          <span style={{ fontSize: '0.9rem', color: '#888' }}>帧率</span>
          {[15, 25, 30].map((v) => (
            <label key={v} style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', cursor: 'pointer' }}>
              <input type="radio" name="screen-fps" checked={fps === v}
                onChange={() => setFps(v)} disabled={recording || busy} /> {v} fps
            </label>
          ))}
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
          {!recording ? (
            <button onClick={handleStart} disabled={busy || !hwnd}>
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

        {recording && (
          <p style={{ marginTop: '0.75rem', fontSize: '0.85rem', color: '#888' }}>
            提示：录制期间请保持目标窗口可见（不要最小化），画面静止时不会产生新帧。
          </p>
        )}

        {error && <p style={{ color: '#e53935', marginTop: '1rem' }}>{error}</p>}

        {result && !recording && (
          <div style={{
            marginTop: '1.25rem', padding: '1rem 1.25rem', borderRadius: '10px',
            background: 'var(--accent-bg, #f0f4ff)', display: 'flex', alignItems: 'center',
            gap: '1rem', flexWrap: 'wrap',
          }}>
            <span>
              ✓ 录制完成，时长 <strong>{fmtTime(Math.round(result.seconds || 0))}</strong>
              （MP4 {result.width}×{result.height}
              {result.bytes ? `，${(result.bytes / 1024 / 1024).toFixed(2)} MB` : ''}
              {result.mic ? '，含麦克风' : ''}）
            </span>
            <button onClick={handleDownload}>⬇ 下载视频</button>
          </div>
        )}
      </div>

      <style>{`@keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.3 } }`}</style>
    </>
  )
}

export default ScreenRecorder
