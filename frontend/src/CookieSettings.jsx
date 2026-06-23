import { useState, useEffect } from 'react'

/**
 * Per-user YouTube / Bilibili login cookies for the Video Transcript tool.
 *
 * Two mutually-exclusive options:
 *   1. Paste a Netscape-format cookies.txt (works anywhere, portable).
 *   2. Read cookies from a local browser (only when the server runs on the
 *      user's own machine).
 *
 * The raw cookies are never returned by the API — only status metadata
 * (domains + count). Choices are persisted server-side per user (encrypted).
 */
function CookieSettings({ token, onAuthError }) {
  const [status, setStatus] = useState(null)
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState('paste') // 'paste' | 'browser'
  const [text, setText] = useState('')
  const [browser, setBrowser] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const load = async () => {
    try {
      const res = await fetch('/api/transcribe/cookies', { headers: authHeaders() })
      if (res.status === 401) { onAuthError && onAuthError(); return }
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const data = await res.json()
      setStatus(data)
      setBrowser(data.browser || (data.supported_browsers?.[0] || ''))
      if (data.browser) setMode('browser')
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const body = mode === 'paste' ? { text: text.trim() } : { browser }
      const res = await fetch('/api/transcribe/cookies', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(body),
      })
      if (res.status === 401) { onAuthError && onAuthError(); return }
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      setStatus(data)
      setText('')
      setOpen(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const clear = async () => {
    setSaving(true)
    setError('')
    try {
      const res = await fetch('/api/transcribe/cookies', { method: 'DELETE', headers: authHeaders() })
      if (res.status === 401) { onAuthError && onAuthError(); return }
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      setStatus(data)
      setText('')
      setOpen(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (!status) return null

  const configured = status.has_cookies || !!status.browser
  const dot = configured ? '#2ea043' : status.global_fallback ? '#d29922' : '#8b949e'
  let summary
  if (status.has_cookies) {
    const doms = (status.domains || []).slice(0, 3).join(', ')
    summary = `已配置 cookies（${doms || '未知域名'}，共 ${status.cookie_count} 条）`
  } else if (status.browser) {
    summary = `使用本机浏览器：${status.browser}`
  } else if (status.global_fallback) {
    summary = '未单独配置，使用服务器默认 cookies'
  } else {
    summary = '未配置（公开视频可直接转录；YouTube 常需登录 cookie）'
  }

  return (
    <div
      style={{
        marginTop: '8px',
        padding: '0.55rem 0.75rem',
        borderRadius: '8px',
        border: '1px solid var(--border, #e1e4e8)',
        background: 'var(--surface-muted, #fafbfc)',
        fontSize: '13px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: dot, flexShrink: 0 }} />
        <span>🔑 YouTube 登录 Cookies — {summary}</span>
        <button
          type="button"
          onClick={() => { setOpen((v) => !v); setError('') }}
          style={{
            marginLeft: 'auto', padding: '0.15rem 0.5rem', borderRadius: '6px',
            border: '1px solid var(--border, #d0d7de)', background: 'transparent',
            cursor: 'pointer', fontSize: '12px',
          }}
        >
          {open ? '收起' : '配置'}
        </button>
      </div>

      {open && (
        <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ fontSize: '12px', color: 'var(--text-muted, #888)', lineHeight: 1.5 }}>
            主要用于 <strong>YouTube</strong>（绕过机器人检测 / 年龄限制 / 限流）。
            Bilibili 公开视频<strong>无需 cookie</strong>，仅会员/登录内容才需要——可放进同一份文件。
          </div>
          <div style={{ display: 'flex', gap: '6px' }}>
            <button
              type="button"
              onClick={() => setMode('paste')}
              style={tabStyle(mode === 'paste')}
            >粘贴 cookies.txt</button>
            <button
              type="button"
              onClick={() => setMode('browser')}
              style={tabStyle(mode === 'browser')}
            >读取本机浏览器</button>
          </div>

          {mode === 'paste' ? (
            <>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={'# Netscape HTTP Cookie File\n粘贴从已登录 YouTube 页面导出的 cookies.txt 内容…'}
                rows={5}
                style={{ padding: '0.4rem 0.5rem', borderRadius: '6px', border: '1px solid var(--border, #d0d7de)', fontSize: '12px', fontFamily: 'monospace', resize: 'vertical' }}
              />
              <div style={{ fontSize: '12px', color: 'var(--text-muted, #888)' }}>
                用 <a href="https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc" target="_blank" rel="noreferrer">Get cookies.txt LOCALLY</a> 扩展在<strong>已登录的 YouTube</strong> 页面导出（如需 B 站会员内容，再到 bilibili.com 导出合并）。
              </div>
            </>
          ) : (
            <>
              <select
                value={browser}
                onChange={(e) => setBrowser(e.target.value)}
                style={{ padding: '0.35rem 0.5rem', borderRadius: '6px', border: '1px solid var(--border, #d0d7de)', maxWidth: '240px' }}
              >
                {(status.supported_browsers || []).map((b) => (
                  <option key={b} value={b}>{b}</option>
                ))}
              </select>
              <div style={{ fontSize: '12px', color: 'var(--text-muted, #888)' }}>
                直接读取本机该浏览器的登录态（仅当服务运行在你自己的电脑上时有效）。
              </div>
            </>
          )}

          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            <button
              type="button"
              onClick={save}
              disabled={saving || (mode === 'paste' ? !text.trim() : !browser)}
              style={{ padding: '0.25rem 0.7rem', borderRadius: '6px', border: '1px solid #2da44e', background: '#2da44e', color: '#fff', cursor: 'pointer', fontSize: '12px' }}
            >
              {saving ? '保存中…' : '保存'}
            </button>
            {configured && (
              <button
                type="button"
                onClick={clear}
                disabled={saving}
                style={{ padding: '0.25rem 0.7rem', borderRadius: '6px', border: '1px solid var(--border, #d0d7de)', background: 'transparent', cursor: 'pointer', fontSize: '12px' }}
              >
                {status.global_fallback ? '清除并改用服务器 cookies' : '清除'}
              </button>
            )}
          </div>
          {configured && (
            <div style={{ fontSize: '11px', color: 'var(--text-muted, #888)' }}>
              {status.global_fallback
                ? '清除你的个人 cookies 配置后，会自动回退到服务器端默认 cookies。'
                : '清除后将不再使用任何 cookies（公开视频仍可正常转录）。'}
            </div>
          )}
        </div>
      )}

      {error && <div style={{ marginTop: '6px', color: '#cf222e', fontSize: '12px' }}>{error}</div>}
    </div>
  )
}

function tabStyle(active) {
  return {
    padding: '0.2rem 0.6rem',
    borderRadius: '6px',
    border: `1px solid ${active ? '#646cff' : 'var(--border, #d0d7de)'}`,
    background: active ? 'rgba(100,108,255,0.1)' : 'transparent',
    cursor: 'pointer',
    fontSize: '12px',
  }
}

export default CookieSettings
