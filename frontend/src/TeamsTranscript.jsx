import { useState, useRef, useEffect } from 'react'
import useSSEStream from './useSSEStream'

function TeamsTranscript({ token, onAuthError }) {
  const [url, setUrl] = useState('')
  const [result, setResult] = useState(null) // { job_id, name, lang }
  const { progressLog, logContainerRef, addLog, loading, streamSSE } = useSSEStream()

  const authHeaders = () => token ? { Authorization: `Bearer ${token}` } : {}

  const handleGenerate = async () => {
    if (!url.trim()) return
    setResult(null)

    await streamSSE(
      () => fetch('/api/teams-transcript/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ url: url.trim() }),
      }),
      {
        onAuthError,
        onEvent: (event) => {
          if (event.type === 'done') {
            setResult({ job_id: event.job_id, name: event.name, lang: event.lang })
            addLog({ type: 'done', message: `✓ Transcript ready: ${event.name}.txt` })
            // Auto-download
            const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
            window.open(`/api/teams-transcript/download/${event.job_id}${authParam}`, '_blank')
          } else if (event.type === 'error') {
            addLog({ type: 'error', message: event.message })
          } else {
            addLog({ type: 'status', message: event.message })
          }
        },
      }
    )
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
              {result.name}.txt{result.lang ? ` · ${result.lang}` : ''}
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
