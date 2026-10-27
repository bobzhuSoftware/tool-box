import { useEffect, useMemo, useRef, useState } from 'react'
import useSSEStream from './useSSEStream'
import './ServiceNowTables.css'

const emptyCounts = { values: {}, at: null }

// Numbers differ per login identity, so each identity keeps its own cache.
const countsKey = (accId) => `sn_counts:${accId}`

const loadCounts = (accId) => {
  try {
    return JSON.parse(localStorage.getItem(countsKey(accId))) || emptyCounts
  } catch {
    return emptyCounts
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

// Mirrors the server rule (https://*.service-now.com) plus the simplelist form the worker can parse.
const urlProblem = (url) => {
  const u = url.trim()
  if (!u) return ''
  let parsed
  try {
    parsed = new URL(u)
  } catch {
    return '不是有效的链接'
  }
  const host = parsed.hostname.toLowerCase()
  if (parsed.protocol !== 'https:' || !(host === 'service-now.com' || host.endsWith('.service-now.com'))) {
    return '只支持 https://*.service-now.com 的链接'
  }
  if (!parsed.pathname.includes('/simplelist/')) return '不是 simplelist 列表链接'
  return ''
}

const shortError = (s) => (s.length > 16 ? `${s.slice(0, 16)}…` : s)

const formatUpdated = (iso) => {
  const d = new Date(iso)
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString() : d.toLocaleString()
}

function ActionMenu({ title, items, disabled, align = 'right' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  useEffect(() => { if (disabled) setOpen(false) }, [disabled])

  return (
    <div className="sn-menu-wrap" ref={ref}>
      <button
        className="btn-outline btn-sm sn-menu-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={title}
        title={title}
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
      >
        ⋯
      </button>
      {open && (
        <div className={`sn-menu sn-menu-${align}`} role="menu">
          {items.map((it, i) =>
            it.sep ? (
              <div key={i} className="sn-menu-sep" />
            ) : (
              <button
                key={i}
                role="menuitem"
                className={`sn-menu-item ${it.danger ? 'sn-danger' : ''}`}
                disabled={it.disabled}
                onClick={() => { setOpen(false); it.onClick() }}
              >
                {it.label}
              </button>
            )
          )}
        </div>
      )}
    </div>
  )
}

function ServiceNowTables({ token, onAuthError }) {
  const [tables, setTables] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [editing, setEditing] = useState(false)
  const [accounts, setAccounts] = useState([])
  const [accId, setAccId] = useState('')
  const [counts, setCounts] = useState(emptyCounts)
  const [pending, setPending] = useState(new Set())
  const [loadError, setLoadError] = useState('')
  const [notice, setNotice] = useState('')
  // Which action owns the shared SSE `loading` flag, so the UI can label it correctly.
  const [busy, setBusy] = useState(null) // 'login' | 'check' | 'refresh' | null
  const [stage, setStage] = useState(null) // worker stage: 'check' | 'window' | 'working'
  const [liveStatus, setLiveStatus] = useState('')
  const [loginResult, setLoginResult] = useState(null) // { ok, text } shown after login/check
  const [saveState, setSaveState] = useState('idle') // 'idle' | 'pending' | 'saving' | 'saved' | 'error'
  const { progressLog, logContainerRef, addLog, loading, streamSSE } = useSSEStream()

  const saver = useRef({ timer: null, latest: null, running: false })
  const tokenRef = useRef(token)
  tokenRef.current = token

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})
  const jsonHeaders = () => ({ 'Content-Type': 'application/json', ...authHeaders() })

  const applyAccounts = (data) => {
    setAccounts(data.accounts)
    setAccId(data.selected)
    setCounts(loadCounts(data.selected))
    setLoginResult(null)
  }

  // Shared request helper for the account endpoints; returns the new account list.
  const accountRequest = async (path, method, body) => {
    const res = await fetch(`/api/servicenow/accounts${path}`, {
      method,
      headers: jsonHeaders(),
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.status === 401) { onAuthError?.(); return null }
    const data = await res.json().catch(() => ({}))
    if (!res.ok) { setNotice(data.detail || `请求失败 (${res.status})`); return null }
    applyAccounts(data)
    return data
  }

  useEffect(() => {
    Promise.all([
      fetch('/api/servicenow/config', { headers: authHeaders() }),
      fetch('/api/servicenow/accounts', { headers: authHeaders() }),
    ])
      .then(async ([cfgRes, accRes]) => {
        if (cfgRes.status === 401 || accRes.status === 401) { onAuthError?.(); return }
        if (!cfgRes.ok || !accRes.ok) throw new Error('加载失败')
        const cfg = await cfgRes.json()
        setTables(cfg.tables)
        setActiveId(cfg.tables[0]?.id ?? null)
        applyAccounts(await accRes.json())
      })
      .catch((e) => setLoadError(e.message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- autosave (debounced, one request at a time, always the latest layout) ----
  const saveConfig = async (next) => {
    const res = await fetch('/api/servicenow/config', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ tables: next }),
    })
    if (res.status === 401) { onAuthError?.(); throw new Error('未登录') }
    if (!res.ok) throw new Error(`保存失败 (${res.status})`)
  }

  const flushSave = async () => {
    const s = saver.current
    clearTimeout(s.timer)
    if (s.running) return
    s.running = true
    try {
      while (s.latest) {
        const next = s.latest
        s.latest = null
        setSaveState('saving')
        try {
          await saveConfig(next)
        } catch {
          s.latest = s.latest || next
          setSaveState('error')
          return
        }
      }
      setSaveState('saved')
    } finally {
      s.running = false
    }
  }

  const applyTables = (next) => {
    const s = saver.current
    setTables(next)
    s.latest = next
    setSaveState('pending')
    clearTimeout(s.timer)
    s.timer = setTimeout(flushSave, 600)
  }

  useEffect(() => {
    if (saveState !== 'saved') return undefined
    const t = setTimeout(() => setSaveState('idle'), 2500)
    return () => clearTimeout(t)
  }, [saveState])

  // Leaving the page with an unsent change: send it best-effort and warn on tab close.
  useEffect(() => () => {
    const s = saver.current
    clearTimeout(s.timer)
    if (!s.latest) return
    const headers = { 'Content-Type': 'application/json' }
    if (tokenRef.current) headers.Authorization = `Bearer ${tokenRef.current}`
    fetch('/api/servicenow/config', {
      method: 'POST', headers, body: JSON.stringify({ tables: s.latest }), keepalive: true,
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (saveState === 'idle' || saveState === 'saved') return undefined
    const warn = (e) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [saveState])

  // Success banners are transient; failures stay until the next action.
  useEffect(() => {
    if (!loginResult?.ok) return undefined
    const timer = setTimeout(() => setLoginResult(null), 8000)
    return () => clearTimeout(timer)
  }, [loginResult])

  useEffect(() => {
    if (!notice) return undefined
    const timer = setTimeout(() => setNotice(''), 5000)
    return () => clearTimeout(timer)
  }, [notice])

  const table = useMemo(() => tables.find((t) => t.id === activeId) || null, [tables, activeId])
  const account = useMemo(() => accounts.find((a) => a.id === accId) || null, [accounts, accId])

  // ---- table layout editing ----
  const updateTable = (patch) => {
    applyTables(tables.map((t) => (t.id === activeId ? { ...t, ...patch } : t)))
  }

  const switchTable = (id) => {
    if (id === activeId) return
    setEditing(false)
    setActiveId(id)
  }

  const addTable = () => {
    const titles = new Set(tables.map((t) => t.title))
    let n = 1
    while (titles.has(n === 1 ? '新表格' : `新表格 ${n}`)) n += 1
    const name = window.prompt('新表格的名称', n === 1 ? '新表格' : `新表格 ${n}`)
    if (name === null) return
    const id = `t${Date.now().toString(36)}`
    const t = { id, title: name.trim() || '新表格', rows: ['行 1', '行 2'], cols: ['列 1', '列 2'], cells: {} }
    applyTables([...tables, t])
    setActiveId(id)
    setEditing(true)
  }

  const renameTable = (id = activeId) => {
    const target = tables.find((t) => t.id === id)
    if (!target) return
    const name = window.prompt('表格名称', target.title)
    if (!name?.trim()) return
    applyTables(tables.map((t) => (t.id === id ? { ...t, title: name.trim() } : t)))
  }

  const duplicateTable = () => {
    if (!table) return
    const id = `t${Date.now().toString(36)}`
    const copy = {
      ...table,
      id,
      title: `${table.title} 副本`,
      rows: [...table.rows],
      cols: [...table.cols],
      cells: { ...table.cells },
    }
    applyTables([...tables, copy])
    setEditing(false)
    setActiveId(id)
  }

  const deleteTable = () => {
    if (!table || !window.confirm(`删除表格「${table.title}」？`)) return
    const next = tables.filter((t) => t.id !== table.id)
    applyTables(next)
    setEditing(false)
    setActiveId(next[0]?.id ?? null)
  }

  const setHeader = (axis, idx, value) => {
    const key = axis === 'r' ? 'rows' : 'cols'
    updateTable({ [key]: table[key].map((v, i) => (i === idx ? value : v)) })
  }

  const addAxis = (axis) => {
    const key = axis === 'r' ? 'rows' : 'cols'
    updateTable({ [key]: [...table[key], `${axis === 'r' ? '行' : '列'} ${table[key].length + 1}`] })
  }

  const removeAxis = (axis, idx) => {
    const key = axis === 'r' ? 'rows' : 'cols'
    const affected = Object.entries(table.cells).filter(([k, url]) => {
      const [r, c] = k.split(',').map(Number)
      return (axis === 'r' ? r : c) === idx && url.trim()
    }).length
    if (affected && !window.confirm(`删除「${table[key][idx]}」会同时删除其中 ${affected} 个链接，确认？`)) return
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

  // ---- login identities ----
  const selectAccount = (id) => accountRequest('/select', 'POST', { id })

  const addAccount = async () => {
    const name = window.prompt('新身份的名称（例如：同事A）')
    if (name?.trim()) await accountRequest('', 'POST', { name })
  }

  const renameAccount = async () => {
    const name = window.prompt('新的名称', account?.name || '')
    if (name?.trim()) await accountRequest(`/${accId}`, 'PUT', { name })
  }

  const deleteAccount = async () => {
    if (window.confirm(`删除身份「${account?.name}」及其登录状态？`)) await accountRequest(`/${accId}`, 'DELETE')
  }

  const clearLogin = async () => {
    if (window.confirm(`清除身份「${account?.name}」的登录状态？下次使用需要重新登录。`)) {
      await accountRequest(`/${accId}/logout`, 'POST')
    }
  }

  const onIdentity = (ident) =>
    setAccounts((prev) =>
      prev.map((a) => (a.id === accId ? { ...a, ...ident, logged_in: true } : a))
    )

  const login = async () => {
    const checking = !!account?.logged_in
    setNotice('')
    setLoginResult(null)
    setBusy(checking ? 'check' : 'login')
    setStage(null)
    setLiveStatus('正在准备…')
    let ident = null
    let failure = ''
    try {
      await streamSSE(
        () => fetch(`/api/servicenow/accounts/${accId}/login/stream`, { method: 'POST', headers: authHeaders() }),
        {
          onAuthError,
          onEvent: (event) => {
            if (event.type === 'identity') { ident = event.data; onIdentity(event.data) }
            else if (event.type === 'stage') setStage(event.stage)
            else if (event.type === 'done') addLog({ type: 'done', message: '✓ 登录完成' })
            else if (event.type === 'error') { failure = event.message; addLog({ type: 'error', message: event.message }) }
            else { addLog({ type: 'status', message: event.message }); setLiveStatus(event.message) }
          },
        }
      )
    } finally {
      setBusy(null)
      setStage(null)
      setLiveStatus('')
    }
    if (failure) setLoginResult({ ok: false, text: `登录失败：${failure}` })
    else if (ident) {
      const who = `${ident.display_name || ident.user_name}（${ident.user_name}）`
      setLoginResult({ ok: true, text: `${checking ? '登录状态正常' : '登录成功'}：${who}` })
    }
    // The session file may exist even if the identity read failed, so resync from the server.
    try {
      const res = await fetch('/api/servicenow/accounts', { headers: authHeaders() })
      if (res.ok) setAccounts((await res.json()).accounts)
    } catch { /* keep current state */ }
  }

  const tableUrls = table
    ? [...new Set(Object.values(table.cells).map((u) => u.trim()).filter(Boolean))]
    : []
  const failedUrls = tableUrls.filter((u) => !urlProblem(u) && counts.values[u]?.error)

  const refresh = async (onlyFailed = false) => {
    if (!table) return
    const valid = tableUrls.filter((u) => !urlProblem(u))
    const urls = onlyFailed ? failedUrls : valid
    if (!tableUrls.length) { setNotice('这个表格还没有配置任何单元格链接'); return }
    if (!urls.length) { setNotice('链接格式不正确，请先修正标红的单元格'); return }
    setNotice(valid.length < tableUrls.length ? '已跳过格式不正确的链接' : '')
    setLoginResult(null)
    setEditing(false)
    setPending(new Set(urls))
    setBusy('refresh')
    setStage(null)
    setLiveStatus('正在准备…')
    const runAcc = accId

    try {
      await streamSSE(
        () =>
          fetch('/api/servicenow/run/stream', {
            method: 'POST',
            headers: jsonHeaders(),
            body: JSON.stringify({ urls, account: runAcc }),
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
                localStorage.setItem(countsKey(runAcc), JSON.stringify(next))
                return next
              })
            } else if (event.type === 'identity') {
              onIdentity(event.data)
            } else if (event.type === 'stage') {
              setStage(event.stage)
            } else if (event.type === 'done') {
              addLog({ type: 'done', message: '✓ 读取完成' })
            } else if (event.type === 'error') {
              addLog({ type: 'error', message: event.message })
            } else {
              addLog({ type: 'status', message: event.message })
              setLiveStatus(event.message)
            }
          },
        }
      )
    } finally {
      setPending(new Set())
      setBusy(null)
      setStage(null)
      setLiveStatus('')
    }
  }

  const cellInfo = (url) => {
    if (!url) return { kind: 'empty', text: '' }
    const problem = urlProblem(url)
    if (problem) return { kind: 'error', text: problem }
    if (pending.has(url)) return { kind: 'pending', text: '…' }
    const v = counts.values[url]
    if (!v) return { kind: 'none', text: '—' }
    return v.error ? { kind: 'error', text: v.error } : { kind: 'count', text: String(v.count) }
  }

  const copyResults = async () => {
    if (!table) return
    const val = (url) => {
      const info = cellInfo(url)
      return info.kind === 'count' ? info.text : ''
    }
    const grid = [
      [table.title, ...table.cols],
      ...table.rows.map((row, r) => [row, ...table.cols.map((_, c) => val(table.cells[cellKey(r, c)]))]),
    ]

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

  const busyTitle =
    stage === 'window' ? '需要登录'
    : busy === 'check' || (busy === 'refresh' && stage === 'check') ? '正在检查登录状态'
    : busy === 'refresh' ? '正在读取数据'
    : '正在登录'

  const identityText = account?.user_name
    ? `${account.display_name || account.user_name}（${account.user_name}）`
    : account?.logged_in ? '已登录（身份待读取）' : '尚未登录'

  // One-line status next to the identity picker; only a needed user action or a failure gets a full banner.
  const chip = busy
    ? { kind: 'busy', text: stage === 'window' ? '等待登录…' : liveStatus || `${busyTitle}…` }
    : loginResult?.ok
      ? { kind: 'ok', text: loginResult.text }
      : { kind: account?.logged_in ? 'ok' : 'off', text: identityText }

  const accountMenu = [
    { label: '重新检查登录', onClick: login, disabled: loading || !account?.logged_in },
    { label: '＋ 添加身份', onClick: addAccount, disabled: loading },
    { label: '重命名', onClick: renameAccount, disabled: loading || !account },
    { sep: true },
    { label: '清除登录', onClick: clearLogin, disabled: loading || !account?.logged_in, danger: true },
    { label: '删除身份', onClick: deleteAccount, disabled: loading || accounts.length <= 1, danger: true },
  ]

  const tableMenu = [
    { label: '重命名表格', onClick: () => renameTable() },
    { label: '复制一份', onClick: duplicateTable },
    { sep: true },
    { label: '删除表格', onClick: deleteTable, danger: true },
  ]

  const saveIndicator =
    saveState === 'error' ? (
      <button className="sn-save sn-save-error" onClick={flushSave}>保存失败，点击重试</button>
    ) : saveState === 'saved' ? (
      <span className="sn-save">✓ 已保存</span>
    ) : saveState === 'pending' || saveState === 'saving' ? (
      <span className="sn-save">保存中…</span>
    ) : null

  const logHasError = progressLog.some((e) => e.type === 'error')

  return (
    <>
      <h2 className="tool-page-title">📊 ServiceNow 数据表</h2>

      <div className="input-section">
        <p className="tool-description">每个单元格对应一个 ServiceNow 列表链接，刷新时读取该列表的记录数。</p>

        <div className="sn-account-bar">
          <span className="sn-account-label">登录身份</span>
          {accounts.length > 1 ? (
            <select value={accId} onChange={(e) => selectAccount(e.target.value)} disabled={loading}>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name}{a.logged_in ? ' ✓' : ''}</option>
              ))}
            </select>
          ) : (
            <strong>{account?.name}</strong>
          )}
          <span className={`sn-chip sn-chip-${chip.kind}`} role="status" title={chip.text}>
            <span className="sn-dot" />{chip.text}
          </span>
          {(!account?.logged_in || busy === 'login') && (
            <button className="btn-outline btn-sm" onClick={login} disabled={loading || !account}>
              {busy === 'login' ? '登录中…' : '登录'}
            </button>
          )}
          <div className="sn-menu-push">
            <ActionMenu title="身份管理" items={accountMenu} disabled={loading} />
          </div>
        </div>

        {busy && stage === 'window' && (
          <div className="sn-busy" role="status">
            <span className="sn-busy-title">需要登录</span>
            <span>请在弹出的 Edge 窗口中完成登录（含 2FA），最多等待 5 分钟，完成后会自动继续。</span>
          </div>
        )}
        {!busy && loginResult && !loginResult.ok && (
          <div className="sn-busy sn-busy-err" role="status">
            <span className="sn-busy-title">✗ {loginResult.text}</span>
          </div>
        )}

        <div className="sn-tabs">
          {tables.map((t) => (
            <button
              key={t.id}
              className={`sn-tab ${t.id === activeId ? 'active' : ''}`}
              onClick={() => switchTable(t.id)}
              onDoubleClick={() => { switchTable(t.id); renameTable(t.id) }}
              title="双击重命名"
              disabled={loading}
            >
              {t.title || '(未命名)'}
            </button>
          ))}
          <button className="sn-tab" onClick={addTable} disabled={loading}>＋ 新表格</button>
          {table && (
            <ActionMenu title={`表格管理：${table.title}`} items={tableMenu} disabled={loading} align="left" />
          )}
        </div>

        {!table && (
          <div className="sn-empty">
            <p>还没有表格，新建一个开始吧。</p>
            <button className="btn-primary sn-btn" onClick={addTable} disabled={loading}>＋ 新建表格</button>
            {notice && <p className="sn-notice">{notice}</p>}
          </div>
        )}

        {table && (
          <>
            <div className="sn-toolbar">
              <button className="btn-primary sn-btn" onClick={() => refresh(false)} disabled={loading}>
                {busy === 'refresh' ? '正在读取…' : '刷新数据'}
              </button>
              {failedUrls.length > 0 && !loading && (
                <button className="btn-outline btn-sm" onClick={() => refresh(true)}>
                  重试失败项（{failedUrls.length}）
                </button>
              )}
              <button className="btn-outline btn-sm" onClick={copyResults} disabled={loading}>复制结果</button>
              <button
                className="btn-outline btn-sm"
                onClick={() => { if (editing) flushSave(); setEditing(!editing) }}
                disabled={loading}
              >
                {editing ? '完成' : '编辑内容'}
              </button>
              {saveIndicator}
              {counts.at && (
                <span className="sn-updated">
                  更新于 {formatUpdated(counts.at)}{account ? `（${account.name}）` : ''}
                </span>
              )}
              {notice && <span className="sn-notice-inline">{notice}</span>}
            </div>

            {editing && <p className="sn-hint">直接修改表格标题、行名、列名和链接，改动会自动保存。</p>}

            <div className="sn-table-wrap">
              <table className="sn-table">
                <thead>
                  <tr>
                    <th>
                      {editing ? (
                        <input
                          className="sn-title-input"
                          value={table.title}
                          onChange={(e) => updateTable({ title: e.target.value })}
                        />
                      ) : table.title}
                    </th>
                    {table.cols.map((col, c) => (
                      <th key={c}>
                        {editing ? (
                          <div className="sn-head-edit">
                            <input value={col} onChange={(e) => setHeader('c', c, e.target.value)} />
                            <button title="删除此列" onClick={() => removeAxis('c', c)}>✕</button>
                          </div>
                        ) : col}
                      </th>
                    ))}
                    {editing && <th><button className="sn-add" onClick={() => addAxis('c')}>＋ 列</button></th>}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, r) => (
                    <tr key={r}>
                      <th>
                        {editing ? (
                          <div className="sn-head-edit">
                            <input value={row} onChange={(e) => setHeader('r', r, e.target.value)} />
                            <button title="删除此行" onClick={() => removeAxis('r', r)}>✕</button>
                          </div>
                        ) : row}
                      </th>
                      {table.cols.map((_, c) => {
                        const url = table.cells[cellKey(r, c)] || ''
                        const info = cellInfo(url)
                        const problem = urlProblem(url)
                        return (
                          <td key={c} className={info.kind === 'error' && !editing ? 'sn-cell-error' : ''}>
                            {editing ? (
                              <input
                                className={`sn-url-input ${problem ? 'sn-url-invalid' : ''}`}
                                placeholder="ServiceNow 列表链接"
                                title={problem}
                                value={url}
                                onChange={(e) => setCellUrl(r, c, e.target.value)}
                              />
                            ) : info.kind === 'count' ? (
                              <a href={url.trim()} target="_blank" rel="noopener noreferrer" title="在 ServiceNow 中打开">
                                {info.text}
                              </a>
                            ) : info.kind === 'error' ? (
                              <span title={info.text}>⚠ {shortError(info.text)}</span>
                            ) : info.text}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                  {editing && (
                    <tr>
                      <th><button className="sn-add" onClick={() => addAxis('r')}>＋ 行</button></th>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {progressLog.length > 0 && (
        <details className="progress-section sn-log" open={logHasError || undefined}>
          <summary>运行详情</summary>
          <div className="progress-log" ref={logContainerRef}>
            {progressLog.map((entry, i) => (
              <div key={i} className={`log-entry log-${entry.type}`}>{entry.message}</div>
            ))}
          </div>
        </details>
      )}
    </>
  )
}

export default ServiceNowTables
