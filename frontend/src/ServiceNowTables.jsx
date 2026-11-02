import { useEffect, useMemo, useRef, useState } from 'react'
import useSSEStream from './useSSEStream'
import ServiceNowLinks from './ServiceNowLinks'
import LinkCellInput from './LinkCellInput'
import useSnLinks, { REF_PREFIX } from './useSnLinks'
import { parseFormula, evaluateFormula, formulaMentions } from './formula'
import { moveAxis } from './tableMove'
import ActionMenu from './SnActionMenu'
import SumPicker from './SnSumPicker'
import {
  emptyCounts, countsKey, loadCounts, cellKey, removeIndex, removeSumIndex,
  urlProblem, shortError, sourceSuffix, UNCONFIRMED_HINT, formatUpdated,
} from './serviceNowUtils'
import './ServiceNowTables.css'

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
  const [drag, setDrag] = useState(null) // { axis, from, over } while a row/column is being dragged
  const { progressLog, logContainerRef, addLog, loading, streamSSE } = useSSEStream()
  const linksStore = useSnLinks(token, onAuthError)
  const { links: savedLinks, loaded: linksLoaded, loadError: linksError } = linksStore

  const saver = useRef({ timer: null, latest: null, running: false })
  const wrapRef = useRef(null)
  const scrollSpeed = useRef(0)
  const focusHead = useRef(null) // header just added; focused (and scrolled into view) after render
  const tokenRef = useRef(token)
  tokenRef.current = token
  // Throttle localStorage writes during a refresh; latestCounts holds the final value to flush.
  const latestCounts = useRef(null)
  const lastPersist = useRef(0)

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
  // O(1) lookups for cell rendering instead of scanning savedLinks per cell.
  const linkById = useMemo(() => new Map(savedLinks.map((l) => [l.id, l])), [savedLinks])
  const linkByAlias = useMemo(
    () => new Map(savedLinks.filter((l) => l.alias).map((l) => [l.alias.toLowerCase(), l])),
    [savedLinks],
  )
  const sums = table?.sums || {}
  const isSum = (axis, idx) => `${axis}${idx}` in sums

  useEffect(() => {
    if (!focusHead.current) return
    const input = wrapRef.current?.querySelector(`[data-head="${focusHead.current}"]`)
    if (!input) return
    focusHead.current = null
    input.focus()
    input.select()
  })

  useEffect(() => {
    if (drag?.axis !== 'c') return undefined
    scrollSpeed.current = 0
    const timer = setInterval(() => {
      if (scrollSpeed.current && wrapRef.current) wrapRef.current.scrollLeft += scrollSpeed.current
    }, 16)
    return () => { clearInterval(timer); scrollSpeed.current = 0 }
  }, [drag?.axis])

  // A cell value is a plain URL or "ref:<id>" of a saved link. Resolves to the URL to use:
  // { url } normally, { loading } while the saved links load, { error } if the link is unavailable.
  const resolveCell = (raw) => {
    const v = (raw || '').trim()
    if (!v.startsWith(REF_PREFIX)) return { url: v }
    if (linksError) return { url: '', error: '无法加载常用链接' }
    if (!linksLoaded) return { url: '', loading: true }
    const link = linkById.get(v.slice(REF_PREFIX.length))
    return link ? { url: link.url.trim() } : { url: '', error: '引用的常用链接已删除' }
  }

  const cellProblem = (raw) => {
    const v = (raw || '').trim()
    if (v.startsWith('=')) return resolveFormula(v).error || ''
    const r = resolveCell(v)
    return r.error || (r.loading ? '' : urlProblem(r.url))
  }

  // A formula cell: "=[PB]+[PR]". Yields { parsed, vars: {alias -> url} }, { loading }, or { error }.
  const resolveFormula = (raw) => {
    const parsed = parseFormula(raw.trim().slice(1))
    if (parsed.error) return { error: parsed.error }
    if (linksError) return { error: '无法加载常用链接' }
    if (!linksLoaded) return { loading: true }
    const vars = {}
    for (const key of parsed.aliases) {
      const link = linkByAlias.get(key)
      if (!link) return { error: `找不到缩写 [${key}]，请在常用链接里设置` }
      const problem = urlProblem(link.url.trim())
      if (problem) return { error: `[${link.alias}]：${problem}` }
      vars[key] = { url: link.url.trim(), alias: link.alias }
    }
    return { parsed, vars }
  }

  // Cells depending on a saved link: pointing at it directly, or using its alias in a formula.
  const linkUsage = (link) => {
    let refs = 0
    let formulas = 0
    for (const t of tables) {
      for (const raw of Object.values(t.cells)) {
        if (raw === `${REF_PREFIX}${link.id}`) refs += 1
        else if (raw.trim().startsWith('=') && formulaMentions(raw, link.alias)) formulas += 1
      }
    }
    return { refs, formulas }
  }

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
      sums: Object.fromEntries(Object.entries(sums).map(([k, v]) => [k, [...v]])),
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
    focusHead.current = `${axis}${table[key].length}`
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
      sums: removeSumIndex(sums, axis, idx),
    })
  }

  const moveHeader = (axis, from, to) => {
    const patch = moveAxis({ ...table, sums }, axis, from, to)
    if (patch) updateTable(patch)
  }

  const headerDnD = (axis, idx) => ({
    onDragOver: (e) => {
      if (drag?.axis !== axis) return
      e.preventDefault()
      if (drag.over !== idx) setDrag({ ...drag, over: idx })
    },
    onDrop: (e) => {
      if (drag?.axis !== axis) return
      e.preventDefault()
      moveHeader(axis, drag.from, idx)
      setDrag(null)
    },
  })

  // The pinned title cell is always visible, so it doubles as a "move to the very first position" target.
  const titleDnD = {
    onDragOver: (e) => {
      if (!drag) return
      e.preventDefault()
      if (drag.over !== 'start') setDrag({ ...drag, over: 'start' })
    },
    onDrop: (e) => {
      if (!drag) return
      e.preventDefault()
      moveHeader(drag.axis, drag.from, 0)
      setDrag(null)
    },
  }

  const dragClass = (axis, idx) =>
    drag?.axis === axis && drag.from !== idx && drag.over === idx
      ? `sn-drop-${axis}${drag.from < idx ? '-after' : '-before'}`
      : drag?.axis === axis && drag.from === idx ? 'sn-dragging' : ''

  // While dragging a column, scroll the table sideways when the pointer nears either edge.
  // The left edge starts after the pinned first column, which otherwise hides the first data columns.
  const onWrapDragOver = (e) => {
    if (drag?.axis !== 'c') return
    const wrap = wrapRef.current
    const pinned = wrap.querySelector('thead th:first-child')?.offsetWidth || 0
    const rect = wrap.getBoundingClientRect()
    const zone = 90
    const max = 24
    const left = rect.left + pinned + zone
    const right = rect.right - zone
    if (e.clientX < left) scrollSpeed.current = -Math.min(max, ((left - e.clientX) / zone) * max)
    else if (e.clientX > right) scrollSpeed.current = Math.min(max, ((e.clientX - right) / zone) * max)
    else scrollSpeed.current = 0
  }

  const onWrapDragLeave = (e) => {
    if (!wrapRef.current.contains(e.relatedTarget)) scrollSpeed.current = 0
  }

  // One editable row/column header: drag handle, name, move and delete buttons (plus the total picker).
  // Only the handle is draggable so selecting text in the name input is unaffected.
  const headEditor = (axis, idx, name, count) => {
    const vertical = axis === 'r'
    return (
      <>
        <div className="sn-head-edit">
          <span
            className="sn-drag-handle"
            title="拖动以调整顺序"
            draggable
            onDragStart={(e) => {
              e.dataTransfer.effectAllowed = 'move'
              e.dataTransfer.setData('text/plain', '')
              const cell = e.currentTarget.closest('th')
              if (cell) e.dataTransfer.setDragImage(cell, 0, 0)
              setDrag({ axis, from: idx, over: idx })
            }}
            onDragEnd={() => setDrag(null)}
          >⠿</span>
          <input
            data-head={`${axis}${idx}`}
            value={name}
            title={name}
            onChange={(e) => setHeader(axis, idx, e.target.value)}
          />
          <button
            title={vertical ? '上移（Shift+点击：移到最前）' : '左移（Shift+点击：移到最前）'}
            disabled={idx === 0}
            onClick={(e) => moveHeader(axis, idx, e.shiftKey ? 0 : idx - 1)}
          >{vertical ? '▲' : '◀'}</button>
          <button
            title={vertical ? '下移（Shift+点击：移到最后）' : '右移（Shift+点击：移到最后）'}
            disabled={idx === count - 1}
            onClick={(e) => moveHeader(axis, idx, e.shiftKey ? count - 1 : idx + 1)}
          >{vertical ? '▼' : '▶'}</button>
          <button title={vertical ? '删除此行' : '删除此列'} onClick={() => removeAxis(axis, idx)}>✕</button>
        </div>
        {isSum(axis, idx) && sumPicker(axis, idx)}
      </>
    )
  }

  // A total row/column is an ordinary row/column that adds up the chosen rows/columns of its axis.
  const addSum = (axis) => {
    const key = axis === 'r' ? 'rows' : 'cols'
    const members = table[key].map((_, i) => i).filter((i) => !isSum(axis, i))
    focusHead.current = `${axis}${table[key].length}`
    updateTable({
      [key]: [...table[key], '合计'],
      sums: { ...sums, [`${axis}${table[key].length}`]: members },
    })
  }

  const toggleSum = (axis, idx, member) => {
    const key = `${axis}${idx}`
    const cur = sums[key] || []
    const next = cur.includes(member) ? cur.filter((i) => i !== member) : [...cur, member].sort((a, b) => a - b)
    updateTable({ sums: { ...sums, [key]: next } })
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

  // URLs a cell needs counts for: its own URL/reference, or every link a formula uses.
  const cellUrls = (raw) => {
    if (raw.trim().startsWith('=')) return Object.values(resolveFormula(raw).vars || {}).map((v) => v.url)
    return [resolveCell(raw).url].filter(Boolean)
  }

  const urlsOf = (list) => [...new Set(list.flatMap((t) => Object.values(t.cells).flatMap(cellUrls)))]
  const failedOf = (urls) => urls.filter((u) => !urlProblem(u) && counts.values[u]?.error)

  const tableUrls = table ? urlsOf([table]) : []
  const failedUrls = failedOf(tableUrls)
  const allFailedUrls = failedOf(urlsOf(tables))

  // scope 'table' = active table; 'all' = every table in one run (shared links are read once).
  const refresh = async (onlyFailed = false, scope = 'table') => {
    const scoped = scope === 'all' ? tables : table ? [table] : []
    if (!scoped.length) return
    const cellValues = scoped.flatMap((t) => Object.values(t.cells)).filter((raw) => raw.trim())
    if (!cellValues.length) { setNotice(scope === 'all' ? '还没有配置任何单元格链接' : '这个表格还没有配置任何单元格链接'); return }
    if (!linksLoaded && cellValues.some((raw) => /^(=|ref:)/.test(raw.trim())) && !linksError) {
      setNotice('常用链接还在加载，请稍后再试')
      return
    }
    const valid = urlsOf(scoped).filter((u) => !urlProblem(u))
    const urls = onlyFailed ? failedOf(valid) : valid
    if (!urls.length) {
      setNotice(onlyFailed ? '没有需要重试的失败项' : '链接格式不正确，请先修正标红的单元格')
      return
    }
    const skipped = cellValues.filter((raw) => cellProblem(raw)).length
    setNotice(skipped ? '已跳过格式不正确或已失效的链接' : '')
    setLoginResult(null)
    setEditing(false)
    setPending(new Set(urls))
    setBusy('refresh')
    setStage(null)
    setLiveStatus('正在准备…')
    const runAcc = accId
    let announced = false
    latestCounts.current = null
    lastPersist.current = 0

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
            if (!announced) {
              announced = true
              addLog({
                type: 'status',
                message: onlyFailed
                  ? `重试 ${urls.length} 个失败的链接`
                  : `${scope === 'all' ? `${scoped.length} 个表格，` : ''}${cellValues.length} 个单元格，共 ${urls.length} 个不同的链接（公式用到的每个链接各读一次）`,
              })
            }
            if (event.type === 'result') {
              const { url, count, error, source, unconfirmed } = event.data
              setPending((prev) => { const n = new Set(prev); n.delete(url); return n })
              setCounts((prev) => {
                const next = {
                  values: { ...prev.values, [url]: error ? { error } : { count, source, unconfirmed: !!unconfirmed } },
                  at: new Date().toISOString(),
                }
                latestCounts.current = next
                // Persist at most ~once a second during a run; the final value is flushed in finally.
                const now = Date.now()
                if (now - lastPersist.current >= 1000) {
                  lastPersist.current = now
                  localStorage.setItem(countsKey(runAcc), JSON.stringify(next))
                }
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
      if (latestCounts.current) {
        localStorage.setItem(countsKey(runAcc), JSON.stringify(latestCounts.current))
      }
      setPending(new Set())
      setBusy(null)
      setStage(null)
      setLiveStatus('')
    }
  }

  // Sum of the formula's variables. Variables without data count as 0 and make the result partial (⚠),
  // the same way incomplete totals behave. An unconfirmed 0 is still shown, with ⚠, instead of hiding
  // the number; like the plain cell it is not a trusted count for totals and copying.
  const formulaInfo = (raw) => {
    const f = resolveFormula(raw)
    const entries = Object.entries(f.vars || {})
    if (f.loading || entries.some(([, v]) => pending.has(v.url))) return { kind: 'pending', text: '…' }
    const failures = {}
    const unconfirmed = []
    const { total, missing } = evaluateFormula(f.parsed.ast, (key) => {
      const v = counts.values[f.vars[key].url]
      if (!v || v.error) { failures[key] = v?.error || '尚未读取'; return null }
      if (v.unconfirmed) unconfirmed.push(f.vars[key].alias)
      return v.count
    })
    const expr = raw.trim()
    const missingDetail = missing.map((a) => `[${a}]：${failures[a.toLowerCase()]}`).join('\n')
    const unconfirmedDetail = unconfirmed.length ? `${UNCONFIRMED_HINT}：${unconfirmed.map((a) => `[${a}]`).join('、')}` : ''
    if (missing.length === entries.length && entries.length) {
      return { kind: 'none', text: '—', title: `${expr}\n没有数据：\n${missingDetail}` }
    }
    if (missing.length) {
      const detail = [`结果不完整，缺少：\n${missingDetail}`, unconfirmedDetail].filter(Boolean).join('\n')
      return { kind: 'partial', text: `${total} ⚠`, title: `${expr}\n${detail}` }
    }
    const values = entries
      .map(([, v]) => `[${v.alias}] = ${counts.values[v.url].count}${sourceSuffix(counts.values[v.url].source)}`)
      .join('\n')
    const title = [expr, values, unconfirmedDetail].filter(Boolean).join('\n')
    if (unconfirmed.length) return { kind: 'unconfirmed', text: `${total} ⚠`, value: total, title }
    return { kind: 'count', text: String(total), value: total, title }
  }

  // `raw` is the stored cell value; returns what to show and, for counts, the URL to open.
  const cellInfo = (raw) => {
    if (!raw?.trim()) return { kind: 'empty', text: '' }
    const problem = cellProblem(raw)
    if (problem) return { kind: 'error', text: problem }
    if (raw.trim().startsWith('=')) return formulaInfo(raw)
    const { url, loading: resolving } = resolveCell(raw)
    if (resolving || pending.has(url)) return { kind: 'pending', text: '…' }
    const v = counts.values[url]
    if (!v) return { kind: 'none', text: '—', url }
    if (v.error) return { kind: 'error', text: v.error }
    if (v.unconfirmed) return { kind: 'unconfirmed', text: `${v.count} ⚠`, value: v.count, url, title: UNCONFIRMED_HINT }
    return { kind: 'count', text: String(v.count), value: v.count, url, title: `数据来源${sourceSuffix(v.source) || '：未知'}` }
  }

  // Total cell for (r, c), or null for an ordinary cell. The crossing of a total row and total
  // column is left blank. Incomplete totals keep the partial sum with a ⚠ marker (`partial`).
  const sumCellInfo = (r, c, t = table) => {
    const tSums = t.sums || {}
    const rowSum = `r${r}` in tSums
    const colSum = `c${c}` in tSums
    if (!rowSum && !colSum) return null
    if (rowSum && colSum) return { kind: 'sum', text: '', partial: false }
    const refs = colSum
      ? (tSums[`c${c}`] || []).map((m) => cellKey(r, m))
      : (tSums[`r${r}`] || []).map((m) => cellKey(m, c))
    let total = 0
    let counted = 0
    let missing = 0
    for (const k of refs) {
      const url = t.cells[k]
      if (!url?.trim()) continue
      const info = cellInfo(url)
      if (info.kind === 'count') { total += info.value; counted += 1 } else missing += 1
    }
    if (!counted && !missing) return { kind: 'sum', text: '', partial: false }
    if (!counted) return { kind: 'sum', text: '—', partial: true, title: '汇总的单元格还没有数据' }
    if (missing) return { kind: 'sum', text: `${total} ⚠`, partial: true, title: `有 ${missing} 个汇总的单元格没有数据，合计不完整` }
    return { kind: 'sum', text: String(total), partial: false }
  }

  // Checkbox list (edit mode) choosing which rows/columns a total adds up.
  const sumPicker = (axis, idx) => (
    <SumPicker
      names={axis === 'r' ? table.rows : table.cols}
      isSum={(i) => isSum(axis, i)}
      members={sums[`${axis}${idx}`] || []}
      onToggle={(i) => toggleSum(axis, idx, i)}
    />
  )

  const copyResults = async (scope = 'table') => {
    const scoped = scope === 'all' ? tables : table ? [table] : []
    if (!scoped.length) return
    let missing = 0
    const cellText = (t, r, c) => {
      const s = sumCellInfo(r, c, t)
      if (s) {
        if (s.partial) missing += 1
        return s.partial ? '' : s.text
      }
      const raw = t.cells[cellKey(r, c)]
      const info = cellInfo(raw)
      if (info.kind === 'count') return info.text
      if (raw?.trim()) missing += 1
      return ''
    }
    const grids = scoped.map((t) => [
      [t.title, ...t.cols],
      ...t.rows.map((row, r) => [row, ...t.cols.map((_, c) => cellText(t, r, c))]),
    ])

    const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
    const toHtml = (grid) =>
      '<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse">' +
      grid
        .map((line, i) =>
          '<tr>' + line.map((v, j) => (i === 0 || j === 0 ? `<th>${esc(v)}</th>` : `<td>${esc(v)}</td>`)).join('') + '</tr>'
        )
        .join('') +
      '</table>'
    // The spacer paragraph keeps Outlook/Teams from merging adjacent tables.
    const html = grids.map(toHtml).join('<p>&nbsp;</p>')
    const text = grids.map((grid) => grid.map((line) => line.join('\t')).join('\n')).join('\n\n')
    const what = scoped.length > 1 ? `${scoped.length} 个表格` : ''
    const suffix = missing ? `，有 ${missing} 个单元格没有数据` : ''

    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' }),
        }),
      ])
      setNotice(`已复制${what}${suffix}，可直接粘贴到 Excel / Outlook / Teams`)
    } catch {
      try {
        await navigator.clipboard.writeText(text)
        setNotice(`已复制${what}为文本（制表符分隔）${suffix}`)
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

  const refreshMenu = [
    { label: '刷新当前表格', onClick: () => refresh(false) },
    { label: `刷新全部表格（${tables.length} 个）`, onClick: () => refresh(false, 'all') },
    { sep: true },
    { label: `重试全部表格的失败项（${allFailedUrls.length}）`, onClick: () => refresh(true, 'all'), disabled: !allFailedUrls.length },
  ]

  const copyMenu = [
    { label: '复制当前表格', onClick: () => copyResults() },
    { label: `复制全部表格（${tables.length} 个）`, onClick: () => copyResults('all') },
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
    <div className="sn-page">
      <h2 className="tool-page-title">📊 ServiceNow 数据表</h2>

      <div className="input-section sn-main">
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
              <div className="sn-split">
                <button className="btn-primary sn-btn" onClick={() => refresh(false)} disabled={loading}>
                  {busy === 'refresh' ? '正在读取…' : '刷新数据'}
                </button>
                {tables.length > 1 && (
                  <ActionMenu title="刷新范围" label="▾" items={refreshMenu} disabled={loading} align="left" />
                )}
              </div>
              {failedUrls.length > 0 && !loading && (
                <button className="btn-outline btn-sm" onClick={() => refresh(true)}>
                  重试失败项（{failedUrls.length}）
                </button>
              )}
              <div className="sn-split">
                <button className="btn-outline btn-sm" onClick={() => copyResults()} disabled={loading}>复制结果</button>
                {tables.length > 1 && (
                  <ActionMenu title="复制范围" label="▾" items={copyMenu} disabled={loading} align="left" />
                )}
              </div>
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

            {editing && (
              <>
                <p className="sn-hint">
                  直接修改表格标题、行名、列名和链接，改动会自动保存。单元格也可以填公式，如 =[PB]+[PR]（[缩写] 在常用链接里设置，输入 [ 可选择）。拖动 ⠿ 或点箭头可调整行/列顺序。
                </p>
                <div className="sn-edit-tools">
                  <button className="sn-add" onClick={() => addAxis('r')}>＋ 行</button>
                  <button className="sn-add" onClick={() => addSum('r')}>＋ 合计行</button>
                  <button className="sn-add" onClick={() => addAxis('c')}>＋ 列</button>
                  <button className="sn-add" onClick={() => addSum('c')}>＋ 合计列</button>
                </div>
              </>
            )}

            <div className="sn-table-wrap" ref={wrapRef} onDragOver={onWrapDragOver} onDragLeave={onWrapDragLeave}>
              <table className={`sn-table${editing ? ' sn-editing' : ''}`}>
                <thead>
                  <tr>
                    <th
                      className={drag?.over === 'start' ? 'sn-drop-start' : ''}
                      title={editing && drag ? '放到这里：移到最前' : undefined}
                      {...(editing ? titleDnD : {})}
                    >
                      {editing ? (
                        <input
                          className="sn-title-input"
                          value={table.title}
                          onChange={(e) => updateTable({ title: e.target.value })}
                        />
                      ) : table.title}
                    </th>
                    {table.cols.map((col, c) => (
                      <th key={c} className={dragClass('c', c)} {...(editing ? headerDnD('c', c) : {})}>
                        {editing ? headEditor('c', c, col, table.cols.length) : col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, r) => (
                    <tr key={r}>
                      <th className={dragClass('r', r)} {...(editing ? headerDnD('r', r) : {})}>
                        {editing ? headEditor('r', r, row, table.rows.length) : row}
                      </th>
                      {table.cols.map((_, c) => {
                        const sumInfo = sumCellInfo(r, c)
                        if (sumInfo) {
                          return (
                            <td key={c} className="sn-sum-cell" title={sumInfo.title}>
                              {sumInfo.text}
                            </td>
                          )
                        }
                        const raw = table.cells[cellKey(r, c)] || ''
                        const info = cellInfo(raw)
                        const problem = cellProblem(raw)
                        return (
                          <td
                            key={c}
                            className={
                              info.kind === 'error' && !editing ? 'sn-cell-error'
                                : info.kind === 'unconfirmed' && !editing ? 'sn-cell-warn' : ''
                            }
                          >
                            {editing ? (
                              <LinkCellInput
                                value={raw}
                                links={savedLinks}
                                ready={linksLoaded}
                                problem={problem}
                                onChange={(v) => setCellUrl(r, c, v)}
                              />
                            ) : (info.kind === 'count' || info.kind === 'unconfirmed') && info.url ? (
                              <a
                                href={info.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                title={`在 ServiceNow 中打开\n${info.title}`}
                              >
                                {info.text}
                              </a>
                            ) : info.kind === 'error' ? (
                              <span title={info.text}>⚠ {shortError(info.text)}</span>
                            ) : <span title={info.title}>{info.text}</span>}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      <ServiceNowLinks store={linksStore} usageCount={linkUsage} />

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
    </div>
  )
}

export default ServiceNowTables
