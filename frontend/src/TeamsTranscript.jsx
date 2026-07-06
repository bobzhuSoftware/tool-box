import { useState, useRef, useEffect } from 'react'
import EdgeProfilePicker from './EdgeProfilePicker'

/**
 * Teams Transcript tool — queue-based mode.
 *
 * Submitting a URL calls /api/teams-transcript/enqueue and returns immediately;
 * the job runs in the background. Progress is polled every 2 s via
 * /api/teams-transcript/status/:id so the user can freely navigate away and
 * come back. Clicking a completed job in the global TeamsQueue panel passes
 * `initialJob` here to restore the result view.
 */
function TeamsTranscript({ token, onAuthError, initialJob, onClearInitialJob }) {
  const [url, setUrl] = useState('')
  const [result, setResult] = useState(null)      // { job_id, name, lang }
  const [progressLog, setProgressLog] = useState([])
  const [loading, setLoading] = useState(false)   // true while polling a running job
  const logContainerRef = useRef(null)
  const pollRef = useRef(null)
  const tokenRef = useRef(token)
  useEffect(() => { tokenRef.current = token }, [token])

  // Auto-scroll the progress log
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
        const res = await fetch(`/api/teams-transcript/status/${jobId}`, {
          headers: authHeaders(),
        })
        if (!res.ok) {
          if (res.status === 401) { onAuthError?.(); clearInterval(pollRef.current); return }
          if (res.status === 404) {
            // Job was cancelled/removed — stop polling silently
            setLoading(false)
            clearInterval(pollRef.current)
            return
          }
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
    setResult(null)
    // Show the last known message immediately while the full log loads
    setProgressLog(initialJob.last_message
      ? [{ type: initialJob.status === 'error' ? 'error' : 'status', message: initialJob.last_message }]
      : []
    )

    if (initialJob.status === 'done') {
      setResult({ job_id: initialJob.job_id, ...initialJob.result })
      setLoading(false)
      // Fetch full progress log (one-shot — job is already finished)
      fetch(`/api/teams-transcript/status/${initialJob.job_id}`, { headers: authHeaders() })
        .then(r => r.ok ? r.json() : null)
        .then(job => { if (job?.progress?.length) setProgressLog(job.progress) })
        .catch(() => {})
    } else if (initialJob.status === 'running') {
      setLoading(true)
      // startPolling fires doPoll() immediately, which loads the full log
      startPolling(initialJob.job_id)
    } else {
      setLoading(false)
      // error / cancelled — fetch full log once
      fetch(`/api/teams-transcript/status/${initialJob.job_id}`, { headers: authHeaders() })
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
      const res = await fetch('/api/teams-transcript/enqueue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ url: url.trim() }),
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
        message: '任务已提交，正在后台运行…可切换到其他工具，完成后从右下角任务面板跳回下载。',
      }])
      startPolling(job_id)
    } catch (err) {
      setProgressLog([{ type: 'error', message: err.message || 'Something went wrong' }])
      setLoading(false)
    }
  }

  // ---- download ----

  const handleDownload = () => {
    const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
    window.open(`/api/teams-transcript/download/${result.job_id}${authParam}`, '_blank')
  }

  const handleDownloadSplit = async (chunkMinutes = 30) => {
    if (!result?.job_id) return
    try {
      const authParam = token ? `&token=${encodeURIComponent(token)}` : ''
      const res = await fetch(
        `/api/teams-transcript/download/${result.job_id}?chunk_minutes=${chunkMinutes}${authParam}`,
        { headers: authHeaders() }
      )
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const blob = await res.blob()
      const blobUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = blobUrl
      const disposition = res.headers.get('content-disposition') || ''
      const rfc5987Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
      const asciiMatch = disposition.match(/filename="([^"]+)"/)
      const rawName = rfc5987Match
        ? decodeURIComponent(rfc5987Match[1].trim())
        : asciiMatch ? asciiMatch[1] : `${result.name || 'transcript'}_split${chunkMinutes}min.zip`
      a.download = rawName
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(blobUrl)
    } catch (err) {
      setProgressLog(prev => [...prev, { type: 'error', message: `Download failed: ${err.message}` }])
    }
  }

  return (
    <>
      <h2 className="tool-page-title">📋 Teams Transcript</h2>

      <div className="input-section">
        <p className="tool-description">
          Paste a Teams recording URL (SharePoint MP4 link or Stream page URL) to download the meeting transcript as a clean VTT file.
          任务在后台运行，可随时切换到其他工具，完成后从右下角任务面板跳回下载。
        </p>
        <div className="url-row">
          <input
            type="text"
            placeholder="https://dsvcorp-my.sharepoint.com/.../Recording.mp4?web=1&..."
            value={url}
            onChange={(e) => { setUrl(e.target.value); setResult(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter') handleGenerate() }}
          />
          <button onClick={handleGenerate} disabled={!url.trim()}>
            提交任务
          </button>
        </div>
        <p style={{ fontSize: '13px', color: 'var(--text-muted, #888)', marginTop: '8px' }}>
          Uses your signed-in Edge browser session. Pick the account below; Edge must be closed before running.
        </p>
        <EdgeProfilePicker token={token} onAuthError={onAuthError} />
      </div>

      {progressLog.length > 0 && (
        <div className="progress-section">
          <div className="progress-log" ref={logContainerRef}>
            {progressLog.map((entry, i) => (
              <div key={i} className={`log-entry log-${entry.type}`}>
                {entry.message}
              </div>
            ))}
          </div>
          {loading && (
            <p style={{ fontSize: '12px', color: 'var(--text-muted, #888)', marginTop: '6px' }}>
              任务后台运行中，可切换至其他工具，此处每 2 秒自动刷新。
            </p>
          )}
        </div>
      )}

      {result && (
        <div className="result-section">
          <div className="result-info">
            <span className="result-label">Transcript ready</span>
            <span className="result-meta">
              {result.name}.txt{result.lang ? ` · ${result.lang}` : ''}
            </span>
          </div>
          <div className="download-row">
            <button className="btn-primary" onClick={handleDownload}>
              Download
            </button>
            <button
              className="btn-outline"
              onClick={() => handleDownloadSplit(30)}
              title="Download as a ZIP of multiple WebVTT files, each covering 30 minutes"
            >
              ✂ Split by 30 min (ZIP)
            </button>
          </div>
        </div>
      )}
    </>
  )
}

export default TeamsTranscript
