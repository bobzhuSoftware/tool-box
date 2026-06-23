import { useState, useEffect } from 'react'

/**
 * Calibre engine status + per-machine configuration for the Book Converter
 * (EPUB → PDF uses Calibre's ebook-convert). Shows whether an engine is
 * reachable on this machine, offers a download link when missing, and lets the
 * user point to a custom Calibre install. The custom path is persisted
 * server-side per user.
 */
function CalibreStatus({ token, onAuthError }) {
  const [status, setStatus] = useState(null) // { installed, path, custom_path, cloud_available, download_url }
  const [editing, setEditing] = useState(false)
  const [pathInput, setPathInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const load = async () => {
    try {
      const res = await fetch('/api/book/calibre-status', { headers: authHeaders() })
      if (res.status === 401) { onAuthError && onAuthError(); return }
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const data = await res.json()
      setStatus(data)
      setPathInput(data.custom_path || '')
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const savePath = async () => {
    setSaving(true)
    setError('')
    try {
      const res = await fetch('/api/book/calibre-path', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ path: pathInput.trim() }),
      })
      if (res.status === 401) { onAuthError && onAuthError(); return }
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      setStatus((s) => ({ ...s, ...data }))
      setEditing(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const clearPath = async () => {
    setSaving(true)
    setError('')
    try {
      const res = await fetch('/api/book/calibre-path', {
        method: 'DELETE',
        headers: authHeaders(),
      })
      if (res.status === 401) { onAuthError && onAuthError(); return }
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      setPathInput('')
      setStatus((s) => ({ ...s, ...data }))
      setEditing(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (!status) return null

  const ok = status.installed || status.cloud_available
  const dot = status.installed ? '#2ea043' : status.cloud_available ? '#d29922' : '#cf222e'

  return (
    <div
      style={{
        marginTop: '8px',
        padding: '0.6rem 0.8rem',
        borderRadius: '8px',
        border: '1px solid var(--border, #e1e4e8)',
        background: 'var(--surface-muted, #fafbfc)',
        fontSize: '13px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: dot, flexShrink: 0 }} />
        {status.installed ? (
          <span>已检测到 Calibre 转换引擎</span>
        ) : status.cloud_available ? (
          <span>本机未安装 Calibre，将使用云端 API 转换</span>
        ) : (
          <span style={{ color: '#cf222e' }}>未检测到可用的转换引擎（EPUB → PDF 需要 Calibre）</span>
        )}
        <button
          type="button"
          onClick={() => { setEditing((v) => !v); setError('') }}
          style={{
            marginLeft: 'auto', padding: '0.15rem 0.5rem', borderRadius: '6px',
            border: '1px solid var(--border, #d0d7de)', background: 'transparent',
            cursor: 'pointer', fontSize: '12px',
          }}
        >
          {editing ? '收起' : '设置路径'}
        </button>
      </div>

      {status.installed && status.path && (
        <div style={{ marginTop: '4px', color: 'var(--text-muted, #888)', wordBreak: 'break-all' }}>
          {status.path}
        </div>
      )}

      {!status.installed && (
        <div style={{ marginTop: '6px' }}>
          <a href={status.download_url} target="_blank" rel="noreferrer" style={{ fontSize: '12px' }}>
            ↓ 下载并安装 Calibre
          </a>
          <span style={{ fontSize: '12px', color: 'var(--text-muted, #888)' }}>
            　安装后点击「设置路径」或刷新即可自动识别。
          </span>
        </div>
      )}

      {editing && (
        <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <input
            type="text"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            placeholder={'Calibre 安装目录或 ebook-convert 完整路径'}
            style={{ padding: '0.35rem 0.5rem', borderRadius: '6px', border: '1px solid var(--border, #d0d7de)', fontSize: '12px' }}
          />
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            <button
              type="button"
              onClick={savePath}
              disabled={saving}
              style={{ padding: '0.25rem 0.7rem', borderRadius: '6px', border: '1px solid #2da44e', background: '#2da44e', color: '#fff', cursor: 'pointer', fontSize: '12px' }}
            >
              {saving ? '保存中…' : '保存并验证'}
            </button>
            {status.custom_path && (
              <button
                type="button"
                onClick={clearPath}
                disabled={saving}
                style={{ padding: '0.25rem 0.7rem', borderRadius: '6px', border: '1px solid var(--border, #d0d7de)', background: 'transparent', cursor: 'pointer', fontSize: '12px' }}
              >
                清除（恢复自动探测）
              </button>
            )}
            <button
              type="button"
              onClick={load}
              disabled={saving}
              style={{ padding: '0.25rem 0.7rem', borderRadius: '6px', border: '1px solid var(--border, #d0d7de)', background: 'transparent', cursor: 'pointer', fontSize: '12px' }}
            >
              重新检测
            </button>
          </div>
        </div>
      )}

      {error && <div style={{ marginTop: '6px', color: '#cf222e', fontSize: '12px' }}>{error}</div>}
    </div>
  )
}

export default CalibreStatus
