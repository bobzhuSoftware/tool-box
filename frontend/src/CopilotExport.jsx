import { useState } from 'react'
import useSSEStream from './useSSEStream'
import EdgeProfilePicker from './EdgeProfilePicker'

function CopilotExport({ token, onAuthError }) {
  const [url, setUrl] = useState('')
  const [exportFormat, setExportFormat] = useState('html')
  const [exporting, setExporting] = useState(false)
  const { progressLog, logContainerRef, addLog, readSSEStream } = useSSEStream()

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const handleExport = async () => {
    const trimmed = url.trim()
    if (!trimmed) return
    setExporting(true)
    addLog({ type: 'status', message: '正在启动导出…' })

    try {
      const res = await fetch('/api/copilot-chat/export/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ url: trimmed, format: exportFormat }),
      })

      if (!res.ok) {
        if (res.status === 401 && onAuthError) { onAuthError(); return }
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `Server error (${res.status})`)
      }

      await readSSEStream(res, (event) => {
        if (event.type === 'done') {
          addLog({ type: 'done', message: `✓ 导出完成（${event.count} 条消息）` })
          const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
          window.open(`/api/copilot-chat/download/${event.job_id}${authParam}`, '_blank')
        } else if (event.type === 'error') {
          addLog({ type: 'error', message: event.message })
        } else {
          addLog({ type: 'status', message: event.message })
        }
      })
    } catch (err) {
      addLog({ type: 'error', message: err.message })
    } finally {
      setExporting(false)
    }
  }

  return (
    <>
      <h2 className="tool-page-title">🤖 Copilot 对话导出</h2>

      <div className="input-section">
        <p className="tool-description">
          粘贴一条 Microsoft 365 Copilot 对话链接（形如
          {' '}<code>https://m365.cloud.microsoft/chat/conversation/…</code>），
          通过你已登录的 Edge 会话抓取整段对话并导出为 HTML/TXT 文件。
          需要你已在 Edge 中登录 Microsoft 365。
        </p>
        <EdgeProfilePicker token={token} onAuthError={onAuthError} />

        <div className="url-row" style={{ marginTop: '0.75rem' }}>
          <input
            type="text"
            placeholder="https://m365.cloud.microsoft/chat/conversation/…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={exporting}
            style={{ flex: 1 }}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', whiteSpace: 'nowrap' }}>
            <span style={{ fontSize: '0.85rem', color: '#888' }}>格式</span>
            <select
              value={exportFormat}
              onChange={(e) => setExportFormat(e.target.value)}
              disabled={exporting}
              style={{ padding: '0.3rem 0.5rem', borderRadius: '6px' }}
            >
              <option value="html">HTML</option>
              <option value="txt">TXT</option>
            </select>
          </div>
          <button onClick={handleExport} disabled={exporting || !url.trim()}>
            {exporting ? '导出中...' : '导出对话'}
          </button>
        </div>
      </div>

      {progressLog.length > 0 && (
        <div className="progress-section" style={{ marginTop: '1rem' }}>
          <h3>日志</h3>
          <div className="progress-log" ref={logContainerRef}>
            {progressLog.map((entry, i) => (
              <div key={i} className={`log-entry log-${entry.type}`}>
                {entry.message}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

export default CopilotExport
