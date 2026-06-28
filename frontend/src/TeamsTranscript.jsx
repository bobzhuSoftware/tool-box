import { useState, useRef, useEffect } from 'react'
import useSSEStream from './useSSEStream'
import EdgeProfilePicker from './EdgeProfilePicker'

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
            addLog({ type: 'done', message: `✓ Transcript ready: ${event.name}.txt — click Download below` })
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
      addLog({ type: 'error', message: `Download failed: ${err.message}` })
    }
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
