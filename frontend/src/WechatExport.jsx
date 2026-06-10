import { useState, useRef, useEffect } from 'react'
import useSSEStream from './useSSEStream'

function WechatExport({ token, onAuthError }) {
  const [dataDir, setDataDir] = useState('auto')
  const [contacts, setContacts] = useState([])
  const [selectedContacts, setSelectedContacts] = useState([])
  const [searchQuery, setSearchQuery] = useState('')
  const [connected, setConnected] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportResult, setExportResult] = useState(null)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [exportFormat, setExportFormat] = useState('html')
  const { progressLog, logContainerRef, addLog, loading, streamSSE, readSSEStream, setLoading } = useSSEStream()

  const authHeaders = () => token ? { Authorization: `Bearer ${token}` } : {}

  const handleConnect = async () => {
    setContacts([])
    setConnected(false)
    setSelectedContacts([])
    setExportResult(null)

    await streamSSE(
      () => fetch('/api/wechat/contacts/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ data_dir: dataDir }),
      }),
      {
        onAuthError,
        onEvent: (event) => {
          if (event.type === 'done') {
            const data = event.data || {}
            setContacts(data.contacts || [])
            setConnected(true)
            addLog({ type: 'done', message: `✓ 连接成功！找到 ${(data.contacts || []).length} 个联系人/群聊` })
          } else if (event.type === 'error') {
            addLog({ type: 'error', message: event.message })
          } else {
            addLog({ type: 'status', message: event.message })
          }
        },
      }
    )
  }

  const handleExport = async () => {
    if (selectedContacts.length === 0) return
    setExporting(true)
    setExportResult(null)

    for (const contact of selectedContacts) {
      addLog({ type: 'status', message: `正在导出: ${contact.name}...` })

      try {
        const res = await fetch('/api/wechat/export/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({
            data_dir: dataDir,
            contact_id: contact.id,
            contact_name: contact.name,
            start_date: startDate || '',
            end_date: endDate || '',
            format: exportFormat,
          }),
        })

        if (!res.ok) {
          if (res.status === 401 && onAuthError) { onAuthError(); return }
          const data = await res.json().catch(() => ({}))
          throw new Error(data.detail || `Server error (${res.status})`)
        }

        await readSSEStream(res, (event) => {
          if (event.type === 'done') {
            setExportResult(event)
            addLog({ type: 'done', message: `✓ ${contact.name}: 导出完成 (${event.count} 条消息)` })
            // Auto-download
            const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
            window.open(`/api/wechat/download/${event.job_id}${authParam}`, '_blank')
          } else if (event.type === 'error') {
            addLog({ type: 'error', message: `${contact.name}: ${event.message}` })
          } else {
            addLog({ type: 'status', message: event.message })
          }
        })
      } catch (err) {
        addLog({ type: 'error', message: `${contact.name}: ${err.message}` })
      }
    }

    setExporting(false)
  }

  const toggleContact = (contact) => {
    setSelectedContacts((prev) => {
      const exists = prev.find((c) => c.id === contact.id)
      if (exists) return prev.filter((c) => c.id !== contact.id)
      return [...prev, contact]
    })
  }

  const filteredContacts = contacts.filter((c) => {
    if (!searchQuery) return true
    const q = searchQuery.toLowerCase()
    return (
      (c.name && c.name.toLowerCase().includes(q)) ||
      (c.nickname && c.nickname.toLowerCase().includes(q)) ||
      (c.remark && c.remark.toLowerCase().includes(q)) ||
      (c.id && c.id.toLowerCase().includes(q))
    )
  })

  return (
    <>
      <h2 className="tool-page-title">💬 微信聊天记录导出</h2>

      <div className="input-section">
        <p className="tool-description">
          从本地微信中提取聊天记录并导出为 HTML 文件（含图片）。需要微信正在运行并已登录。
        </p>

        <div className="url-row">
          <input
            type="text"
            placeholder="微信数据目录路径（留空或输入 auto 自动检测）"
            value={dataDir}
            onChange={(e) => setDataDir(e.target.value || 'auto')}
            disabled={loading || exporting}
          />
          <button onClick={handleConnect} disabled={loading || exporting}>
            {loading ? '连接中...' : '连接微信'}
          </button>
        </div>
      </div>

      {connected && contacts.length > 0 && (
        <div className="input-section" style={{ marginTop: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <input
              type="text"
              placeholder="搜索联系人/群聊..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{ flex: 1 }}
            />
            <span style={{ fontSize: '0.85rem', color: '#888', whiteSpace: 'nowrap' }}>
              已选 {selectedContacts.length} 个
            </span>
          </div>

          <div style={{
            maxHeight: '300px',
            overflowY: 'auto',
            border: '1px solid var(--border-color, #ddd)',
            borderRadius: '8px',
            padding: '0.5rem',
          }}>
            {filteredContacts.map((contact) => {
              const isSelected = selectedContacts.some((c) => c.id === contact.id)
              return (
                <div
                  key={contact.id}
                  onClick={() => toggleContact(contact)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    padding: '0.5rem 0.75rem',
                    cursor: 'pointer',
                    borderRadius: '6px',
                    background: isSelected ? 'var(--accent-bg, #e8f0fe)' : 'transparent',
                    marginBottom: '2px',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleContact(contact)}
                    style={{ marginRight: '0.75rem' }}
                  />
                  <div>
                    <span style={{ fontWeight: 500 }}>{contact.name}</span>
                    {contact.remark && contact.nickname && contact.remark !== contact.nickname && (
                      <span style={{ marginLeft: '0.5rem', color: '#888', fontSize: '0.85rem' }}>
                        ({contact.nickname})
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
            {filteredContacts.length === 0 && (
              <div style={{ textAlign: 'center', padding: '1rem', color: '#888' }}>
                未找到匹配的联系人
              </div>
            )}
          </div>

          {selectedContacts.length > 0 && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              marginTop: '0.75rem',
              padding: '0.75rem 1rem',
              background: 'var(--accent-bg, #f0f4ff)',
              borderRadius: '8px',
              flexWrap: 'wrap',
            }}>
              <span style={{ fontSize: '0.9rem', fontWeight: 500, whiteSpace: 'nowrap' }}>时间范围（可选）</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flex: 1, flexWrap: 'wrap' }}>
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  max={endDate || undefined}
                  disabled={exporting}
                  style={{ flex: 1, minWidth: '130px' }}
                />
                <span style={{ color: '#888' }}>至</span>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  min={startDate || undefined}
                  disabled={exporting}
                  style={{ flex: 1, minWidth: '130px' }}
                />
                {(startDate || endDate) && (
                  <button
                    onClick={() => { setStartDate(''); setEndDate('') }}
                    disabled={exporting}
                    style={{ padding: '0.3rem 0.6rem', fontSize: '0.8rem', background: 'transparent', border: '1px solid var(--border-color, #ccc)', borderRadius: '6px', cursor: 'pointer' }}
                  >
                    清除
                  </button>
                )}
              </div>
            </div>
          )}

          <button
            onClick={handleExport}
            disabled={exporting || selectedContacts.length === 0}
            style={{ marginTop: '0.75rem', width: '100%' }}
          >
            {exporting ? '导出中...' : `导出选中的 ${selectedContacts.length} 个对话${startDate || endDate ? '（含日期筛选）' : ''}`}
          </button>
        </div>
      )}

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

export default WechatExport
