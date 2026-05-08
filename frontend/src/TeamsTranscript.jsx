import { useState, useRef, useEffect } from 'react'

function TeamsTranscript({ token, onAuthError }) {
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [progressLog, setProgressLog] = useState([])
  const [result, setResult] = useState(null) // { job_id, name, lang }
  const logContainerRef = useRef(null)

  useEffect(() => {
    const el = logContainerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [progressLog])

  const addLog = (entry) => setProgressLog((prev) => [...prev, entry])

  const handleGenerate = async () => {
    if (!url.trim()) return
    setLoading(true)
    setProgressLog([])
    setResult(null)

    try {
      const res = await fetch('/api/teams-transcript/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ url: url.trim() }),
      })

      if (!res.ok) {
        if (res.status === 401 && onAuthError) { onAuthError(); return }
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `Server error (${res.status})`)
      }

      const reader = res.body.getReader()
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
                if (event.type === 'done') {
                  setResult({ job_id: event.job_id, name: event.name, lang: event.lang })
                  addLog({ type: 'done', message: `✓ Transcript ready: ${event.name}.vtt` })
                  // Auto-download
                  const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
                  window.open(`/api/teams-transcript/download/${event.job_id}${authParam}`, '_blank')
                } else if (event.type === 'error') {
                  addLog({ type: 'error', message: event.message })
                } else {
                  addLog({ type: 'status', message: event.message })
                }
              } catch { /* ignore */ }
            }
          }
        }
      }
    } catch (err) {
      addLog({ type: 'error', message: err.message || 'Something went wrong' })
    } finally {
      setLoading(false)
    }
  }

  const handleDownload = () => {
    const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
    window.open(`/api/teams-transcript/download/${result.job_id}${authParam}`, '_blank')
  }

  return (
    <>
      <h2 className="tool-page-title">📋 Teams Transcript</h2>

      <div className="input-section">
        <p className="tool-description">
          Paste a Teams recording URL (SharePoint MP4 link or Stream page URL) to download the meeting transcript as a clean VTT file.
        </p>
        <div className="url-row">
          <input
            type="text"
            placeholder="https://dsvcorp-my.sharepoint.com/.../Recording.mp4?web=1&..."
            value={url}
            onChange={(e) => { setUrl(e.target.value); setResult(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !loading) handleGenerate() }}
            disabled={loading}
          />
          <button onClick={handleGenerate} disabled={loading || !url.trim()}>
            {loading ? 'Fetching…' : 'Get Transcript'}
          </button>
        </div>
        <p style={{ fontSize: '13px', color: 'var(--text-muted, #888)', marginTop: '8px' }}>
          Uses your DSV Edge browser session (bob.zhu@dsv.com). Edge must be closed before running.
        </p>
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
        </div>
      )}

      {result && (
        <div className="result-section">
          <div className="result-info">
            <span className="result-label">Transcript downloaded</span>
            <span className="result-meta">
              {result.name}.vtt{result.lang ? ` · ${result.lang}` : ''}
            </span>
          </div>
          <button className="btn-primary" onClick={handleDownload}>
            Download Again
          </button>
        </div>
      )}
    </>
  )
}

export default TeamsTranscript
