import { useState, useRef, useEffect } from 'react'
import FirefoxProfilePicker from './FirefoxProfilePicker'

/**
 * Web → PDF tool — queue-based mode.
 *
 * Submitting a URL calls /api/pdf/enqueue and returns immediately;
 * the job runs in the background. Progress is polled every 2 s via
 * /api/pdf/status/:id. Clicking a completed job in the global queue
 * panel passes `initialJob` here to restore the download view.
 */
function WebToPdf({ token, onAuthError, initialJob, onClearInitialJob }) {
  const [url, setUrl] = useState('')
  const [isX, setIsX] = useState(false)
  const [result, setResult] = useState(null)        // { job_id, title }
  const [progressLog, setProgressLog] = useState([])
  const [loading, setLoading] = useState(false)     // true while polling a running job
  const logContainerRef = useRef(null)
  const pollRef = useRef(null)
  const tokenRef = useRef(token)
  useEffect(() => { tokenRef.current = token }, [token])

  // Auto-scroll progress log
  useEffect(() => {
    const el = logContainerRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [progressLog])

  // Stop polling on unmount
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const authHeaders = () =>
    tokenRef.current ? { Authorization: `Bearer ${tokenRef.current}` } : {}

  // ---- polling helpers ----

  const startPolling = (jobId) => {
    if (pollRef.current) clearInterval(pollRef.current)

    const doPoll = async () => {
      try {
        const res = await fetch(`/api/pdf/status/${jobId}`, { headers: authHeaders() })
        if (!res.ok) {
          if (res.status === 401) { onAuthError?.(); clearInterval(pollRef.current); return }
          if (res.status === 404) { setLoading(false); clearInterval(pollRef.current); return }
          clearInterval(pollRef.current)
          return
        }
        const job = await res.json()
        setProgressLog(job.progress || [])

        if (job.status === 'done') {
          setResult({ job_id: jobId, ...job.result })
          setLoading(false)
          clearInterval(pollRef.current)
        } else if (job.status === 'error') {
          setLoading(false)
          clearInterval(pollRef.current)
        }
      } catch { /* ignore transient network errors */ }
    }

    doPoll()
    pollRef.current = setInterval(doPoll, 2000)
  }

  // ---- restore view when the user clicks a job in the global queue panel ----

  useEffect(() => {
    if (!initialJob) return
    if (pollRef.current) clearInterval(pollRef.current)
    setUrl(initialJob.url || '')
    setResult(null)
    setProgressLog(initialJob.last_message
      ? [{ type: initialJob.status === 'error' ? 'error' : 'status', message: initialJob.last_message }]
      : []
    )

    if (initialJob.status === 'done') {
      setResult({ job_id: initialJob.job_id, ...initialJob.result })
      setLoading(false)
      fetch(`/api/pdf/status/${initialJob.job_id}`, { headers: authHeaders() })
        .then(r => r.ok ? r.json() : null)
        .then(job => { if (job?.progress?.length) setProgressLog(job.progress) })
        .catch(() => {})
    } else if (initialJob.status === 'running') {
      setLoading(true)
      startPolling(initialJob.job_id)
    } else {
      setLoading(false)
      fetch(`/api/pdf/status/${initialJob.job_id}`, { headers: authHeaders() })
        .then(r => r.ok ? r.json() : null)
        .then(job => { if (job?.progress?.length) setProgressLog(job.progress) })
        .catch(() => {})
    }
    onClearInitialJob?.()
  }, [initialJob]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- submit ----

  const handleGenerate = async () => {
    if (!url.trim()) return
    if (pollRef.current) clearInterval(pollRef.current)
    setResult(null)
    setProgressLog([])
    setLoading(true)

    try {
      const res = await fetch('/api/pdf/enqueue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ url: url.trim(), is_x: isX }),
      })
      if (!res.ok) {
        if (res.status === 401) { onAuthError?.(); setLoading(false); return }
        const data = await res.json().catch(() => ({}))
        setProgressLog([{ type: 'error', message: data.detail || `Server error (${res.status})` }])
        setLoading(false)
        return
      }
      const { job_id } = await res.json()
      setProgressLog([{
        type: 'status',
        message: '任务已提交，后台生成中…可切换到其他工具，完成后从右下角任务面板点击下载。',
      }])
      startPolling(job_id)
    } catch (err) {
      setProgressLog([{ type: 'error', message: err.message || 'Something went wrong' }])
      setLoading(false)
    }
  }

  const handleDownload = () => {
    const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
    window.open(`/api/pdf/download/${result.job_id}${authParam}`, '_blank')
  }

  return (
    <>
      <h2 className="tool-page-title">🌐 Web → PDF（智能提取正文）</h2>

      <div className="input-section">
        <p className="tool-description">
          输入任意网页 URL，自动智能提取正文与图片（去除广告、导航等杂乱内容），生成干净易读的 PDF。任务在后台运行，可随时切换到其他工具。
        </p>
        <div className="url-row">
          <input
            type="text"
            placeholder="https://example.com"
            value={url}
            onChange={(e) => { setUrl(e.target.value); setResult(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !loading) handleGenerate() }}
            disabled={loading}
          />
          <button onClick={handleGenerate} disabled={loading || !url.trim()}>
            {loading ? '生成中…' : '提交任务'}
          </button>
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '10px', fontSize: '14px', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={isX}
            onChange={(e) => setIsX(e.target.checked)}
            disabled={loading}
          />
          X / Twitter article (uses Firefox login session)
        </label>
        {isX && <FirefoxProfilePicker token={token} onAuthError={onAuthError} />}
      </div>

      {progressLog.length > 0 && (
        <div className="progress-section">
          <div className="progress-log" ref={logContainerRef}>
            {progressLog.map((entry, i) => (
              <div key={i} className={`log-entry log-${entry.type}`}>
                <span className="log-icon">
                  {entry.type === 'done' ? '✓' : entry.type === 'error' ? '✕' : '●'}
                </span>
                <span className="log-message">{entry.message}</span>
              </div>
            ))}
            {loading && (
              <div className="log-entry log-status">
                <span className="spinner" />
                <span className="log-message">后台运行中，每 2 秒自动刷新…</span>
              </div>
            )}
          </div>
        </div>
      )}

      {result && (
        <div className="pdf-result">
          <div className="pdf-result-info">
            <span className="pdf-result-icon">📄</span>
            <div>
              <div className="pdf-result-title">PDF Ready</div>
              <div className="pdf-result-url">{result.title || url}</div>
            </div>
          </div>
          <button className="btn-primary pdf-download-btn" onClick={handleDownload}>
            ↓ Download PDF
          </button>
        </div>
      )}
    </>
  )
}

export default WebToPdf
