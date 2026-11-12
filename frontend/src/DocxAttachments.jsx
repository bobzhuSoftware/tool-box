import { useState } from 'react'

const KIND_LABEL = {
  package: '文件包',
  pdf: 'PDF',
  office: 'Office',
  unknown: '未知',
}

const formatSize = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`)

function DocxAttachments({ token, onAuthError }) {
  const [path, setPath] = useState('')
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [result, setResult] = useState(null)
  const [selected, setSelected] = useState(() => new Set())
  const [folder, setFolder] = useState('')
  const [dragOver, setDragOver] = useState(false)

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const request = async (url, options) => {
    const res = await fetch(url, { ...options, headers: { ...authHeaders(), ...(options?.headers || {}) } })
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

  const handleParse = async (picked = file) => {
    setLoading(true)
    setError('')
    setInfo('')
    setResult(null)
    try {
      const form = new FormData()
      if (picked) form.append('file', picked)
      else form.append('path', path)
      const data = await (await request('/api/docx-attachments', { method: 'POST', body: form })).json()
      setResult(data)
      setSelected(new Set(data.attachments.map((a) => a.index)))
      setFolder('')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const pickFile = (f) => {
    if (!f || loading) return
    if (!/\.(docx|docm)$/i.test(f.name)) {
      setError('仅支持 .docx / .docm 文件。')
      return
    }
    setFile(f)
    setPath('')
    handleParse(f)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    pickFile(e.dataTransfer.files?.[0])
  }

  const saveBlob = async (res, fallbackName) => {
    const disposition = res.headers.get('content-disposition') || ''
    const match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = match ? decodeURIComponent(match[1].trim()) : fallbackName
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const handleDownload = async (att) => {
    setError('')
    try {
      await saveBlob(await request(`/api/docx-attachments/${result.id}/${att.index}`), att.name)
    } catch (err) {
      setError('下载失败：' + err.message)
    }
  }

  const handleZip = async () => {
    setError('')
    try {
      await saveBlob(await request(`/api/docx-attachments/${result.id}/zip`), 'attachments.zip')
    } catch (err) {
      setError('下载失败：' + err.message)
    }
  }

  const handleSave = async () => {
    setError('')
    setInfo('')
    try {
      const res = await request(`/api/docx-attachments/${result.id}/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder, indexes: [...selected] }),
      })
      const data = await res.json()
      setInfo(`已保存 ${data.saved.length} 个文件到：${data.folder}`)
    } catch (err) {
      setError('保存失败：' + err.message)
    }
  }

  const toggle = (index) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })

  const canSave = result && selected.size > 0 && (folder.trim() || result.source_path)

  return (
    <>
      <h2 className="tool-page-title">📎 Word 附件提取</h2>

      <div className="input-section">
        <p className="tool-description">
          提取 <code>.docx / .docm</code> 中嵌入的附件（PDF、Excel、Word、zip、msg、edi 等）。
          可填写本机文件路径，或选择文件上传。
        </p>
        <label className="field-label">文件路径</label>
        <div className="url-row">
          <input
            type="text"
            placeholder="例如 C:\Users\you\Documents\spec.docx"
            value={path}
            onChange={(e) => { setPath(e.target.value); setFile(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !loading && path.trim()) handleParse() }}
            disabled={loading}
          />
          <button onClick={() => handleParse()} disabled={loading || (!path.trim() && !file)}>
            {loading ? '解析中...' : '提取附件'}
          </button>
        </div>
        <label
          className={`upload-area${file ? ' has-file' : ''}${dragOver ? ' drag-over' : ''}`}
          style={{ display: 'block', marginTop: 14 }}
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          <input
            type="file"
            accept=".docx,.docm"
            style={{ display: 'none' }}
            disabled={loading}
            onChange={(e) => { pickFile(e.target.files?.[0]); e.target.value = '' }}
          />
          {file ? (
            <div className="upload-file-info">
              <span className="upload-file-icon">📎</span>
              <span className="upload-file-name">{file.name}</span>
              <span className="upload-file-size">{formatSize(file.size)}</span>
            </div>
          ) : (
            <div className="upload-placeholder">
              <span className="upload-icon">⬆</span>
              <p><strong>点击选择</strong>或拖放 Word 文件到此处</p>
              <p className="upload-hint">支持 .docx / .docm，拖入后自动提取</p>
            </div>
          )}
        </label>
        {error && <div className="error-message" style={{ marginTop: 12 }}>{error}</div>}
      </div>

      {result && (
        <div className="result-section">
          <div className="result-header">
            <h2>{result.filename}</h2>
          </div>
          {result.attachments.length === 0 ? (
            <p className="category-empty">该文档中没有嵌入附件。</p>
          ) : (
            <>
              <table className="excel-search-table">
                <thead>
                  <tr>
                    <th>
                      <input
                        type="checkbox"
                        checked={selected.size === result.attachments.length}
                        onChange={(e) =>
                          setSelected(e.target.checked ? new Set(result.attachments.map((a) => a.index)) : new Set())
                        }
                      />
                    </th>
                    <th>#</th>
                    <th>文件名</th>
                    <th>类型</th>
                    <th>大小</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {result.attachments.map((a) => (
                    <tr key={a.index}>
                      <td>
                        <input type="checkbox" checked={selected.has(a.index)} onChange={() => toggle(a.index)} />
                      </td>
                      <td>{a.index}</td>
                      <td style={{ wordBreak: 'break-all' }}>{a.name}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>{KIND_LABEL[a.kind] || a.kind}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>{formatSize(a.size)}</td>
                      <td>
                        <button className="btn-outline btn-sm" onClick={() => handleDownload(a)}>下载</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="download-row" style={{ marginTop: 14 }}>
                <button className="btn-primary" onClick={handleZip}>全部打包下载（ZIP）</button>
              </div>

              <label className="field-label" style={{ marginTop: 14 }}>保存到本机文件夹（所选 {selected.size} 个）</label>
              <div className="url-row">
                <input
                  type="text"
                  placeholder={result.source_path ? '留空则保存到文档同目录下的 “文档名_attachments” 文件夹' : '例如 C:\\Users\\you\\Downloads\\attachments'}
                  value={folder}
                  onChange={(e) => setFolder(e.target.value)}
                />
                <button onClick={handleSave} disabled={!canSave}>保存</button>
              </div>
              {info && <p className="tool-description" style={{ marginTop: 8 }}>{info}</p>}
            </>
          )}
        </div>
      )}
    </>
  )
}

export default DocxAttachments
