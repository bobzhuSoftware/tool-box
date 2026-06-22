import { useState, useEffect } from 'react'
import './App.css'
import HomePage from './HomePage'
import VideoTranscript from './VideoTranscript'
import WebToPdf from './WebToPdf'
import WebToPdf2 from './WebToPdf2'
import DsvPagePdf from './DsvPagePdf'
import TeamsTranscript from './TeamsTranscript'
import TeamsChat from './TeamsChat'
import BookConverter from './BookConverter'
import WechatExport from './WechatExport'
import DiscordExport from './DiscordExport'
import ThreadsDownload from './ThreadsDownload'

function App() {
  // --- Auth state ---
  const [token, setToken] = useState(() => localStorage.getItem('token'))
  const [username, setUsername] = useState(() => localStorage.getItem('username') || '')
  const [authChecked, setAuthChecked] = useState(false)
  const [authMode, setAuthMode] = useState('login') // 'login' | 'register'
  const [authUser, setAuthUser] = useState('')
  const [authPass, setAuthPass] = useState('')
  const [authError, setAuthError] = useState('')
  const [authLoading, setAuthLoading] = useState(false)

  // --- Navigation state ---
  const [currentTool, setCurrentTool] = useState(null) // null = home page

  // Validate stored token on startup
  useEffect(() => {
    if (!token) {
      setAuthChecked(true)
      return
    }
    fetch('/api/me', { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => {
        if (!res.ok) throw new Error('invalid')
      })
      .catch(() => {
        localStorage.removeItem('token')
        localStorage.removeItem('username')
        setToken(null)
        setUsername('')
      })
      .finally(() => setAuthChecked(true))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

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
    setCurrentTool(null)
  }

  // --- Render: verifying token ---
  if (!authChecked) {
    return <div className="app" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>Loading...</div>
  }

  // --- Render: not logged in ---
  if (!token) {
    return (
      <div className="app">
        <div className="auth-hero">
          <h1>ToolKit</h1>
          <p>Your personal collection of productivity tools</p>
        </div>
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
      </div>
    )
  }

  // --- Render: logged in ---
  return (
    <div className="app">
      <div className="app-header">
        <div className="header-left">
          {currentTool && (
            <button className="back-btn" onClick={() => setCurrentTool(null)}>
              ? Back
            </button>
          )}
          <h1 className="app-title" onClick={() => setCurrentTool(null)}>ToolKit</h1>
        </div>
        <div className="user-bar">
          <span>Logged in as <strong>{username}</strong></span>
          <button className="btn-outline btn-sm" onClick={handleLogout}>Logout</button>
        </div>
      </div>

      {currentTool === null && <HomePage onSelectTool={setCurrentTool} />}
      {currentTool === 'transcript' && <VideoTranscript token={token} onAuthError={handleLogout} />}
      {currentTool === 'webtopdf' && <WebToPdf token={token} onAuthError={handleLogout} />}
      {currentTool === 'webtopdf2' && <WebToPdf2 token={token} onAuthError={handleLogout} />}
      {currentTool === 'dsvpdf' && <DsvPagePdf token={token} onAuthError={handleLogout} />}
      {currentTool === 'teams' && <TeamsTranscript token={token} onAuthError={handleLogout} />}
      {currentTool === 'teamschat' && <TeamsChat token={token} onAuthError={handleLogout} />}
      {currentTool === 'bookconvert' && <BookConverter token={token} onAuthError={handleLogout} />}
      {currentTool === 'wechat' && <WechatExport token={token} onAuthError={handleLogout} />}
      {currentTool === 'discord' && <DiscordExport token={token} onAuthError={handleLogout} />}
      {currentTool === 'threads' && <ThreadsDownload token={token} onAuthError={handleLogout} />}
    </div>
  )
}

export default App
