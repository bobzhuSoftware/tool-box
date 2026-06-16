import { useState } from 'react'
import useSSEStream from './useSSEStream'

function ThreadsDownload({ token, onAuthError }) {
  const [url, setUrl] = useState('')
  const [result, setResult] = useState(null)
  const { progressLog, logContainerRef, addLog, loading, streamSSE } = useSSEStream()

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const handleDownload = async () => {
    if (!url.trim()) return
    setResult(null)

    await streamSSE(
      () =>
        fetch('/api/threads/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ url: url.trim() }),
        }),
      {
        onAuthError,
        onEvent: (event) => {
          if (event.type === 'done') {
            setResult(event)
            const tail = event.count > 1 ? ` · 共 ${event.count} 个视频` : ''
            addLog({ type: 'done', message: `✓ 下载完成${tail}` })
          } else if (event.type === 'error') {
            addLog({ type: 'error', message: event.message })
          } else if (event.type === 'progress') {
            addLog({ type: 'progress', message: event.message })
          } else {
            addLog({ type: 'status', message: event.message })
          }
        },
      }
    )
  }

  const handleFileDownload = () => {
    if (!result?.job_id) return
    const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
    window.open(`/api/threads/download/${result.job_id}${authParam}`, '_blank')
  }

  const downloadLabel = result?.count > 1 ? `下载 ZIP（${result.count} 个视频）` : '下载视频文件'

  return (
    <>
      <h2 className="tool-page-title">🧵 Threads 视频下载</h2>

      <div className="input-section">
        <p className="tool-description">
          粘贴一个 Threads 帖子链接，下载其中的视频（支持单个视频或多视频轮播）。仅支持公开帖子。
        </p>

        <div className="url-row">
          <input
            type="text"
            placeholder="https://www.threads.net/@username/post/XXXXXXXXXXX"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              setResult(null)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !loading) handleDownload()
            }}
            disabled={loading}
          />
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button
            className="btn-primary"
            onClick={handleDownload}
            disabled={loading || !url.trim()}
            style={{
              padding: '0.7rem 1.8rem',
              borderRadius: 8,
              fontSize: '0.95rem',
              fontWeight: 600,
              cursor: 'pointer',
              border: 'none',
              opacity: loading || !url.trim() ? 0.6 : 1,
            }}
          >
            {loading ? '正在下载…' : '开始下载'}
          </button>
        </div>
      </div>

      {progressLog.length > 0 && (
        <div className="progress-section">
          <div className="progress-log" ref={logContainerRef}>
            {progressLog.map((entry, i) => (
              <div key={i} className={`log-entry log-${entry.type}`}>
                {entry.message}
              </div>
            ))}
          </div>
        </div>
      )}

      {result && (
        <div className="pdf-result">
          <div className="pdf-result-info">
            <span className="pdf-result-icon">🧵</span>
            <div>
              <div className="pdf-result-title">
                {result.title || 'threads_video'}
                {result.uploader ? ` · @${result.uploader}` : ''}
              </div>
              <div className="pdf-result-url">{url}</div>
            </div>
          </div>
          <button className="pdf-download-btn btn-primary" onClick={handleFileDownload}>
            {downloadLabel}
          </button>
        </div>
      )}
    </>
  )
}

export default ThreadsDownload
