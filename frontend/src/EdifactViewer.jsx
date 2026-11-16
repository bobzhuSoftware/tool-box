import { useEffect, useMemo, useState } from 'react'
import './EdifactViewer.css'

const DEFAULT_VERSION = 'D01B'

function EdifactViewer({ token, onAuthError }) {
  const [versions, setVersions] = useState([])
  const [version, setVersion] = useState(DEFAULT_VERSION)
  const [messages, setMessages] = useState([])
  const [message, setMessage] = useState('IFTMIN')
  const [includeCodes, setIncludeCodes] = useState(true)
  const [listLoading, setListLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null) // { html, filename, version, message }

  const authHeaders = useMemo(() => (token ? { Authorization: `Bearer ${token}` } : {}), [token])

  const request = async (url, options) => {
    const res = await fetch(url, { ...options, headers: { ...authHeaders, ...(options?.headers || {}) } })
    if (res.status === 401) {
      onAuthError?.()
      throw new Error('登录已过期')
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `服务器错误 ${res.status}`)
    }
    return res
  }

  useEffect(() => {
    let cancelled = false
    setListLoading(true)
    request('/api/edifact/versions')
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return
        setVersions(d.versions)
        setVersion((v) => (d.versions.includes(v) ? v : d.versions[0] || v))
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setListLoading(false))
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!version) return undefined
    let cancelled = false
    setMessages([])
    request(`/api/edifact/messages?version=${encodeURIComponent(version)}`)
      .then((r) => r.json())
      .then((d) => !cancelled && setMessages(d.messages))
      .catch((e) => !cancelled && setError(e.message))
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version])

  const code = message.trim().toUpperCase()
  const known = messages.length === 0 || messages.includes(code)

  const handleGenerate = async (refresh = false) => {
    if (!code || !version) return
    setLoading(true)
    setError('')
    try {
      const res = await request('/api/edifact/html', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version, message: code, include_codes: includeCodes, refresh }),
      })
      const html = await res.text()
      setResult({
        html,
        filename: res.headers.get('X-Filename') || `${code}_${version}.html`,
        version,
        message: code,
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleDownload = () => {
    if (!result) return
    const url = URL.createObjectURL(new Blob([result.html], { type: 'text/html;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = result.filename
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const handleOpen = () => {
    if (!result) return
    const url = URL.createObjectURL(new Blob([result.html], { type: 'text/html;charset=utf-8' }))
    window.open(url, '_blank')
    setTimeout(() => URL.revokeObjectURL(url), 60000)
  }

  return (
    <>
      <h2 className="tool-page-title">🧾 EDIFACT 结构查看</h2>

      <div className="input-section">
        <p className="tool-description">
          选择 UN/EDIFACT 版本和消息类型，自动下载官方目录数据并生成<strong>无广告的本地 HTML</strong>（段/组层级、M/C、重复次数、元素与代码值，可搜索）。
          数据首次下载后缓存在本地，之后可离线使用。
        </p>
        <div className="edx-row">
          <div className="edx-field">
            <label className="field-label">版本</label>
            <select value={version} onChange={(e) => setVersion(e.target.value)} disabled={loading || listLoading}>
              {(versions.length ? versions : [version]).map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
          <div className="edx-field edx-grow">
            <label className="field-label">消息类型</label>
            <input
              type="text"
              list="edx-messages"
              placeholder="例如 IFTMIN、ORDERS、INVOIC"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !loading) handleGenerate() }}
              disabled={loading}
              spellCheck={false}
            />
            <datalist id="edx-messages">
              {messages.map((m) => <option key={m} value={m} />)}
            </datalist>
          </div>
          <button className="edx-go" onClick={() => handleGenerate()} disabled={loading || !code || !known}>
            {loading ? '生成中...' : '生成 HTML'}
          </button>
        </div>
        <label className="edx-check">
          <input type="checkbox" checked={includeCodes} onChange={(e) => setIncludeCodes(e.target.checked)} disabled={loading} />
          包含代码值（文件较大，约 1 MB；首次需额外下载代码表）
        </label>
        {!known && <div className="edx-hint">{version} 版本中没有 {code} 这个消息类型。</div>}
        {error && <div className="error-message">{error}</div>}
      </div>

      {result && (
        <div className="edx-result">
          <div className="edx-result-bar">
            <strong>{result.message} · {result.version}</strong>
            <span className="edx-saved">已保存：data/edifact/output/{result.filename}</span>
            <span className="edx-spacer" />
            <button className="btn-outline btn-sm" onClick={handleOpen}>新窗口打开</button>
            <button className="btn-outline btn-sm" onClick={() => handleGenerate(true)} disabled={loading}>重新下载数据</button>
            <button className="btn-primary" onClick={handleDownload}>下载 HTML</button>
          </div>
          <iframe className="edx-frame" title="EDIFACT 结构预览" srcDoc={result.html} sandbox="allow-scripts" />
        </div>
      )}
    </>
  )
}

export default EdifactViewer
