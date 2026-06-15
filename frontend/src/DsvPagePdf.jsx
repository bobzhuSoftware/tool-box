import { useState } from 'react'

// Print-friendly CSS + auto window.print(). Wrapped as javascript: so it can be
// dragged to the bookmarks bar and run on any DSV page with one click.
const BOOKMARKLET_HREF = "javascript:(function(){const s=document.createElement('style');s.innerHTML='@media print,screen{table{table-layout:auto!important;width:100%!important}th,td{white-space:normal!important;word-break:break-word!important;font-size:10px!important;padding:2px 4px!important}.kb_article_text,#kb_article_view{max-width:none!important}}';document.head.appendChild(s);setTimeout(function(){window.print()},200);})();"

function DsvPagePdf({ token, onAuthError }) {
  const [url, setUrl] = useState('')
  const [normalized, setNormalized] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)
  const [snippetCopied, setSnippetCopied] = useState(false)

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

  const handleCopySnippet = async () => {
    try {
      await navigator.clipboard.writeText(BOOKMARKLET_HREF)
      setSnippetCopied(true)
      setTimeout(() => setSnippetCopied(false), 1500)
    } catch {
      window.prompt('Copy this snippet:', BOOKMARKLET_HREF)
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

      <div
        className="input-section"
        style={{ marginTop: 24, borderTop: '1px solid #e5e7eb', paddingTop: 20 }}
      >
        <h3 style={{ margin: '0 0 8px', fontSize: 16 }}>📌 One-time setup: "DSV Print Fix" bookmarklet</h3>
        <p className="tool-description" style={{ marginTop: 0 }}>
          Long ServiceNow tables get cut off when printed. Drag the button below to your browser&apos;s
          <strong> bookmarks bar</strong> (press <code>Ctrl+Shift+B</code> if it&apos;s hidden), then on any
          DSV page click the bookmark — it injects print-friendly CSS and opens the print dialog automatically.
        </p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          {/* eslint-disable-next-line react/jsx-no-script-url */}
          <a
            href={BOOKMARKLET_HREF}
            onClick={(e) => {
              e.preventDefault()
              alert("Drag this button to your bookmarks bar — don't click it here.")
            }}
            draggable
            style={{
              display: 'inline-block',
              padding: '8px 16px',
              background: '#2563eb',
              color: '#fff',
              borderRadius: 6,
              textDecoration: 'none',
              fontWeight: 600,
              cursor: 'grab',
              userSelect: 'none',
            }}
            title="Drag me to the bookmarks bar"
          >
            ⤓ DSV Print Fix
          </a>
          <button className="btn-outline btn-sm" onClick={handleCopySnippet}>
            {snippetCopied ? '✓ Copied' : '⧉ Copy snippet'}
          </button>
        </div>
        <details style={{ marginTop: 12, fontSize: 13, color: '#555' }}>
          <summary style={{ cursor: 'pointer' }}>Can&apos;t drag? Add it manually</summary>
          <ol style={{ marginTop: 8, paddingLeft: 20 }}>
            <li>Right-click the bookmarks bar → <em>Add favorite</em>.</li>
            <li>Name: <code>DSV Print Fix</code></li>
            <li>URL: click <strong>Copy snippet</strong> above and paste.</li>
            <li>Save. Click the bookmark on any DSV page when you want to print.</li>
          </ol>
        </details>
      </div>
    </>
  )
}

export default DsvPagePdf
