import { useEffect, useMemo, useRef, useState } from 'react'
import './RichTextToHtml.css'

const VOID_TAGS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr',
])

// Clipboard HTML wraps the real content in StartFragment/EndFragment markers.
function extractFragment(html) {
  const m = html.match(/<!--\s*StartFragment\s*-->([\s\S]*?)<!--\s*EndFragment\s*-->/i)
  return m ? m[1] : html
}

// Minimal indenter: one tag or text run per line; <pre>/<style>/<script> bodies are kept verbatim.
function prettyHtml(html) {
  const tokens = html.match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g) || []
  const lines = []
  let depth = 0
  let raw = null // tag name whose body is being kept verbatim
  let rawBuf = ''
  for (const tok of tokens) {
    if (raw) {
      const close = new RegExp(`^</${raw}\\s*>$`, 'i')
      if (close.test(tok)) {
        lines[lines.length - 1] += rawBuf + tok
        raw = null
        rawBuf = ''
        depth = Math.max(0, depth - 1)
      } else {
        rawBuf += tok
      }
      continue
    }
    if (!tok.startsWith('<')) {
      const t = tok.replace(/\s+/g, ' ').trim()
      if (t) lines.push('  '.repeat(depth) + t)
      continue
    }
    if (tok.startsWith('<!--') || tok.startsWith('<!')) {
      lines.push('  '.repeat(depth) + tok)
      continue
    }
    const closing = tok.startsWith('</')
    const name = (tok.match(/^<\/?\s*([a-zA-Z0-9-]+)/) || [])[1]?.toLowerCase()
    if (closing) {
      depth = Math.max(0, depth - 1)
      lines.push('  '.repeat(depth) + tok)
    } else if (VOID_TAGS.has(name) || tok.endsWith('/>')) {
      lines.push('  '.repeat(depth) + tok)
    } else {
      lines.push('  '.repeat(depth) + tok)
      depth += 1
      if (name === 'pre' || name === 'style' || name === 'script') raw = name
    }
  }
  return lines.join('\n')
}

function RichTextToHtml() {
  const [html, setHtml] = useState('')
  const [text, setText] = useState('')
  const [hasPasted, setHasPasted] = useState(false)
  const [tab, setTab] = useState('html') // 'html' | 'text'
  const [stripFragment, setStripFragment] = useState(false)
  const [pretty, setPretty] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const copiedTimer = useRef(null)

  const applyClipboard = (data) => {
    setHtml(data.getData('text/html') || '')
    setText(data.getData('text/plain') || '')
    setHasPasted(true)
    setError('')
  }

  // Listen at document level so Ctrl+V works anywhere on the page.
  useEffect(() => {
    const onPaste = (e) => {
      if (!e.clipboardData) return
      e.preventDefault()
      applyClipboard(e.clipboardData)
    }
    document.addEventListener('paste', onPaste)
    return () => {
      document.removeEventListener('paste', onPaste)
      clearTimeout(copiedTimer.current)
    }
  }, [])

  const readFromClipboard = async () => {
    setError('')
    try {
      const items = await navigator.clipboard.read()
      let h = ''
      let t = ''
      for (const item of items) {
        if (item.types.includes('text/html')) h = await (await item.getType('text/html')).text()
        if (item.types.includes('text/plain')) t = await (await item.getType('text/plain')).text()
      }
      setHtml(h)
      setText(t)
      setHasPasted(true)
    } catch {
      setError('无法读取剪贴板（浏览器未授权或不支持），请直接按 Ctrl+V。')
    }
  }

  const outputHtml = useMemo(() => {
    let out = html
    if (stripFragment) out = extractFragment(out)
    if (pretty) out = prettyHtml(out)
    return out
  }, [html, stripFragment, pretty])

  const output = tab === 'html' ? outputHtml : text

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(output)
      setCopied(true)
      clearTimeout(copiedTimer.current)
      copiedTimer.current = setTimeout(() => setCopied(false), 1500)
    } catch {
      setError('复制失败，请手动选中右侧文本复制。')
    }
  }

  const handleDownload = () => {
    const blob = new Blob([output], { type: tab === 'html' ? 'text/html;charset=utf-8' : 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = tab === 'html' ? 'clipboard.html' : 'clipboard.txt'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  const handleClear = () => {
    setHtml('')
    setText('')
    setHasPasted(false)
    setError('')
  }

  const noHtml = hasPasted && !html

  return (
    <>
      <h2 className="tool-page-title">🧬 富文本转 HTML</h2>

      <div className="input-section">
        <p className="rt-intro">
          复制任意富文本（网页、Word、Teams、Copilot 等），在本页按 <kbd>Ctrl</kbd>+<kbd>V</kbd>，右侧即显示剪贴板中的原始 HTML 源码。
        </p>

        <div className="rt-toolbar">
          <button type="button" className="btn-secondary" onClick={readFromClipboard}>
            从剪贴板读取
          </button>
          <label className="rt-check">
            <input type="checkbox" checked={stripFragment} onChange={(e) => setStripFragment(e.target.checked)} />
            仅保留 Fragment 片段
          </label>
          <label className="rt-check">
            <input type="checkbox" checked={pretty} onChange={(e) => setPretty(e.target.checked)} />
            美化缩进
          </label>
        </div>
        {error && <p className="rt-error">{error}</p>}

        <div className="rt-panes">
          <div className="rt-pane">
            <div className="rt-pane-head">
              <span className="rt-pane-title">预览</span>
            </div>
            {hasPasted ? (
              <iframe className="rt-preview" title="preview" sandbox="" srcDoc={html || `<pre style="font-family:inherit;white-space:pre-wrap">${text.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))}</pre>`} />
            ) : (
              <div className="rt-placeholder">等待粘贴… 请按 Ctrl+V</div>
            )}
          </div>

          <div className="rt-pane">
            <div className="rt-pane-head">
              <div className="rt-tabs">
                <button type="button" className={tab === 'html' ? 'active' : ''} onClick={() => setTab('html')}>HTML</button>
                <button type="button" className={tab === 'text' ? 'active' : ''} onClick={() => setTab('text')}>纯文本</button>
              </div>
              <div className="rt-actions">
                <button type="button" className="btn-sm btn-primary" onClick={handleCopy} disabled={!output}>
                  {copied ? '已复制' : '复制'}
                </button>
                <button type="button" className="btn-sm btn-outline" onClick={handleDownload} disabled={!output}>
                  下载
                </button>
                <button type="button" className="btn-sm btn-danger" onClick={handleClear} disabled={!hasPasted}>
                  清空
                </button>
              </div>
            </div>
            {tab === 'html' && noHtml && <p className="rt-warn">剪贴板中没有 HTML 格式（复制的是纯文本）。</p>}
            <textarea
              className="rt-output"
              readOnly
              spellCheck={false}
              value={output}
              placeholder={tab === 'html' ? 'HTML 源码将显示在这里' : '纯文本将显示在这里'}
            />
          </div>
        </div>
      </div>
    </>
  )
}

export default RichTextToHtml
