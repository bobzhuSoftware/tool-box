import { useRef, useState } from 'react'

const ACCEPTED_EXTS = ['.vtt', '.srt', '.txt']

function SubtitleProcessor({ token, onAuthError }) {
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [dragOver, setDragOver] = useState(false)
  const [splitMinutes, setSplitMinutes] = useState(30)
  const fileInputRef = useRef(null)

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const hasTimestamps =
    result && result.segments.some((s) => s.start && s.start !== '00:00:00')

  const pickFile = (f) => {
    if (!f) return
    const ext = '.' + (f.name.split('.').pop() || '').toLowerCase()
    if (!ACCEPTED_EXTS.includes(ext)) {
      setError(`不支持的格式：${ext}。请上传 ${ACCEPTED_EXTS.join(' / ')} 文件。`)
      return
    }
    setError('')
    setFile(f)
    setResult(null)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    if (e.dataTransfer.files?.[0]) pickFile(e.dataTransfer.files[0])
  }

  const handleUpload = async () => {
    if (!file) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch('/api/subtitle/upload', {
        method: 'POST',
        headers: authHeaders(),
        body: form,
      })
      if (res.status === 401) {
        onAuthError?.()
        return
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `服务器错误 ${res.status}`)
      }
      setResult(await res.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleDownload = async (withTimestamps, chunkMinutes = 0) => {
    if (!result) return
    const ts = withTimestamps ? 'true' : 'false'
    const chunkParam = chunkMinutes > 0 ? `&chunk_minutes=${chunkMinutes}` : ''
    const authParam = token ? `&token=${encodeURIComponent(token)}` : ''

    if (chunkMinutes > 0) {
      // ZIP binary — must use fetch+Blob so the browser downloads it.
      try {
        const res = await fetch(
          `/api/download/${result.job_id}?timestamps=${ts}${chunkParam}${authParam}`,
          { headers: authHeaders() }
        )
        if (!res.ok) throw new Error(`服务器错误 ${res.status}`)
        const blob = await res.blob()
        const blobUrl = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = blobUrl
        const disposition = res.headers.get('content-disposition') || ''
        const rfc5987Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
        const asciiMatch = disposition.match(/filename="([^"]+)"/)
        a.download = rfc5987Match
          ? decodeURIComponent(rfc5987Match[1].trim())
          : asciiMatch ? asciiMatch[1] : `subtitle_split${chunkMinutes}min.zip`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(blobUrl)
      } catch (err) {
        setError('下载失败：' + err.message)
      }
    } else {
      window.open(
        `/api/download/${result.job_id}?timestamps=${ts}${authParam}`,
        '_blank'
      )
    }
  }

  return (
    <>
      <h2 className="tool-page-title">📝 字幕处理</h2>

      <div className="input-section">
        <p style={{ color: '#666', fontSize: '0.9rem', marginTop: 0 }}>
          上传已有的字幕文件（VTT / SRT / TXT），转换为纯文本、带时间戳文本，或按分钟拆分的多文件 ZIP 下载。
        </p>

        <label
          className={`upload-area${file ? ' has-file' : ''}${dragOver ? ' drag-over' : ''}`}
          style={{ display: 'block' }}
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_EXTS.join(',')}
            style={{ display: 'none' }}
            onChange={(e) => pickFile(e.target.files?.[0])}
            disabled={loading}
          />
          {file ? (
            <div className="upload-file-info">
              <span className="upload-file-icon">📝</span>
              <span className="upload-file-name">{file.name}</span>
              <span className="upload-file-size">{(file.size / 1024).toFixed(0)} KB</span>
            </div>
          ) : (
            <div className="upload-placeholder">
              <span className="upload-icon">⬆</span>
              <p><strong>点击选择</strong>或拖放字幕文件到此处</p>
              <p className="upload-hint">支持 {ACCEPTED_EXTS.join(' / ')}</p>
            </div>
          )}
        </label>

        <div style={{ marginTop: 12 }}>
          <button
            className="btn-primary"
            onClick={handleUpload}
            disabled={!file || loading}
          >
            {loading ? '处理中…' : '解析字幕'}
          </button>
        </div>

        {error && <div className="error-message" style={{ marginTop: 12 }}>{error}</div>}
      </div>

      {result && (
        <div className="result-section">
          <div className="result-header">
            <h2>{result.title}</h2>
          </div>
          <div className="transcript-box">
            {result.segments.map((seg, i) => (
              <div className="segment" key={i}>
                {hasTimestamps && (
                  <div className="time">[{seg.start} → {seg.end}]</div>
                )}
                <p className="segment-text">{seg.text}</p>
              </div>
            ))}
          </div>
          <div className="download-row">
            {hasTimestamps && (
              <button className="btn-primary" onClick={() => handleDownload(true)}>
                下载（带时间戳）
              </button>
            )}
            <button className="btn-outline" onClick={() => handleDownload(false)}>
              下载纯文本
            </button>
            {hasTimestamps && (
              <span className="split-controls">
                <button
                  className="btn-outline"
                  onClick={() => handleDownload(true, splitMinutes)}
                  title="打包为 ZIP，每个文件覆盖 N 分钟（保留时间戳）"
                >
                  ✂ 按 X 分钟拆分（ZIP）
                </button>
                <input
                  type="number"
                  min={1}
                  value={splitMinutes}
                  onChange={(e) => setSplitMinutes(Math.max(1, Number(e.target.value) || 1))}
                  className="split-input"
                  title="每个分段的分钟数"
                />
                <span className="split-unit">分钟</span>
              </span>
            )}
          </div>
          {!hasTimestamps && (
            <p className="hint" style={{ marginTop: 8, opacity: 0.7 }}>
              该文件无时间戳，仅支持纯文本下载（无法按分钟拆分）。
            </p>
          )}
        </div>
      )}
    </>
  )
}

export default SubtitleProcessor
