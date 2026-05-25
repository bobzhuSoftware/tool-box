import { useState, useRef, useEffect } from 'react'

function DiscordExport({ token, onAuthError }) {
  const [discordToken, setDiscordToken] = useState('')
  const [channelUrl, setChannelUrl] = useState('')
  const [limit, setLimit] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [loading, setLoading] = useState(false)
  const [progressLog, setProgressLog] = useState([])
  const [result, setResult] = useState(null)
  const [showHelp, setShowHelp] = useState(false)
  const [tokenSaved, setTokenSaved] = useState(false)
  const logContainerRef = useRef(null)

  // Load saved token from DB on mount
  useEffect(() => {
    if (!token) return
    fetch('/api/discord/token', {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.token) {
          setDiscordToken(data.token)
          setTokenSaved(true)
        }
      })
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = logContainerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [progressLog])

  const addLog = (entry) => setProgressLog((prev) => [...prev, entry])

  const handleExport = async () => {
    if (!discordToken.trim() || !channelUrl.trim()) return
    setLoading(true)
    setProgressLog([])
    setResult(null)
    // Persist token to DB
    fetch('/api/discord/token', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ token: discordToken.trim() }),
    }).then(() => setTokenSaved(true)).catch(() => {})

    try {
      const res = await fetch('/api/discord/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          token: discordToken.trim(),
          channel_url: channelUrl.trim(),
          limit: limit ? parseInt(limit, 10) : null,
          start_date: startDate || null,
          end_date: endDate || null,
        }),
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
                  setResult(event)
                  addLog({ type: 'done', message: `✓ 导出完成！共 ${event.message_count} 条消息` })
                } else if (event.type === 'error') {
                  addLog({ type: 'error', message: event.message })
                } else {
                  addLog({ type: 'status', message: event.message })
                }
              } catch { /* ignore */ }
            }
          }
        }
      }
    } catch (err) {
      addLog({ type: 'error', message: err.message || '导出失败' })
    } finally {
      setLoading(false)
    }
  }

  const handleSaveToken = () => {
    if (!discordToken.trim()) return
    fetch('/api/discord/token', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ token: discordToken.trim() }),
    }).then(() => setTokenSaved(true)).catch(() => {})
  }

  const handleDownload = () => {
    if (!result?.job_id) return
    const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
    window.open(`/api/discord/download/${result.job_id}${authParam}`, '_blank')
  }

  return (
    <>
      <h2 className="tool-page-title">🎮 Discord 聊天记录导出</h2>

      <div className="input-section">
        <p className="tool-description">
          导出 Discord 服务器频道的聊天记录为 HTML 文件。需要你的 Discord Token 和频道 URL（从浏览器地址栏复制）。
        </p>

        {/* Token field */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' }}>
            <label style={{ fontSize: '0.85rem', color: '#555', fontWeight: 600 }}>Discord Token</label>
            <button
              type="button"
              onClick={() => setShowHelp(!showHelp)}
              style={{ fontSize: '0.75rem', cursor: 'pointer', background: 'none', border: '1.5px solid #c0c0d0', borderRadius: 4, padding: '2px 8px', color: '#888', fontWeight: 600 }}
            >
              {showHelp ? '收起' : '如何获取?'}
            </button>
          </div>
          {showHelp && (
            <div style={{ background: '#f8f8ff', border: '1.5px solid #e0e0f0', borderRadius: 8, padding: '0.85rem 1rem', marginBottom: '0.75rem', fontSize: '0.83rem', lineHeight: 1.7, color: '#444' }}>
              <strong>获取 Discord Token：</strong>
              <ol style={{ paddingLeft: '1.2rem', margin: '0.4rem 0 0.5rem' }}>
                <li>在浏览器打开 <strong>discord.com</strong> 并登录</li>
                <li>进入你想导出的频道，按 <kbd style={{ background: '#eee', border: '1px solid #ccc', borderRadius: 3, padding: '0 4px' }}>F12</kbd> 打开 DevTools</li>
                <li>切换到 <strong>Network</strong> 标签页，在 Discord 中点击任意频道</li>
                <li>点击 Network 列表中的任一请求 → <strong>Headers</strong> → 找到 <code style={{ background: '#eff0ff', padding: '0 3px', borderRadius: 3 }}>Authorization</code> 并复制</li>
              </ol>
              <span style={{ color: '#b45309' }}>⚠ Token 仅发送到本地服务器，不会被存储或转发给任何第三方。</span>
            </div>
          )}
          <input
            type="password"
            placeholder="粘贴你的 Discord Token（格式：MTxx...）"
            value={discordToken}
            onChange={(e) => { setDiscordToken(e.target.value); setTokenSaved(false) }}
            disabled={loading}
            autoComplete="off"
            style={{ width: '100%', padding: '0.7rem 1rem', border: '2px solid #e0e0e0', borderRadius: 8, fontSize: '0.95rem', transition: 'border-color 0.2s', boxSizing: 'border-box' }}
            onFocus={e => e.target.style.borderColor = '#646cff'}
            onBlur={e => e.target.style.borderColor = '#e0e0e0'}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.5rem' }}>
            <button
              type="button"
              onClick={handleSaveToken}
              disabled={!discordToken.trim() || tokenSaved}
              style={{ fontSize: '0.82rem', cursor: 'pointer', background: tokenSaved ? '#e8f5e9' : '#646cff', border: tokenSaved ? '1.5px solid #a5d6a7' : 'none', borderRadius: 6, padding: '4px 14px', color: tokenSaved ? '#2e7d32' : '#fff', fontWeight: 600, transition: 'all 0.2s', opacity: (!discordToken.trim() || tokenSaved) && !tokenSaved ? 0.5 : 1 }}
            >
              {tokenSaved ? '✓ Token 已保存' : '保存 Token'}
            </button>
            {tokenSaved && (
              <button
                type="button"
                onClick={() => {
                  fetch('/api/discord/token', {
                    method: 'DELETE',
                    headers: token ? { Authorization: `Bearer ${token}` } : {},
                  }).catch(() => {})
                  setDiscordToken('')
                  setTokenSaved(false)
                }}
                style={{ fontSize: '0.82rem', cursor: 'pointer', background: 'none', border: '1.5px solid #e0a0a0', borderRadius: 6, padding: '4px 14px', color: '#c62828', fontWeight: 600 }}
              >
                清除
              </button>
            )}
          </div>
        </div>

        {/* Channel URL field */}
        <div className="url-row">
          <input
            type="text"
            placeholder="https://discord.com/channels/服务器ID/频道ID（从地址栏复制）"
            value={channelUrl}
            onChange={(e) => { setChannelUrl(e.target.value); setResult(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !loading) handleExport() }}
            disabled={loading}
          />
        </div>

        {/* Options row */}
        <div className="options-row">
          <label>
            起始日期（可选）
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              disabled={loading}
            />
          </label>
          <label>
            截止日期（可选）
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              disabled={loading}
            />
          </label>
          <label>
            消息数量上限（可选）
            <input
              type="number"
              placeholder="留空 = 全部"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              disabled={loading}
              min="1"
              style={{ width: 120 }}
            />
          </label>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button
            className="btn-primary"
            onClick={handleExport}
            disabled={loading || !discordToken.trim() || !channelUrl.trim()}
            style={{ padding: '0.7rem 1.8rem', borderRadius: 8, fontSize: '0.95rem', fontWeight: 600, cursor: 'pointer', border: 'none', opacity: (loading || !discordToken.trim() || !channelUrl.trim()) ? 0.6 : 1 }}
          >
            {loading ? '正在导出…' : '开始导出'}
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
            <span className="pdf-result-icon">🎮</span>
            <div>
              <div className="pdf-result-title">导出完成 · {result.message_count} 条消息</div>
              <div className="pdf-result-url">{channelUrl}</div>
            </div>
          </div>
          <button className="pdf-download-btn btn-primary" onClick={handleDownload}>
            下载 HTML 文件
          </button>
        </div>
      )}
    </>
  )
}

export default DiscordExport
