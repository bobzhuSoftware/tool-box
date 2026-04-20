import { useState, useRef, useEffect, useCallback } from 'react'
import './App.css'

function formatDate(iso) {
  const d = new Date(iso)
  return d.toLocaleString()
}

function getToken() {
  return localStorage.getItem('token')
}

function authHeaders() {
  const token = getToken()
  return token ? { 'Authorization': `Bearer ${token}` } : {}
}

function App() {
  // --- Auth state ---
  const [token, setToken] = useState(() => localStorage.getItem('token'))
  const [username, setUsername] = useState(() => localStorage.getItem('username') || '')
  const [authMode, setAuthMode] = useState('login') // 'login' | 'register'
  const [authUser, setAuthUser] = useState('')
  const [authPass, setAuthPass] = useState('')
  const [authError, setAuthError] = useState('')
  const [authLoading, setAuthLoading] = useState(false)

  const handleAuth = async (e) => {
    e.preventDefault()
    setAuthError('')
    setAuthLoading(true)
    try {
      const endpoint = authMode === 'register' ? '/api/register' : '/api/login'
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: authUser, password: authPass }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Auth failed')
      localStorage.setItem('token', data.access_token)
      localStorage.setItem('username', data.username)
      setToken(data.access_token)
      setUsername(data.username)
      setAuthUser('')
      setAuthPass('')
    } catch (err) {
      setAuthError(err.message)
    } finally {
      setAuthLoading(false)
    }
  }

  const handleLogout = () => {
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    setToken(null)
    setUsername('')
    setHistory([])
    setResult(null)
  }

  // --- App state ---
  const [platform, setPlatform] = useState('youtube')
  const [url, setUrl] = useState('')
  const [model, setModel] = useState('base')
  const [language, setLanguage] = useState('')

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
  const [loading, setLoading] = useState(false)
  const [progressLog, setProgressLog] = useState([])
  const [result, setResult] = useState(null)
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const logContainerRef = useRef(null)

  const fetchHistory = useCallback(async () => {
    if (!getToken()) { setHistoryLoading(false); return }
    try {
      const res = await fetch('/api/history', { headers: authHeaders() })
      if (res.ok) setHistory(await res.json())
      else if (res.status === 401) handleLogout()
    } catch {}
    finally { setHistoryLoading(false) }
  }, [token])

  useEffect(() => { fetchHistory() }, [fetchHistory])

  useEffect(() => {
    const el = logContainerRef.current
    if (!el) return
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    if (isNearBottom) el.scrollTop = el.scrollHeight
  }, [progressLog])

  const addLog = (entry) => {
    // Download progress lines update in-place instead of stacking
    if (entry.type === 'progress') {
      setProgressLog(prev => {
        const last = prev[prev.length - 1]
        if (last?.type === 'progress') {
          return [...prev.slice(0, -1), entry]
        }
        return [...prev, entry]
      })
    } else {
      setProgressLog(prev => [...prev, entry])
    }
  }

  const handleTranscribe = async () => {
    if (!url.trim()) return
    setLoading(true)
    setProgressLog([])
    setResult(null)

    try {
      const body = { url: url.trim(), model }
      if (language.trim()) body.language = language.trim()

      const response = await fetch('/api/transcribe/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(body),
      })

      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(data.detail || `Server error (${response.status})`)
      }

      const reader = response.body.getReader()
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
                  setResult(event)
                  addLog({ type: 'done', message: `Transcription complete! Detected language: ${event.language}` })
                  fetchHistory() // refresh history list
                } else {
                  addLog(event)
                }
              } catch { /* ignore malformed SSE lines */ }
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

  const handleDownload = (jobId, withTimestamps) => {
    const ts = withTimestamps ? 'true' : 'false'
    const t = getToken()
    const authParam = t ? `&token=${encodeURIComponent(t)}` : ''
    window.open(`/api/download/${jobId}?timestamps=${ts}${authParam}`, '_blank')
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
    <div className="app">
      <h1>Video Transcript Generator</h1>

      {/* Auth: Login / Register */}
      {!token ? (
        <div className="auth-section">
          <div className="auth-toggle">
            <button className={authMode === 'login' ? 'active' : ''} onClick={() => { setAuthMode('login'); setAuthError('') }}>Login</button>
            <button className={authMode === 'register' ? 'active' : ''} onClick={() => { setAuthMode('register'); setAuthError('') }}>Register</button>
          </div>
          <form className="auth-form" onSubmit={handleAuth}>
            <input type="text" placeholder="Username" value={authUser} onChange={e => setAuthUser(e.target.value)} required autoComplete="username" />
            <input type="password" placeholder="Password" value={authPass} onChange={e => setAuthPass(e.target.value)} required autoComplete={authMode === 'register' ? 'new-password' : 'current-password'} />
            {authError && <p className="auth-error">{authError}</p>}
            <button type="submit" disabled={authLoading}>{authLoading ? 'Please wait...' : authMode === 'register' ? 'Create Account' : 'Sign In'}</button>
          </form>
        </div>
      ) : (
      <>
      {/* User bar */}
      <div className="user-bar">
        <span>Logged in as <strong>{username}</strong></span>
        <button className="btn-outline btn-sm" onClick={handleLogout}>Logout</button>
      </div>

      {/* Input Section */}
      <div className="input-section">
        {/* Platform Toggle */}
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
        <div className="options-row">
          <label>
            Model
            <select value={model} onChange={(e) => setModel(e.target.value)} disabled={loading}>
              <option value="tiny">tiny (fastest)</option>
              <option value="base">base (default)</option>
              <option value="small">small</option>
              <option value="medium">medium</option>
              <option value="large">large (best)</option>
            </select>
          </label>
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
          </div>
        </div>
      )}

      {/* History */}
      <div className="history-section">
        <h2>Recent Transcripts</h2>
        {historyLoading ? (
          <p className="history-empty">Loading...</p>
        ) : history.length === 0 ? (
          <p className="history-empty">No transcripts yet. Paste a video URL above to get started.</p>
        ) : (
          <ul className="history-list">
            {history.map((item) => (
              <li key={item.job_id} className="history-item">
                <div className="history-info">
                  <span className="history-title">{item.title}</span>
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
      )}
    </div>
  )
}

export default App
