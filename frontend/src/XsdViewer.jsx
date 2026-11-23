import { useMemo, useRef, useState } from 'react'
import './EdifactViewer.css'

function XsdViewer({ token, onAuthError }) {
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [result, setResult] = useState(null) // { html, filename, warnings }
  const inputRef = useRef(null)

  const authHeaders = useMemo(() => (token ? { Authorization: `Bearer ${token}` } : {}), [token])

  const pickFiles = (list) => {
    const picked = Array.from(list || []).filter((f) => /\.(xsd|zip)$/i.test(f.name))
    if (!picked.length) {
      setError('请选择 .xsd 文件或包含 .xsd 的 .zip 压缩包')
      return
    }
    setError('')
    setResult(null)
    setFiles(picked)
  }

  const handleGenerate = async () => {
    if (!files.length) return
    setLoading(true)
    setError('')
    try {
      const form = new FormData()
      files.forEach((f) => form.append('files', f, f.name))
      const res = await fetch('/api/xsd/html', { method: 'POST', headers: authHeaders, body: form })
      if (res.status === 401) {
        onAuthError?.()
        throw new Error('登录已过期')
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `服务器错误 ${res.status}`)
      }
      const html = await res.text()
      const warn = decodeURIComponent(res.headers.get('X-Warnings') || '')
      setResult({
        html,
        filename: decodeURIComponent(res.headers.get('X-Filename') || 'xsd_structure.html'),
        warnings: warn ? warn.split('\n') : [],
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const blobUrl = () => URL.createObjectURL(new Blob([result.html], { type: 'text/html;charset=utf-8' }))

  const handleDownload = () => {
    const url = blobUrl()
    const a = document.createElement('a')
    a.href = url
    a.download = result.filename
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const handleOpen = () => {
    const url = blobUrl()
    window.open(url, '_blank')
    setTimeout(() => URL.revokeObjectURL(url), 60000)
  }

  return (
    <>
      <h2 className="tool-page-title">🧬 XSD 结构查看</h2>

      <div className="input-section">
        <p className="tool-description">
          导入本地 <strong>.zip</strong>（内含多个 .xsd）或直接选择多个 <strong>.xsd</strong> 文件，解析 import/include 关联后生成
          <strong>可搜索、可折叠的本地 HTML</strong>（元素层级、基数、类型、属性、枚举与约束）。文件只在内存中处理，不会保存。
        </p>
        <div
          className="edx-row"
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); pickFiles(e.dataTransfer.files) }}
          style={dragOver ? { outline: '2px dashed #2563eb', outlineOffset: 4 } : undefined}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".xsd,.zip"
            multiple
            style={{ display: 'none' }}
            onChange={(e) => { pickFiles(e.target.files); e.target.value = '' }}
          />
          <button className="btn-outline" onClick={() => inputRef.current?.click()} disabled={loading}>
            选择 ZIP / XSD 文件
          </button>
          <div className="edx-field edx-grow">
            {files.length ? files.map((f) => f.name).join('、') : '也可以把文件拖到这里'}
          </div>
          <button className="edx-go" onClick={handleGenerate} disabled={loading || !files.length}>
            {loading ? '解析中...' : '生成 HTML'}
          </button>
        </div>
        {error && <div className="error-message">{error}</div>}
        {result?.warnings.length > 0 && (
          <div className="edx-hint">
            {result.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
          </div>
        )}
      </div>

      {result && (
        <div className="edx-result">
          <div className="edx-result-bar">
            <strong>{result.filename}</strong>
            <span className="edx-spacer" />
            <button className="btn-outline btn-sm" onClick={handleOpen}>新窗口打开</button>
            <button className="btn-primary" onClick={handleDownload}>下载 HTML</button>
          </div>
          <iframe className="edx-frame" title="XSD 结构预览" srcDoc={result.html} sandbox="allow-scripts" />
        </div>
      )}
    </>
  )
}

export default XsdViewer
