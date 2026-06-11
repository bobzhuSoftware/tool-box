import { useState } from 'react'

function DsvPagePdf({ token, onAuthError }) {
  const [url, setUrl] = useState('')
  const [normalized, setNormalized] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)

  const authHeaders = () => token ? { Authorization: `Bearer ${token}` } : {}

  const handleNormalize = async () => {
    if (!url.trim()) return
    setError('')
    setNormalized(null)
    setCopied(false)
    setLoading(true)
    try {
      const res = await fetch('/api/dsv-pdf/normalize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ url: url.trim() }),
      })
      if (res.status === 401) { onAuthError && onAuthError(); return }
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Normalization failed')
      setNormalized(data.normalized_url)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleOpen = () => {
    if (normalized) window.open(normalized, '_blank', 'noopener')
  }

  const handleCopy = async () => {
    if (!normalized) return
    try {
      await navigator.clipboard.writeText(normalized)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard API can fail in non-HTTPS contexts; fall back to prompt
      window.prompt('Copy this URL:', normalized)
    }
  }

  return (
    <>
      <h2 className="tool-page-title">🏢 DSV Page to PDF</h2>

      <div className="input-section">
        <p className="tool-description">
          Paste a DSV ServiceNow URL (the long <code>/now/nav/ui/classic/params/target/…</code> form
          or the bare <code>kb_view.do?sys_kb_id=…</code> form). The wrapper is stripped so you get a
          clean, printable page. Open it in your signed-in Edge and use <strong>Ctrl+P → Save as PDF</strong>.
        </p>
        <div className="url-row">
          <input
            type="text"
            placeholder="https://dsv.service-now.com/..."
            value={url}
            onChange={(e) => { setUrl(e.target.value); setNormalized(null); setError('') }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !loading) handleNormalize() }}
            disabled={loading}
          />
          <button onClick={handleNormalize} disabled={loading || !url.trim()}>
            {loading ? 'Working...' : 'Normalize URL'}
          </button>
        </div>
      </div>

      {error && (
        <div className="progress-section">
          <div className="progress-log">
            <div className="log-entry log-error">
              <span className="log-icon">✕</span>
              <span className="log-message">{error}</span>
            </div>
          </div>
        </div>
      )}

      {normalized && (
        <div className="pdf-result">
          <div className="pdf-result-info" style={{ flex: 1, minWidth: 0 }}>
            <span className="pdf-result-icon">🔗</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="pdf-result-title">Bare page URL</div>
              <div
                className="pdf-result-url"
                style={{ wordBreak: 'break-all', userSelect: 'all' }}
              >
                {normalized}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            <button className="btn-outline btn-sm" onClick={handleCopy}>
              {copied ? '✓ Copied' : '⧉ Copy'}
            </button>
            <button className="btn-primary pdf-download-btn" onClick={handleOpen}>
              ↗ Open in Browser
            </button>
          </div>
        </div>
      )}
    </>
  )
}

export default DsvPagePdf
