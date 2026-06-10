import { useState, useRef, useEffect } from 'react'
import useSSEStream from './useSSEStream'

function WebToPdf2({ token, onAuthError }) {
  const [url, setUrl] = useState('')
  const [result, setResult] = useState(null)
  const { progressLog, logContainerRef, addLog, loading, streamSSE } = useSSEStream()

  const authHeaders = () => token ? { Authorization: `Bearer ${token}` } : {}

  const handleGenerate = async () => {
    if (!url.trim()) return
    setResult(null)

    await streamSSE(
      () => fetch('/api/pdf2/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ url: url.trim() }),
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
      <h2 className="tool-page-title">📰 Article to PDF</h2>

      <div className="input-section">
        <p className="tool-description">
          Extracts only the article text and images from any webpage — removes ads, nav bars, and clutter — and saves as a clean, readable PDF.
        </p>
        <div className="url-row">
          <input
            type="text"
            placeholder="https://example.com/article"
            value={url}
            onChange={(e) => { setUrl(e.target.value); setResult(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !loading) handleGenerate() }}
            disabled={loading}
          />
          <button onClick={handleGenerate} disabled={loading || !url.trim()}>
            {loading ? 'Extracting...' : 'Extract & PDF'}
          </button>
        </div>
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

export default WebToPdf2
