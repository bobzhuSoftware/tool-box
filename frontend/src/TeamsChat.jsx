import { useState } from 'react'
import useSSEStream from './useSSEStream'
import EdgeProfilePicker from './EdgeProfilePicker'

function TeamsChat({ token, onAuthError }) {
  const [chats, setChats] = useState([])
  const [selectedChats, setSelectedChats] = useState([])
  const [searchQuery, setSearchQuery] = useState('')
  const [connected, setConnected] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [exportFormat, setExportFormat] = useState('html')
  const { progressLog, logContainerRef, addLog, loading, streamSSE, readSSEStream } = useSSEStream()

  // Advanced settings: selector config & diagnose
  const [selectorsJson, setSelectorsJson] = useState('')
  const [selectorsJsonError, setSelectorsJsonError] = useState('')
  const [selectorsSaving, setSelectorsSaving] = useState(false)
  const [diagnosing, setDiagnosing] = useState(false)

  const authHeaders = () => token ? { Authorization: `Bearer ${token}` } : {}

  // 时间范围：日期 + 小时 + 15 分档。内部存储 'YYYY-MM-DDTHH:mm'（mm ∈ 00/15/30/45）
  const HOURS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, '0'))
  const MINUTES = ['00', '15', '30', '45']
  const parseParts = (v) => v
    ? { date: v.slice(0, 10), hour: v.slice(11, 13) || '00', minute: v.slice(14, 16) || '00' }
    : { date: '', hour: '00', minute: '00' }
  const buildValue = (date, hour, minute) => date ? `${date}T${hour}:${minute}` : ''
  const sp = parseParts(startDate)
  const ep = parseParts(endDate)
  const sameDay = sp.date && ep.date && sp.date === ep.date
  const setStart = (parts) => {
    let v = buildValue(parts.date, parts.hour, parts.minute)
    if (v && endDate && v > endDate) v = endDate  // 起始不得晚于结束
    setStartDate(v)
  }
  const setEnd = (parts) => {
    let v = buildValue(parts.date, parts.hour, parts.minute)
    if (v && startDate && v < startDate) v = startDate  // 结束不得早于起始
    setEndDate(v)
  }
  const ctrlStyle = {
    height: '34px',
    padding: '0 0.5rem',
    borderRadius: '7px',
    border: '1px solid var(--border-color, #d5dae2)',
    background: 'var(--input-bg, #fff)',
    color: 'inherit',
    fontSize: '0.85rem',
    boxSizing: 'border-box',
  }

  const handleConnect = async () => {
    setChats([])
    setConnected(false)
    setSelectedChats([])

    await streamSSE(
      () => fetch('/api/teams-chat/list/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({}),
      }),
      {
        onAuthError,
        onEvent: (event) => {
          if (event.type === 'done') {
            const data = event.data || {}
            setChats(data.chats || [])
            setConnected(true)
            addLog({ type: 'done', message: `✓ 已连接 Teams！找到 ${(data.chats || []).length} 个聊天` })
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
    if (selectedChats.length === 0) return
    setExporting(true)

    for (const chat of selectedChats) {
      addLog({ type: 'status', message: `正在导出: ${chat.name}...` })

      try {
        const res = await fetch('/api/teams-chat/export/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({
            chat_id: chat.id,
            chat_name: chat.name,
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
            addLog({ type: 'done', message: `✓ ${chat.name}: 导出完成 (${event.count} 条消息)` })
            const authParam = token ? `?token=${encodeURIComponent(token)}` : ''
            window.open(`/api/teams-chat/download/${event.job_id}${authParam}`, '_blank')
          } else if (event.type === 'error') {
            addLog({ type: 'error', message: `${chat.name}: ${event.message}` })
          } else {
            addLog({ type: 'status', message: event.message })
          }
        })
      } catch (err) {
        addLog({ type: 'error', message: `${chat.name}: ${err.message}` })
      }
    }

    setExporting(false)
  }

  const toggleChat = (chat) => {
    setSelectedChats((prev) => {
      const exists = prev.find((c) => c.id === chat.id)
      if (exists) return prev.filter((c) => c.id !== chat.id)
      return [...prev, chat]
    })
  }

  const filteredChats = chats.filter((c) => {
    if (!searchQuery) return true
    const q = searchQuery.toLowerCase()
    return (c.name && c.name.toLowerCase().includes(q)) || (c.id && c.id.toLowerCase().includes(q))
  })

  // --- Advanced: selector config ---
  const handleLoadSelectors = async () => {
    try {
      const res = await fetch('/api/teams-chat/selectors', { headers: authHeaders() })
      if (res.ok) setSelectorsJson(JSON.stringify(await res.json(), null, 2))
    } catch (_) { /* ignore */ }
  }

  const handleSelectorsChange = (val) => {
    setSelectorsJson(val)
    try { JSON.parse(val); setSelectorsJsonError('') }
    catch (e) { setSelectorsJsonError(`JSON 格式错误: ${e.message}`) }
  }

  const handleSaveSelectors = async () => {
    try {
      const data = JSON.parse(selectorsJson)
      setSelectorsSaving(true)
      const res = await fetch('/api/teams-chat/selectors', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(data),
      })
      if (!res.ok) {
        if (res.status === 401 && onAuthError) { onAuthError(); return }
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || '保存失败')
      }
      addLog({ type: 'done', message: '✓ 选择器配置已保存，重新连接 Teams 即生效' })
    } catch (e) { addLog({ type: 'error', message: `保存失败: ${e.message}` }) }
    finally { setSelectorsSaving(false) }
  }

  const handleDiagnose = async () => {
    setDiagnosing(true)
    addLog({ type: 'status', message: '🔍 正在运行选择器诊断，请稍候（需启动浏览器，约 15-30 秒）…' })
    try {
      const res = await fetch('/api/teams-chat/diagnose/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
      })
      if (!res.ok) {
        if (res.status === 401 && onAuthError) { onAuthError(); return }
        throw new Error(`Server error (${res.status})`)
      }
      await readSSEStream(res, (event) => {
        if (event.type === 'done') {
          const ok = event.data?.diagnose === 'ok'
          addLog({ type: ok ? 'done' : 'error', message: ok ? '✅ 所有选择器正常' : '❌ 存在失效的选择器，请查看日志并更新配置' })
        } else if (event.type === 'error') {
          addLog({ type: 'error', message: event.message })
        } else {
          addLog({ type: 'status', message: event.message })
        }
      })
    } catch (e) { addLog({ type: 'error', message: `诊断失败: ${e.message}` }) }
    finally { setDiagnosing(false) }
  }

  return (
    <>
      <h2 className="tool-page-title">💼 Teams 聊天记录导出</h2>

      <div className="input-section">
        <p className="tool-description">
          通过你已登录的 Edge 浏览器会话访问 Teams 网页版，抓取并导出聊天记录为 HTML/TXT 文件。
          需要你已在 Edge 中登录 Teams。
        </p>
        <EdgeProfilePicker token={token} onAuthError={onAuthError} />

        {/* Advanced settings: selector config + diagnose */}
        <details
          style={{ marginTop: '0.75rem' }}
          onToggle={(e) => { if (e.target.open && !selectorsJson) handleLoadSelectors() }}
        >
          <summary style={{
            cursor: 'pointer', fontSize: '0.88rem', color: '#666',
            userSelect: 'none', padding: '0.3rem 0', listStyle: 'none',
            display: 'flex', alignItems: 'center', gap: '0.4rem',
          }}>
            <span>⚙️</span>
            <span>高级设置：选择器配置 &amp; 诊断</span>
          </summary>
          <div style={{
            marginTop: '0.6rem', padding: '0.75rem',
            background: 'var(--accent-bg, #f8f9fa)',
            borderRadius: '8px', border: '1px solid var(--border-color, #e0e0e0)',
          }}>
            <p style={{ fontSize: '0.82rem', color: '#555', margin: '0 0 0.5rem', lineHeight: 1.5 }}>
              <strong>teams_chat_selectors.json</strong> — Teams web UI 的 CSS 选择器配置。<br />
              Teams 更新 UI 后如功能失效，先点「🔍 运行诊断」找出失效的选择器，
              在下方 JSON 中更新对应值后点「💾 保存」，<strong>无需修改任何代码</strong>。
            </p>
            <textarea
              value={selectorsJson}
              onChange={(e) => handleSelectorsChange(e.target.value)}
              style={{
                width: '100%', height: '220px',
                fontFamily: 'monospace', fontSize: '0.78rem',
                padding: '0.5rem', borderRadius: '6px', resize: 'vertical',
                boxSizing: 'border-box',
                border: selectorsJsonError
                  ? '1px solid red'
                  : '1px solid var(--border-color, #ccc)',
              }}
              spellCheck={false}
              placeholder="（展开时自动加载...）"
            />
            {selectorsJsonError && (
              <div style={{ color: 'red', fontSize: '0.78rem', marginTop: '0.2rem' }}>
                {selectorsJsonError}
              </div>
            )}
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
              <button
                onClick={handleSaveSelectors}
                disabled={!!selectorsJsonError || selectorsSaving || !selectorsJson}
                style={{ flex: 1 }}
              >
                {selectorsSaving ? '保存中…' : '💾 保存配置'}
              </button>
              <button
                onClick={handleDiagnose}
                disabled={diagnosing}
                style={{ flex: 1 }}
              >
                {diagnosing ? '诊断中…' : '🔍 运行诊断'}
              </button>
            </div>
          </div>
        </details>

        <div className="url-row" style={{ marginTop: '0.75rem' }}>
          <button onClick={handleConnect} disabled={loading || exporting}>
            {loading ? '连接中...' : '连接 Teams'}
          </button>
        </div>
      </div>

      {connected && chats.length > 0 && (
        <div className="input-section" style={{ marginTop: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <input
              type="text"
              placeholder="搜索聊天..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{ flex: 1 }}
            />
            <span style={{ fontSize: '0.85rem', color: '#888', whiteSpace: 'nowrap' }}>
              已选 {selectedChats.length} 个
            </span>
          </div>

          <div style={{
            maxHeight: '300px',
            overflowY: 'auto',
            border: '1px solid var(--border-color, #ddd)',
            borderRadius: '8px',
            padding: '0.5rem',
          }}>
            {filteredChats.map((chat) => {
              const isSelected = selectedChats.some((c) => c.id === chat.id)
              return (
                <div
                  key={chat.id}
                  onClick={() => toggleChat(chat)}
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
                    onChange={() => toggleChat(chat)}
                    style={{ marginRight: '0.75rem' }}
                  />
                  <span style={{ fontWeight: 500 }}>{chat.name}</span>
                </div>
              )
            })}
            {filteredChats.length === 0 && (
              <div style={{ textAlign: 'center', padding: '1rem', color: '#888' }}>
                未找到匹配的聊天
              </div>
            )}
          </div>

          {selectedChats.length > 0 && (
            <div style={{
              marginTop: '0.75rem',
              padding: '0.85rem 1rem',
              background: 'var(--accent-bg, #f0f4ff)',
              borderRadius: '10px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem', marginBottom: '0.7rem', flexWrap: 'wrap' }}>
                <span style={{ fontSize: '0.92rem', fontWeight: 600, display: 'flex', alignItems: 'baseline', gap: '0.3rem' }}>
                  时间范围
                  <span style={{ fontWeight: 400, color: '#8a8f98', fontSize: '0.78rem' }}>可选</span>
                </span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <span style={{ fontSize: '0.82rem', color: '#666' }}>导出格式</span>
                  <select
                    value={exportFormat}
                    onChange={(e) => setExportFormat(e.target.value)}
                    disabled={exporting}
                    style={{ ...ctrlStyle, height: '30px' }}
                  >
                    <option value="html">HTML</option>
                    <option value="txt">TXT</option>
                  </select>
                </div>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {[
                  { key: 'start', label: '起始', parts: sp, setter: setStart, dateProps: { max: ep.date || undefined },
                    hourDisabled: (h) => sameDay && Number(h) > Number(ep.hour),
                    minDisabled: (m) => sameDay && sp.hour === ep.hour && Number(m) > Number(ep.minute) },
                  { key: 'end', label: '结束', parts: ep, setter: setEnd, dateProps: { min: sp.date || undefined },
                    hourDisabled: (h) => sameDay && Number(h) < Number(sp.hour),
                    minDisabled: (m) => sameDay && ep.hour === sp.hour && Number(m) < Number(sp.minute) },
                ].map(({ key, label, parts, setter, dateProps, hourDisabled, minDisabled }) => (
                  <div key={key} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '0.82rem', color: '#8a8f98', width: '2.6em', flexShrink: 0 }}>{label}</span>
                    <input
                      type="date"
                      value={parts.date}
                      onChange={(e) => setter({ ...parts, date: e.target.value })}
                      {...dateProps}
                      disabled={exporting}
                      style={{ ...ctrlStyle, flex: 1, minWidth: '150px' }}
                    />
                    <select
                      value={parts.hour}
                      onChange={(e) => setter({ ...parts, hour: e.target.value })}
                      disabled={exporting || !parts.date}
                      style={{ ...ctrlStyle, width: '4.5em' }}
                    >
                      {HOURS.map((h) => (
                        <option key={h} value={h} disabled={hourDisabled(h)}>{h}时</option>
                      ))}
                    </select>
                    <select
                      value={parts.minute}
                      onChange={(e) => setter({ ...parts, minute: e.target.value })}
                      disabled={exporting || !parts.date}
                      style={{ ...ctrlStyle, width: '4.5em' }}
                    >
                      {MINUTES.map((m) => (
                        <option key={m} value={m} disabled={minDisabled(m)}>{m}分</option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>

              {(startDate || endDate) && (
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '0.5rem' }}>
                  <button
                    onClick={() => { setStartDate(''); setEndDate('') }}
                    disabled={exporting}
                    style={{ padding: '0.25rem 0.7rem', fontSize: '0.8rem', color: '#666', background: 'transparent', border: '1px solid var(--border-color, #d5dae2)', borderRadius: '7px', cursor: 'pointer' }}
                  >
                    清除时间
                  </button>
                </div>
              )}
            </div>
          )}

          <button
            onClick={handleExport}
            disabled={exporting || selectedChats.length === 0}
            style={{ marginTop: '0.75rem', width: '100%' }}
          >
            {exporting ? '导出中...' : `导出选中的 ${selectedChats.length} 个聊天${startDate || endDate ? '（含时间范围）' : ''}`}
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

export default TeamsChat
