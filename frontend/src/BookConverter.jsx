import { useState, useRef, useEffect, useCallback } from 'react'

const DIRECTIONS = [
  { value: 'epub2pdf', label: 'EPUB → PDF', accept: '.epub', srcLabel: 'EPUB', dstLabel: 'PDF', icon: '📚' },
  { value: 'pdf2epub', label: 'PDF → EPUB', accept: '.pdf', srcLabel: 'PDF', dstLabel: 'EPUB', icon: '📖' },
]

function BookConverter({ token, onAuthError }) {
  const [direction, setDirection] = useState('epub2pdf')
  const [file, setFile] = useState(null)
  const [isDragging, setIsDragging] = useState(false)
  const [loading, setLoading] = useState(false)
  const [progressLog, setProgressLog] = useState([])
  const [result, setResult] = useState(null) // { job_id, filename }
  const fileInputRef = useRef(null)
  const logContainerRef = useRef(null)
  const dragCounter = useRef(0) // track nested drag enter/leave

  useEffect(() => {
    const el = logContainerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [progressLog])

  const addLog = (entry) => setProgressLog((prev) => [...prev, entry])

  const currentDir = DIRECTIONS.find((d) => d.value === direction)

  const handleDirectionChange = (val) => {
    setDirection(val)
    setFile(null)
    setResult(null)
    setProgressLog([])
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const acceptFile = useCallback((f) => {
    if (!f) return
    const ext = f.name.split('.').pop().toLowerCase()
    const expected = currentDir.accept.replace('.', '')
    if (ext !== expected) {
      addLog({ type: 'error', message: `请拖入 ${currentDir.accept.toUpperCase()} 文件（当前方向：${currentDir.label}）` })
      return
    }
    setFile(f)
    setResult(null)
    setProgressLog([])
  }, [currentDir])

  const handleFileChange = (e) => {
    acceptFile(e.target.files?.[0] || null)
  }

  // ---- Drag & Drop handlers ----
  const handleDragEnter = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current += 1
    if (dragCounter.current === 1) setIsDragging(true)
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current -= 1
    if (dragCounter.current === 0) setIsDragging(false)
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current = 0
    setIsDragging(false)
    if (loading) return
    const dropped = e.dataTransfer.files?.[0] || null
    acceptFile(dropped)
  }

  const handleConvert = async () => {
    if (!file) return
    setLoading(true)
    setProgressLog([])
    setResult(null)

    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('direction', direction)

      const res = await fetch('/api/book/convert', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      })

      if (!res.ok) {
        if (res.status === 401 && onAuthError) { onAuthError(); return }
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `Server error (${res.status})`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''
        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (line.startsWith('data: ')) {
              try {
                const event = JSON.parse(line.slice(6))
                if (event.type === 'done') {
                  setResult({ job_id: event.job_id, filename: event.filename })
                  addLog({ type: 'done', message: `Conversion complete — ${event.filename}` })
                } else if (event.type === 'error') {
                  addLog({ type: 'error', message: event.message })
                } else if (event.type === 'status') {
                  addLog({ type: 'status', message: event.message })
                }
              } catch { /* ignore malformed lines */ }
            }
          }
        }
      }
    } catch (err) {
      addLog({ type: 'error', message: err.message || 'Something went wrong' })
    } finally {
      setLoading(false)
    }
  }

  const handleDownload = () => {
    if (!result) return
    const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
    window.open(`/api/book/download/${result.job_id}${authParam}`, '_blank')
  }

  return (
    <>
      <h2 className="tool-page-title">📚 Book Format Converter</h2>

      <div className="input-section">
        <p className="tool-description">
          Convert between PDF and EPUB formats. Upload a file and download the converted version.
        </p>

        {/* Direction toggle */}
        <div>
          <div style={{ fontSize: '0.82rem', color: '#666', marginBottom: '0.4rem', fontWeight: 600 }}>
            Conversion Direction
          </div>
          <div className="input-mode-toggle" style={{ maxWidth: '320px' }}>
            {DIRECTIONS.map((d) => (
              <button
                key={d.value}
                className={`mode-btn${direction === d.value ? ' active' : ''}`}
                onClick={() => handleDirectionChange(d.value)}
                disabled={loading}
              >
                {d.label}
              </button>
            ))}
          </div>
        </div>

        {/* File input — click or drag & drop */}
        <div>
          <div style={{ fontSize: '0.82rem', color: '#666', marginBottom: '0.4rem', fontWeight: 600 }}>
            Select {currentDir.srcLabel} file
          </div>
          <label
            className={`upload-area${file ? ' has-file' : ''}${isDragging ? ' drag-over' : ''}`}
            style={{ display: 'block' }}
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={currentDir.accept}
              onChange={handleFileChange}
              disabled={loading}
              style={{ display: 'none' }}
            />
            {isDragging ? (
              <div className="upload-placeholder">
                <span className="upload-icon">⬇</span>
                <p><strong>松开鼠标</strong>即可上传</p>
              </div>
            ) : file ? (
              <div className="upload-file-info">
                <span className="upload-file-icon">{currentDir.icon}</span>
                <span className="upload-file-name">{file.name}</span>
                <span className="upload-file-size">{(file.size / 1024).toFixed(0)} KB</span>
                {!loading && (
                  <button
                    className="upload-remove-btn"
                    title="Remove file"
                    onClick={(e) => { e.preventDefault(); setFile(null); setResult(null); setProgressLog([]); if (fileInputRef.current) fileInputRef.current.value = '' }}
                  >✕</button>
                )}
              </div>
            ) : (
              <div className="upload-placeholder">
                <span className="upload-icon">{currentDir.icon}</span>
                <p>拖拽文件到此处，或<strong>点击选择</strong></p>
                <p className="upload-hint">仅支持 {currentDir.accept} 格式</p>
              </div>
            )}
          </label>
        </div>

        {/* Convert button */}
        <div>
          <button
            className="btn-primary"
            style={{ padding: '0.7rem 1.8rem', borderRadius: '8px', fontSize: '0.95rem', fontWeight: 600, cursor: 'pointer', border: '2px solid #646cff' }}
            onClick={handleConvert}
            disabled={!file || loading}
          >
            {loading ? 'Converting…' : `Convert to ${currentDir.dstLabel}`}
          </button>
        </div>
      </div>

      {/* Progress log */}
      {progressLog.length > 0 && (
        <div className="progress-section">
          <div className="progress-log" ref={logContainerRef}>
            {progressLog.map((entry, i) => (
              <div key={i} className={`log-entry log-${entry.type}`}>
                <span className="log-icon">
                  {entry.type === 'done' ? '✓' : entry.type === 'error' ? '✕' : '●'}
                </span>
                <span className="log-message">{entry.message}</span>
              </div>
            ))}
            {loading && (
              <div className="log-entry log-status">
                <span className="spinner" />
                <span className="log-message">Please wait…</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Download result */}
      {result && (
        <div className="pdf-result">
          <div className="pdf-result-info">
            <span className="pdf-result-icon">{currentDir.icon}</span>
            <div>
              <div className="pdf-result-title">Ready to Download</div>
              <div className="pdf-result-url">{result.filename}</div>
            </div>
          </div>
          <button className="btn-primary pdf-download-btn" onClick={handleDownload}>
            ↓ Download {currentDir.dstLabel}
          </button>
        </div>
      )}
    </>
  )
}

export default BookConverter
