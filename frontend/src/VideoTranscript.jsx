import { useState, useRef, useEffect, useCallback } from 'react'
import useSSEStream from './useSSEStream'

function formatDate(iso) {
  const d = new Date(iso)
  return d.toLocaleString()
}

function VideoTranscript({ token, onAuthError }) {
  const authHeaders = () =>
    token ? { Authorization: `Bearer ${token}` } : {}

  const [inputMode, setInputMode] = useState('url')
  const [platform, setPlatform] = useState('youtube')
  const [url, setUrl] = useState('')
  const [uploadFile, setUploadFile] = useState(null)
  const fileInputRef = useRef(null)
  const [model, setModel] = useState('base')
  const [language, setLanguage] = useState('')
  const [transcribeMode, setTranscribeMode] = useState('auto')

  const PLATFORMS = {
    youtube: {
      label: 'YouTube',
      placeholder: 'e.g. https://www.youtube.com/watch?v=...',
      icon: '▶',
    },
    bilibili: {
      label: 'Bilibili',
      placeholder: 'e.g. https://www.bilibili.com/video/BV...',
      icon: '📺',
    },
  }

  const [result, setResult] = useState(null)
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyFilter, setHistoryFilter] = useState('')
  const [whisperModels, setWhisperModels] = useState([])
  const [showModelManager, setShowModelManager] = useState(false)
  const [modelDownloadProgress, setModelDownloadProgress] = useState({}) // { modelName: { percent, message } }
  const { progressLog, logContainerRef, addLog, loading, streamSSE } = useSSEStream()

  const fetchModels = useCallback(async () => {
    try {
      const res = await fetch('/api/whisper/models')
      if (res.ok) {
        const data = await res.json()
        setWhisperModels(data)
      }
    } catch {}
  }, [])

  useEffect(() => { fetchModels() }, [fetchModels])
  // Also refresh models when opening the model manager panel
  useEffect(() => { if (showModelManager) fetchModels() }, [showModelManager])

  const handleDownloadModel = async (modelName) => {
    try {
      setModelDownloadProgress(prev => ({ ...prev, [modelName]: { percent: 0, message: 'Starting...' } }))
      const res = await fetch(`/api/whisper/models/${modelName}/download`, { method: 'POST' })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Failed' }))
        setModelDownloadProgress(prev => ({ ...prev, [modelName]: { percent: -1, message: err.detail || 'Error' } }))
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'progress') {
              setModelDownloadProgress(prev => ({ ...prev, [modelName]: { percent: data.percent, message: data.message } }))
            } else if (data.type === 'status') {
              setModelDownloadProgress(prev => ({ ...prev, [modelName]: { ...prev[modelName], message: data.message } }))
            } else if (data.type === 'done') {
              setModelDownloadProgress(prev => { const n = { ...prev }; delete n[modelName]; return n })
              fetchModels()
            } else if (data.type === 'error') {
              setModelDownloadProgress(prev => ({ ...prev, [modelName]: { percent: -1, message: data.message } }))
            }
          } catch {}
        }
      }
    } catch (e) {
      setModelDownloadProgress(prev => ({ ...prev, [modelName]: { percent: -1, message: 'Network error' } }))
    }
  }

  const fetchHistory = useCallback(async () => {
    if (!token) { setHistoryLoading(false); return }
    try {
      const res = await fetch('/api/history', { headers: authHeaders() })
      if (res.ok) setHistory(await res.json())
      else if (res.status === 401) onAuthError()
    } catch {}
    finally { setHistoryLoading(false) }
  }, [token])

  useEffect(() => { fetchHistory() }, [fetchHistory])

  const handleTranscribe = async () => {
    if (inputMode === 'url' && !url.trim()) return
    if (inputMode === 'upload' && !uploadFile) return
    setResult(null)

    const fetchFn = () => {
      if (inputMode === 'upload') {
        const formData = new FormData()
        formData.append('file', uploadFile)
        formData.append('model', model)
        formData.append('language', language.trim())
        return fetch('/api/transcribe/upload', {
          method: 'POST',
          headers: { ...authHeaders() },
          body: formData,
        })
      } else {
        const body = { url: url.trim(), model, mode: transcribeMode }
        if (language.trim()) body.language = language.trim()
        return fetch('/api/transcribe/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify(body),
        })
      }
    }

    await streamSSE(fetchFn, {
      onAuthError,
      onEvent: (event) => {
        if (event.type === 'done') {
          setResult(event)
          const doneMsg = event.source === 'captions'
            ? `Extracted from existing captions! Detected language: ${event.language}`
            : `Transcription complete! Detected language: ${event.language}`
          addLog({ type: 'done', message: doneMsg })
          fetchHistory()
        } else {
          addLog(event)
        }
      },
    })
  }

  const handleDownload = async (jobId, withTimestamps, chunkMinutes = 0) => {
    const ts = withTimestamps ? 'true' : 'false'
    const chunkParam = chunkMinutes > 0 ? `&chunk_minutes=${chunkMinutes}` : ''

    if (chunkMinutes > 0) {
      // ZIP binary — must use fetch+Blob; window.open can't reliably trigger
      // a binary download and some browsers display the raw bytes as text.
      try {
        const authParam = token ? `&token=${encodeURIComponent(token)}` : ''
        const res = await fetch(
          `/api/download/${jobId}?timestamps=${ts}${chunkParam}${authParam}`,
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
          : asciiMatch ? asciiMatch[1] : `transcript_split${chunkMinutes}min.zip`
        a.download = rawName
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(blobUrl)
      } catch (err) {
        alert('Download failed: ' + err.message)
      }
    } else {
      // Plain text — window.open is fine
      const authParam = token ? `&token=${encodeURIComponent(token)}` : ''
      window.open(`/api/download/${jobId}?timestamps=${ts}${authParam}`, '_blank')
    }
  }

  const handleDelete = async (jobId) => {
    if (!window.confirm('Delete this transcript record?')) return
    await fetch(`/api/history/${jobId}`, { method: 'DELETE', headers: authHeaders() })
    fetchHistory()
    if (result?.job_id === jobId) setResult(null)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !loading) handleTranscribe()
  }

  return (
    <>
      <h2 className="tool-page-title">🎬 Video Transcript Generator</h2>

      {/* Input Section */}
      <div className="input-section">
        {/* Input Mode Toggle: URL vs Upload */}
        <div className="input-mode-toggle">
          <button
            className={`mode-btn ${inputMode === 'url' ? 'active' : ''}`}
            onClick={() => setInputMode('url')}
            disabled={loading}
          >
            🔗 URL
          </button>
          <button
            className={`mode-btn ${inputMode === 'upload' ? 'active' : ''}`}
            onClick={() => setInputMode('upload')}
            disabled={loading}
          >
            📁 Upload File
          </button>
        </div>

        {inputMode === 'url' ? (
          <>
            <div className="platform-toggle">
              {Object.entries(PLATFORMS).map(([key, p]) => (
                <button
                  key={key}
                  className={`platform-btn ${platform === key ? 'active' : ''}`}
                  onClick={() => { setPlatform(key); setUrl('') }}
                  disabled={loading}
                >
                  {p.icon} {p.label}
                </button>
              ))}
            </div>
            <div className="url-row">
              <input
                type="text"
                placeholder={PLATFORMS[platform].placeholder}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={loading}
              />
              <button onClick={handleTranscribe} disabled={loading || !url.trim()}>
                {loading ? 'Transcribing...' : 'Transcribe'}
              </button>
            </div>
          </>
        ) : (
          <>
            <div
              className={`upload-area ${uploadFile ? 'has-file' : ''}`}
              onClick={() => !loading && fileInputRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); e.stopPropagation() }}
              onDrop={(e) => {
                e.preventDefault(); e.stopPropagation()
                const f = e.dataTransfer.files[0]
                if (f) setUploadFile(f)
              }}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept="video/*,audio/*"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files[0]
                  if (f) setUploadFile(f)
                }}
                disabled={loading}
              />
              {uploadFile ? (
                <div className="upload-file-info">
                  <span className="upload-file-icon">🎬</span>
                  <span className="upload-file-name">{uploadFile.name}</span>
                  <span className="upload-file-size">({(uploadFile.size / 1024 / 1024).toFixed(1)} MB)</span>
                  <button
                    className="upload-remove-btn"
                    onClick={(e) => { e.stopPropagation(); setUploadFile(null); if (fileInputRef.current) fileInputRef.current.value = '' }}
                  >
                    ✕
                  </button>
                </div>
              ) : (
                <div className="upload-placeholder">
                  <span className="upload-icon">📤</span>
                  <p>Click or drag & drop a video/audio file here</p>
                  <p className="upload-hint">Supports MP4, MKV, AVI, MP3, WAV, etc.</p>
                </div>
              )}
            </div>
            <div className="url-row">
              <button
                onClick={handleTranscribe}
                disabled={loading || !uploadFile}
                style={{ width: '100%' }}
              >
                {loading ? 'Transcribing...' : 'Transcribe Uploaded File'}
              </button>
            </div>
          </>
        )}

        {/* Transcription mode selector — only relevant for URL input, not file upload */}
        {inputMode === 'url' && (
          <div className="options-row">
            <label>Mode</label>
            <div className="platform-toggle">
              {[{ id: 'auto', label: '⚡ Auto' }, { id: 'captions', label: '📄 Captions' }, { id: 'whisper', label: '🤖 Whisper' }].map(({ id, label }) => (
                <button
                  key={id}
                  className={`platform-btn ${transcribeMode === id ? 'active' : ''}`}
                  onClick={() => setTranscribeMode(id)}
                  disabled={loading}
                  title={{
                    auto: 'Try captions first, fall back to AI if not available',
                    captions: 'Extract existing subtitles directly (fast)',
                    whisper: 'Always use Whisper AI transcription',
                  }[id]}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="options-row">
          {transcribeMode !== 'captions' && (
            <label>
              Model
              <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                <select value={model} onChange={(e) => setModel(e.target.value)} disabled={loading}>
                  {['tiny', 'base', 'small', 'medium', 'large'].map(m => {
                    const info = whisperModels.find(x => x.name === m)
                    const installed = info?.installed
                    const label = m === 'tiny' ? 'tiny (fastest)' : m === 'base' ? 'base (default)' : m === 'large' ? 'large (best)' : m
                    return <option key={m} value={m}>{label}{installed ? ' ✓' : ' ⬇'}</option>
                  })}
                </select>
                <button
                  type="button"
                  className="btn-sm btn-outline"
                  onClick={() => setShowModelManager(!showModelManager)}
                  title="Manage Whisper models"
                  style={{ whiteSpace: 'nowrap' }}
                >
                  ⚙ Models
                </button>
              </div>
            </label>
          )}
          <label>
            Language (optional)
            <input
              type="text"
              placeholder="e.g. en, zh, de"
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              disabled={loading}
              style={{ width: '120px' }}
            />
          </label>
        </div>
      </div>

      {/* Model Manager */}
      {showModelManager && (
        <div className="model-manager" style={{
          background: 'var(--bg-secondary, #f8f9fa)',
          border: '1px solid var(--border-color, #dee2e6)',
          borderRadius: '8px',
          padding: '16px',
          marginBottom: '16px',
        }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '14px' }}>Whisper Model Manager</h3>
          <p style={{ margin: '0 0 12px', fontSize: '12px', color: '#666' }}>
            Larger models produce better transcription but take longer to process. Models need to be downloaded before first use.
          </p>
          <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid #ddd' }}>
                <th style={{ padding: '6px 8px' }}>Model</th>
                <th style={{ padding: '6px 8px' }}>Size</th>
                <th style={{ padding: '6px 8px' }}>Quality / Speed</th>
                <th style={{ padding: '6px 8px' }}>Status</th>
                <th style={{ padding: '6px 8px' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {[
                { name: 'tiny', quality: '★☆☆☆☆', speed: 'fastest' },
                { name: 'base', quality: '★★☆☆☆', speed: 'fast' },
                { name: 'small', quality: '★★★☆☆', speed: 'moderate' },
                { name: 'medium', quality: '★★★★☆', speed: 'slow' },
                { name: 'large', quality: '★★★★★', speed: 'slowest' },
              ].map(({ name, quality, speed }) => {
                const info = whisperModels.find(x => x.name === name) || {}
                const dl = modelDownloadProgress[name]
                const isDownloading = !!dl && dl.percent >= 0
                return (
                  <tr key={name} style={{ borderBottom: '1px solid #eee' }}>
                    <td style={{ padding: '8px' }}><strong>{name}</strong></td>
                    <td style={{ padding: '8px' }}>{info.expected_mb ? `~${info.expected_mb}MB` : '—'}</td>
                    <td style={{ padding: '8px' }}>{quality} <span style={{ color: '#888', fontSize: '11px' }}>{speed}</span></td>
                    <td style={{ padding: '8px', minWidth: '180px' }}>
                      {info.installed ? (
                        <span style={{ color: '#28a745' }}>✓ Installed</span>
                      ) : isDownloading ? (
                        <div>
                          <div style={{
                            background: '#e9ecef', borderRadius: '4px', height: '18px',
                            overflow: 'hidden', position: 'relative', marginBottom: '4px'
                          }}>
                            <div style={{
                              background: '#007bff', height: '100%', width: `${dl.percent}%`,
                              transition: 'width 0.3s ease', borderRadius: '4px'
                            }} />
                            <span style={{
                              position: 'absolute', top: '50%', left: '50%',
                              transform: 'translate(-50%, -50%)',
                              fontSize: '11px', fontWeight: 'bold', color: dl.percent > 50 ? '#fff' : '#333'
                            }}>{dl.percent}%</span>
                          </div>
                          <span style={{ fontSize: '11px', color: '#666' }}>{dl.message}</span>
                        </div>
                      ) : dl && dl.percent === -1 ? (
                        <span style={{ color: '#dc3545', fontSize: '12px' }}>⚠ {dl.message}</span>
                      ) : (
                        <span style={{ color: '#888' }}>Not installed</span>
                      )}
                    </td>
                    <td style={{ padding: '8px' }}>
                      {info.installed ? (
                        <button className="btn-sm btn-outline" disabled style={{ opacity: 0.5 }}>Downloaded</button>
                      ) : isDownloading ? (
                        <button className="btn-sm btn-outline" disabled>Downloading...</button>
                      ) : (
                        <button className="btn-sm btn-primary" onClick={() => handleDownloadModel(name)}>
                          ⬇ Download
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Progress Log */}
      {progressLog.length > 0 && (
        <div className="progress-section">
          <div className="progress-log" ref={logContainerRef}>
            {progressLog.map((entry, i) => (
              <div key={i} className={`log-entry log-${entry.type}`}>
                <span className="log-icon">
                  {entry.type === 'done' ? '✓' :
                   entry.type === 'error' ? '✕' :
                   entry.type === 'progress' ? '↓' : '●'}
                </span>
                <span className="log-message">{entry.message}</span>
              </div>
            ))}
            {loading && <div className="log-entry log-status"><span className="spinner" />Waiting...</div>}
          </div>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="result-section">
          <div className="result-header">
            <h2>Transcript</h2>
            <span className="lang-badge">Language: {result.language}</span>
          </div>
          <div className="transcript-box">
            {result.segments.map((seg, i) => (
              <div className="segment" key={i}>
                <div className="time">[{seg.start} → {seg.end}]</div>
                <p className="segment-text">{seg.text}</p>
              </div>
            ))}
          </div>
          <div className="download-row">
            <button className="btn-primary" onClick={() => handleDownload(result.job_id, true)}>
              Download with timestamps
            </button>
            <button className="btn-outline" onClick={() => handleDownload(result.job_id, false)}>
              Download plain text
            </button>
            <button className="btn-outline" onClick={() => handleDownload(result.job_id, true, 30)}
              title="Download as a ZIP of multiple files, each covering 30 minutes">
              ✂ Split by 30 min (ZIP)
            </button>
          </div>
        </div>
      )}

      {/* History */}
      <div className="history-section">
        <h2>Recent Transcripts</h2>
        {!historyLoading && history.length > 0 && (
          <div className="history-filter">
            <input
              type="text"
              placeholder="🔍 Filter by title..."
              value={historyFilter}
              onChange={(e) => setHistoryFilter(e.target.value)}
            />
          </div>
        )}
        {historyLoading ? (
          <p className="history-empty">Loading...</p>
        ) : history.length === 0 ? (
          <p className="history-empty">No transcripts yet. Paste a video URL above to get started.</p>
        ) : (
          <ul className="history-list">
            {history
              .filter((item) => !historyFilter || item.title?.toLowerCase().includes(historyFilter.toLowerCase()))
              .map((item) => (
              <li key={item.job_id} className="history-item">
                <div className="history-info">
                  <span className="history-title" title={item.title}>{item.title}</span>
                  <span className="history-meta">
                    {item.language} · {item.model} · {formatDate(item.created_at)}
                  </span>
                </div>
                <div className="history-actions">
                  <button className="btn-sm btn-primary" onClick={() => handleDownload(item.job_id, true)}>
                    ↓ Timestamps
                  </button>
                  <button className="btn-sm btn-outline" onClick={() => handleDownload(item.job_id, false)}>
                    ↓ Plain
                  </button>
                  <button className="btn-sm btn-outline" onClick={() => handleDownload(item.job_id, true, 30)}
                    title="Download as a ZIP of multiple files, each covering 30 minutes">
                    ✂ Split
                  </button>
                  <button className="btn-sm btn-danger" onClick={() => handleDelete(item.job_id)}>
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}

export default VideoTranscript
