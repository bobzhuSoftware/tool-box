import { useEffect, useMemo, useState } from 'react'
import useSSEStream from './useSSEStream'
import './ServiceNowTables.css'

const COUNTS_KEY = 'sn_counts'

const loadCounts = () => {
  try {
    return JSON.parse(localStorage.getItem(COUNTS_KEY)) || { values: {}, at: null }
  } catch {
    return { values: {}, at: null }
  }
}

const cellKey = (r, c) => `${r},${c}`

// Re-key cell URLs after removing a row (axis 'r') or column (axis 'c').
const removeIndex = (cells, axis, idx) => {
  const out = {}
  for (const [k, url] of Object.entries(cells)) {
    const [r, c] = k.split(',').map(Number)
    const n = axis === 'r' ? r : c
    if (n === idx) continue
    const nr = axis === 'r' && r > idx ? r - 1 : r
    const nc = axis === 'c' && c > idx ? c - 1 : c
    out[cellKey(nr, nc)] = url
  }
  return out
}

function ServiceNowTables({ token, onAuthError }) {
  const [tables, setTables] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [editMode, setEditMode] = useState(false)
  const [counts, setCounts] = useState(loadCounts)
  const [pending, setPending] = useState(new Set())
  const [loadError, setLoadError] = useState('')
  const [notice, setNotice] = useState('')
  const { progressLog, logContainerRef, addLog, loading, streamSSE } = useSSEStream()

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  useEffect(() => {
    fetch('/api/servicenow/config', { headers: authHeaders() })
      .then(async (res) => {
        if (res.status === 401) { onAuthError?.(); return }
        if (!res.ok) throw new Error(`加载失败 (${res.status})`)
        const data = await res.json()
        setTables(data.tables)
        setActiveId(data.tables[0]?.id ?? null)
      })
      .catch((e) => setLoadError(e.message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const table = useMemo(() => tables.find((t) => t.id === activeId) || null, [tables, activeId])

  const updateTable = (patch) =>
    setTables((prev) => prev.map((t) => (t.id === activeId ? { ...t, ...patch } : t)))

  const saveConfig = async (next = tables) => {
    const res = await fetch('/api/servicenow/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ tables: next }),
    })
    if (res.status === 401) { onAuthError?.(); return }
    if (!res.ok) throw new Error(`保存失败 (${res.status})`)
  }

  const toggleEdit = async () => {
    if (editMode) {
      try {
        await saveConfig()
        setNotice('已保存')
      } catch (e) {
        setNotice(e.message)
        return
      }
    }
    setEditMode(!editMode)
  }

  const addTable = () => {
    const id = `t${Date.now().toString(36)}`
    const t = { id, title: '新表格', rows: ['Row 1'], cols: ['Col 1'], cells: {} }
    setTables((prev) => [...prev, t])
    setActiveId(id)
    setEditMode(true)
  }

  const deleteTable = async () => {
    if (!table || !window.confirm(`删除表格「${table.title}」？`)) return
    const next = tables.filter((t) => t.id !== table.id)
    setTables(next)
    setActiveId(next[0]?.id ?? null)
    try { await saveConfig(next) } catch (e) { setNotice(e.message) }
  }

  const setHeader = (axis, idx, value) => {
    const key = axis === 'r' ? 'rows' : 'cols'
    updateTable({ [key]: table[key].map((v, i) => (i === idx ? value : v)) })
  }

  const addAxis = (axis) => {
    const key = axis === 'r' ? 'rows' : 'cols'
    updateTable({ [key]: [...table[key], `${axis === 'r' ? 'Row' : 'Col'} ${table[key].length + 1}`] })
  }

  const removeAxis = (axis, idx) => {
    const key = axis === 'r' ? 'rows' : 'cols'
    updateTable({
      [key]: table[key].filter((_, i) => i !== idx),
      cells: removeIndex(table.cells, axis, idx),
    })
  }

  const setCellUrl = (r, c, url) => {
    const cells = { ...table.cells }
    if (url.trim()) cells[cellKey(r, c)] = url
    else delete cells[cellKey(r, c)]
    updateTable({ cells })
  }

  const refresh = async () => {
    if (!table) return
    const urls = [...new Set(Object.values(table.cells).map((u) => u.trim()).filter(Boolean))]
    if (!urls.length) { setNotice('这个表格还没有配置任何单元格链接'); return }
    setNotice('')
    setPending(new Set(urls))

    await streamSSE(
      () =>
        fetch('/api/servicenow/run/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ urls }),
        }),
      {
        onAuthError,
        onEvent: (event) => {
          if (event.type === 'result') {
            const { url, count, error } = event.data
            setPending((prev) => { const n = new Set(prev); n.delete(url); return n })
            setCounts((prev) => {
              const next = {
                values: { ...prev.values, [url]: error ? { error } : { count } },
                at: new Date().toISOString(),
              }
              localStorage.setItem(COUNTS_KEY, JSON.stringify(next))
              return next
            })
          } else if (event.type === 'done') {
            addLog({ type: 'done', message: '✓ 读取完成' })
          } else if (event.type === 'error') {
            addLog({ type: 'error', message: event.message })
          } else {
            addLog({ type: 'status', message: event.message })
          }
        },
      }
    )
    setPending(new Set())
  }

  const cellText = (url) => {
    if (!url) return ''
    if (pending.has(url)) return '…'
    const v = counts.values[url]
    if (!v) return '—'
    return v.error ? '✗' : String(v.count)
  }

  const copyTable = async () => {
    if (!table) return
    const grid = [
      [table.title, ...table.cols],
      ...table.rows.map((row, r) => [row, ...table.cols.map((_, c) => cellText(table.cells[cellKey(r, c)]))]),
    ].map((line) => line.map((v) => (v === '…' || v === '—' || v === '✗' ? '' : v)))

    const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
    const html =
      '<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse">' +
      grid
        .map((line, i) =>
          '<tr>' + line.map((v, j) => (i === 0 || j === 0 ? `<th>${esc(v)}</th>` : `<td>${esc(v)}</td>`)).join('') + '</tr>'
        )
        .join('') +
      '</table>'
    const text = grid.map((line) => line.join('\t')).join('\n')

    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' }),
        }),
      ])
      setNotice('已复制，可直接粘贴到 Excel / Outlook / Teams')
    } catch {
      try {
        await navigator.clipboard.writeText(text)
        setNotice('已复制为文本（制表符分隔）')
      } catch {
        setNotice('复制失败，请检查浏览器剪贴板权限')
      }
    }
  }

  if (loadError) return <p className="auth-error">{loadError}</p>

  return (
    <>
      <h2 className="tool-page-title">📊 ServiceNow 数据表</h2>

      <div className="input-section">
        <p className="tool-description">
          每个单元格对应一个 ServiceNow 列表链接，刷新时读取该列表的记录数。首次使用会弹出 Edge 窗口，请用 DSV 账号登录（含 2FA），之后会自动记住。
        </p>

        <div className="sn-tabs">
          {tables.map((t) => (
            <button
              key={t.id}
              className={`sn-tab ${t.id === activeId ? 'active' : ''}`}
              onClick={() => setActiveId(t.id)}
              disabled={loading}
            >
              {t.title || '(未命名)'}
            </button>
          ))}
          <button className="sn-tab" onClick={addTable} disabled={loading}>＋ 新表格</button>
        </div>

        {table && (
          <>
            <div className="sn-toolbar">
              <button className="btn-primary sn-btn" onClick={refresh} disabled={loading || editMode}>
                {loading ? '正在读取…' : '刷新数据'}
              </button>
              <button className="btn-outline btn-sm" onClick={copyTable} disabled={loading}>复制表格</button>
              <button className="btn-outline btn-sm" onClick={toggleEdit} disabled={loading}>
                {editMode ? '完成编辑并保存' : '编辑表格'}
              </button>
              {editMode && (
                <button className="btn-outline btn-sm sn-danger" onClick={deleteTable}>删除此表格</button>
              )}
              {counts.at && !editMode && (
                <span className="sn-updated">更新于 {new Date(counts.at).toLocaleString()}</span>
              )}
            </div>

            {editMode && (
              <div className="sn-edit-title">
                表格标题：
                <input value={table.title} onChange={(e) => updateTable({ title: e.target.value })} />
              </div>
            )}

            <div className="sn-table-wrap">
              <table className="sn-table">
                <thead>
                  <tr>
                    <th>{table.title}</th>
                    {table.cols.map((col, c) => (
                      <th key={c}>
                        {editMode ? (
                          <div className="sn-head-edit">
                            <input value={col} onChange={(e) => setHeader('c', c, e.target.value)} />
                            <button title="删除此列" onClick={() => removeAxis('c', c)}>✕</button>
                          </div>
                        ) : col}
                      </th>
                    ))}
                    {editMode && <th><button className="sn-add" onClick={() => addAxis('c')}>＋ 列</button></th>}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, r) => (
                    <tr key={r}>
                      <th>
                        {editMode ? (
                          <div className="sn-head-edit">
                            <input value={row} onChange={(e) => setHeader('r', r, e.target.value)} />
                            <button title="删除此行" onClick={() => removeAxis('r', r)}>✕</button>
                          </div>
                        ) : row}
                      </th>
                      {table.cols.map((_, c) => {
                        const url = table.cells[cellKey(r, c)] || ''
                        const v = counts.values[url]
                        return (
                          <td key={c} className={v?.error && !pending.has(url) ? 'sn-cell-error' : ''}
                              title={v?.error || ''}>
                            {editMode ? (
                              <input
                                className="sn-url-input"
                                placeholder="ServiceNow 列表链接"
                                value={url}
                                onChange={(e) => setCellUrl(r, c, e.target.value)}
                              />
                            ) : cellText(url)}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                  {editMode && (
                    <tr>
                      <th><button className="sn-add" onClick={() => addAxis('r')}>＋ 行</button></th>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}

        {notice && <p className="sn-notice">{notice}</p>}
      </div>

      {progressLog.length > 0 && (
        <div className="progress-section">
          <div className="progress-log" ref={logContainerRef}>
            {progressLog.map((entry, i) => (
              <div key={i} className={`log-entry log-${entry.type}`}>{entry.message}</div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

export default ServiceNowTables
