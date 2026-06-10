import { useState, useRef, useEffect } from 'react'
import useSSEStream from './useSSEStream'

function WebToPdf({ token, onAuthError }) {
  const [url, setUrl] = useState('')
  const [isX, setIsX] = useState(false)
  const [result, setResult] = useState(null) // { job_id }
  const { progressLog, logContainerRef, addLog, loading, streamSSE } = useSSEStream()

  const authHeaders = () => token ? { Authorization: `Bearer ${token}` } : {}

  const handleGenerate = async () => {
    if (!url.trim()) return
    setResult(null)

    await streamSSE(
      () => fetch('/api/pdf/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ url: url.trim(), is_x: isX }),
      }),
      {
        onAuthError,
        onEvent: (event) => {
          if (event.type === 'done') {
            setResult({ job_id: event.job_id })
            addLog({ type: 'done', message: 'PDF generated! Click below to download.' })
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
    window.open(`/api/pdf/download/${result.job_id}${authParam}`, '_blank')
  }

  return (
    <>
      <h2 className="tool-page-title">🌐 Web Page to PDF</h2>

      <div className="input-section">
        <p className="tool-description">
          Enter any webpage URL to render it as a PDF file.
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
            {loading ? 'Generating...' : 'Generate PDF'}
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
                <span className="log-message">Please wait...</span>
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
              <div className="pdf-result-url">{url}</div>
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
