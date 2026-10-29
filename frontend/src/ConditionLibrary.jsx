import { useEffect, useMemo, useState } from 'react'
import { conflicts } from './LinkComposer'

const ALIAS_RE = /^[A-Za-z_][A-Za-z0-9_]{0,19}$/
const FORMAT_KEY = 'sn_library_format'

const STATUS_FILTERS = [['all', '全部'], ['selected', '已选'], ['used', '已使用'], ['unused', '未使用']]
const TYPE_FILTERS = [['all', '全部'], ['eq', '＝'], ['ne', '≠'], ['other', '包含/其他'], ['or', 'OR 组'], ['tag', '标签']]
const OTHER_GROUP = '其他 / OR 组'
const SOURCE_LABEL = { link: '链接', import: '导入', manual: '手动' }
const SOURCE_HINT = {
  link: '从已保存的链接自动收录',
  import: '从链接导入（链接没有保存）',
  manual: '手动新增',
}

const typeOf = (c) => {
  if (c.raw.includes('^')) return 'or'
  if (c.field === 'sys_tags') return 'tag'
  if (c.op === '=') return 'eq'
  if (c.op === '!=') return 'ne'
  return 'other'
}

// Manages every condition collected from the saved links and builds new links straight from them.
// `links` are the saved links, `api` is useSnLinks().api, and `onSave(entry)` saves a built link,
// resolving to an error message or ''.
function ConditionLibrary({ links, api, stream, onSave, onClose }) {
  const [data, setData] = useState(null) // { conditions, tables }
  const [loadError, setLoadError] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [table, setTable] = useState('')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState([])
  const [statusFilter, setStatusFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [fieldFilter, setFieldFilter] = useState('')
  const [linkFilter, setLinkFilter] = useState('')
  const [collapsed, setCollapsed] = useState([]) // group names folded by the user
  const [editing, setEditing] = useState(null) // raw of the condition being edited
  const [editName, setEditName] = useState('')
  const [editNote, setEditNote] = useState('')
  const [adding, setAdding] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importItems, setImportItems] = useState(null) // preview of the pasted links
  const [importPicked, setImportPicked] = useState([]) // `${item index}|${raw}` of the chosen conditions
  const [importBusy, setImportBusy] = useState(false)
  const [importStatus, setImportStatus] = useState('')
  const [namesBusy, setNamesBusy] = useState(false)
  const [namesStatus, setNamesStatus] = useState('')
  const [rawCopied, setRawCopied] = useState('')

  const copyRaw = async (raw) => {
    try {
      await navigator.clipboard.writeText(raw)
      setRawCopied(raw)
      setTimeout(() => setRawCopied(''), 1500)
    } catch {
      setError('复制失败，请手动复制')
    }
  }
  const [sourceFilter, setSourceFilter] = useState('all')
  const [newRaw, setNewRaw] = useState('')
  const [newName, setNewName] = useState('')
  const [format, setFormat] = useState(() => (localStorage.getItem(FORMAT_KEY) === 'classic' ? 'classic' : 'simplelist'))
  const [name, setName] = useState('')
  const [alias, setAlias] = useState('')
  const [note, setNote] = useState('')
  const [preview, setPreview] = useState(null) // { url } | { error }
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    const res = await api('/api/servicenow/conditions')
    if (!res.ok) { setLoadError(res.error); return }
    setData(res.data)
    setTable((t) => (res.data.tables.includes(t) ? t : res.data.tables[0] || ''))
  }

  // Saving a link collects its conditions on the server, so reload whenever the links change.
  useEffect(() => { load() }, [links]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { localStorage.setItem(FORMAT_KEY, format) }, [format])

  const inTable = useMemo(() => (data?.conditions || []).filter((c) => c.table === table), [data, table])
  const byRaw = useMemo(() => new Map(inTable.map((c) => [c.raw, c])), [inTable])

  const groupOf = (c) => c.field || OTHER_GROUP

  const filtersActive = !!(search.trim() || statusFilter !== 'all' || typeFilter !== 'all' || sourceFilter !== 'all' || fieldFilter || linkFilter)

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    return inTable.filter((c) => (
      (!q || `${c.label} ${c.raw} ${c.note} ${c.used_by.map((u) => u.name).join(' ')}`.toLowerCase().includes(q))
      && (statusFilter === 'all'
        || (statusFilter === 'selected' && selected.includes(c.raw))
        || (statusFilter === 'used' && c.used_by.length > 0)
        || (statusFilter === 'unused' && c.used_by.length === 0))
      && (typeFilter === 'all' || typeOf(c) === typeFilter)
      && (sourceFilter === 'all' || c.source === sourceFilter)
      && (!fieldFilter || groupOf(c) === fieldFilter)
      && (!linkFilter || c.used_by.some((u) => u.id === linkFilter))
    ))
  }, [inTable, search, statusFilter, typeFilter, sourceFilter, fieldFilter, linkFilter, selected])

  // Visible conditions grouped by field: [{ name, items }], named groups A-Z and the catch-all last.
  const groups = useMemo(() => {
    const map = new Map()
    for (const c of visible) {
      const g = groupOf(c)
      if (!map.has(g)) map.set(g, [])
      map.get(g).push(c)
    }
    return [...map.entries()]
      .map(([groupName, items]) => ({ name: groupName, items: items.sort((a, b) => a.label.localeCompare(b.label)) }))
      .sort((a, b) => (a.name === OTHER_GROUP) - (b.name === OTHER_GROUP) || a.name.localeCompare(b.name))
  }, [visible])

  // Options for the field and link dropdowns, taken from every condition of the table.
  const fieldOptions = useMemo(() => {
    const m = new Map()
    inTable.forEach((c) => m.set(groupOf(c), (m.get(groupOf(c)) || 0) + 1))
    return [...m.entries()].sort((a, b) => (a[0] === OTHER_GROUP) - (b[0] === OTHER_GROUP) || a[0].localeCompare(b[0]))
  }, [inTable])

  const linkOptions = useMemo(() => {
    const m = new Map()
    inTable.forEach((c) => c.used_by.forEach((u) => m.set(u.id, { name: u.name, n: (m.get(u.id)?.n || 0) + 1 })))
    return [...m.entries()].sort((a, b) => a[1].name.localeCompare(b[1].name))
  }, [inTable])

  const statusCount = (key) => {
    if (key === 'all') return inTable.length
    if (key === 'selected') return selected.length
    return inTable.filter((c) => (key === 'used') === (c.used_by.length > 0)).length
  }
  const typeCount = (key) => (key === 'all' ? inTable.length : inTable.filter((c) => typeOf(c) === key).length)
  const sourceCount = (key) => (key === 'all' ? inTable.length : inTable.filter((c) => c.source === key).length)
  const sourceKinds = Object.keys(SOURCE_LABEL).filter((k) => sourceCount(k) > 0)

  const clearFilters = () => {
    setSearch('')
    setStatusFilter('all')
    setTypeFilter('all')
    setSourceFilter('all')
    setFieldFilter('')
    setLinkFilter('')
  }

  // While filtering, every group with a match is shown open so results are never hidden.
  const isCollapsed = (g) => !filtersActive && collapsed.includes(g)
  const toggleGroup = (g) => setCollapsed((c) => (c.includes(g) ? c.filter((x) => x !== g) : [...c, g]))

  // Only conditions collected from links are cleaned up; imported and manual ones are kept.
  const unusedCount = (data?.conditions || []).filter((c) => c.source === 'link' && !c.used_by.length).length
  const countByTable = useMemo(() => {
    const m = {}
    ;(data?.conditions || []).forEach((c) => { m[c.table] = (m[c.table] || 0) + 1 })
    return m
  }, [data])

  const changeTable = (t) => {
    setTable(t)
    setSelected([])
    setEditing(null)
    clearFilters()
    setCollapsed([])
  }

  const toggle = (cond) => {
    setInfo('')
    if (selected.includes(cond.raw)) {
      setSelected(selected.filter((r) => r !== cond.raw))
      return
    }
    const clash = selected.map((r) => byRaw.get(r)).filter((c) => c && conflicts(c, cond))
    if (clash.length) {
      const names = clash.map((c) => `「${c.label}」`).join('、')
      if (!window.confirm(`「${cond.label}」与已选的${names}冲突（同一字段，结果会为空）。\n\n用它替换已选的条件？`)) return
      setSelected([...selected.filter((r) => !clash.some((c) => c.raw === r)), cond.raw])
      return
    }
    setSelected([...selected, cond.raw])
  }

  const startEdit = (c) => {
    setEditing(c.raw)
    setEditName(c.custom ? c.label : '')
    setEditNote(c.note || '')
    setError('')
  }

  const saveEdit = async (c) => {
    const res = await api('/api/servicenow/conditions', 'POST', { table: c.table, raw: c.raw, name: editName, note: editNote })
    if (!res.ok) { setError(res.error); return }
    setData(res.data)
    setEditing(null)
  }

  const submitNew = async () => {
    setError('')
    const raw = newRaw.trim()
    if (!raw) { setError('请输入条件，如 state!=1000'); return }
    const res = await api('/api/servicenow/conditions', 'POST', { table, raw, name: newName })
    if (!res.ok) { setError(res.error); return }
    setData(res.data)
    setNewRaw('')
    setNewName('')
    setAdding(false)
    setSelected((s) => (s.includes(raw) ? s : [...s, raw]))
  }

  const remove = async (c) => {
    if (!window.confirm(`从条件库删除「${c.label}」？`)) return
    const res = await api('/api/servicenow/conditions/delete', 'POST', { table: c.table, raw: c.raw })
    if (!res.ok) { setError(res.error); return }
    setError('')
    setData(res.data)
    setSelected((s) => s.filter((r) => r !== c.raw))
  }

  const cleanup = async () => {
    if (!window.confirm(`删除 ${unusedCount} 个从链接收录、但现在没有任何链接使用的条件？\n（导入和手动新增的条件不会被删除）`)) return
    const res = await api('/api/servicenow/conditions/cleanup', 'POST', {})
    if (!res.ok) { setError(res.error); return }
    setError('')
    setData(res.data)
    setSelected((s) => s.filter((r) => res.data.conditions.some((c) => c.table === table && c.raw === r)))
  }

  const toggleImport = () => {
    setImportOpen((o) => !o)
    setError('')
  }

  // Shows the progress events of a worker run; `working` is what to say while it reads.
  const showProgress = (setStatus, working) => (event) => {
    if (event.type === 'stage' && event.stage === 'window') setStatus('需要登录：请在弹出的 Edge 窗口中完成登录…')
    else if (event.type === 'stage' && event.stage === 'working') setStatus(working)
    else if (event.type === 'status') setStatus(event.message)
  }

  const readNames = async (refresh) => {
    setError('')
    setInfo('')
    setNamesBusy(true)
    setNamesStatus('正在读取名称…')
    const run = await stream('/api/servicenow/conditions/names/stream', { refresh }, showProgress(setNamesStatus, '正在读取名称…'))
    setNamesBusy(false)
    setNamesStatus('')
    if (!run.ok) { setError(run.error || '读取名称失败'); return }
    await load()
    setInfo('已读取名称，条件的显示文字已更新（条件本身没有改动）')
  }

  const previewImport = async () => {
    setError('')
    setInfo('')
    setImportStatus('')
    const urls = importText.split(/\s+/).filter(Boolean)
    if (!urls.length) { setError('请粘贴至少一条链接'); return }
    setImportBusy(true)
    const res = await api('/api/servicenow/conditions/import/preview', 'POST', { urls })
    if (!res.ok) { setImportBusy(false); setError(res.error); return }
    let items = res.data.items
    // Saved-list links keep their filter on the server: read it with the signed-in Edge, then preview
    // the equivalent simplelist link.
    const saved = items.filter((it) => it.needs_resolve).map((it) => it.url)
    if (saved.length) {
      const resolved = {}
      setImportStatus('正在读取已保存列表…')
      const progress = showProgress(setImportStatus, '正在读取已保存列表…')
      const run = await stream('/api/servicenow/conditions/import/resolve/stream', { urls: saved }, (event) => {
        if (event.type === 'result' && event.data.url) resolved[event.data.url] = event.data
        else progress(event)
      })
      const ok = saved.filter((u) => resolved[u]?.resolved_url)
      const previews = {}
      if (ok.length) {
        const again = await api('/api/servicenow/conditions/import/preview', 'POST', { urls: ok.map((u) => resolved[u].resolved_url) })
        if (again.ok) again.data.items.forEach((it, i) => { previews[ok[i]] = { ...it, source: ok[i] } })
      }
      items = items.map((it) => {
        if (!it.needs_resolve) return it
        if (previews[it.url]) return previews[it.url]
        return { ...it, needs_resolve: false, error: resolved[it.url]?.error || run.error || '无法读取这条已保存列表链接' }
      })
    }
    // Names of ids (people, states…) only change how conditions are shown; failing to read them is harmless.
    const unnamed = items.filter((it) => it.ok && it.names_missing).map((it) => it.url)
    if (unnamed.length) {
      setImportStatus('正在读取名称…')
      const run = await stream('/api/servicenow/conditions/names/stream', { urls: unnamed }, showProgress(setImportStatus, '正在读取名称…'))
      if (run.ok) {
        const again = await api('/api/servicenow/conditions/import/preview', 'POST', { urls: unnamed })
        if (again.ok) {
          const fresh = Object.fromEntries(again.data.items.map((it) => [it.url, it]))
          items = items.map((it) => (fresh[it.url] ? { ...fresh[it.url], source: it.source } : it))
        }
      }
    }
    setImportBusy(false)
    setImportStatus('')
    setImportItems(items)
    // New conditions are chosen by default; the ones the library already has stay unchecked.
    setImportPicked(items.flatMap((it, i) => (it.ok ? it.conditions.filter((c) => !c.exists).map((c) => `${i}|${c.raw}`) : [])))
  }

  const toggleImportPick = (key) => {
    setImportPicked((p) => (p.includes(key) ? p.filter((k) => k !== key) : [...p, key]))
  }

  const confirmImport = async () => {
    const items = importItems
      .map((it, i) => ({ url: it.url, conditions: it.conditions.filter((c) => importPicked.includes(`${i}|${c.raw}`)).map((c) => c.raw) }))
      .filter((it) => it.conditions.length)
    if (!items.length) { setError('请至少选择一个条件'); return }
    setImportBusy(true)
    const res = await api('/api/servicenow/conditions/import', 'POST', { items })
    setImportBusy(false)
    if (!res.ok) { setError(res.error); return }
    setError('')
    setData(res.data.library)
    const firstTable = importItems.find((it) => it.ok && it.url === items[0].url)?.table
    if (firstTable && firstTable !== table) changeTable(firstTable)
    setInfo(`已导入 ${res.data.added} 个新条件${res.data.existing ? `，${res.data.existing} 个已在条件库中（已标为导入，不会被清理）` : ''}`)
    setImportItems(null)
    setImportPicked([])
    setImportText('')
    setImportOpen(false)
  }

  const importSection = (
    <>
      <button className="btn-outline btn-sm" onClick={toggleImport} title="粘贴链接，只把里面的条件放进条件库，不保存链接">
        {importOpen ? '取消导入' : '⇩ 从链接导入'}
      </button>
      {importOpen && (
        <div className="sn-lib-import">
          <textarea
            value={importText}
            onChange={(e) => { setImportText(e.target.value); setImportItems(null) }}
            placeholder={'粘贴 simplelist、经典或已保存列表链接，每行一条。\n只导入里面的条件，链接本身不会保存。'}
            rows={4}
          />
          {!importItems && (
            <button className="btn-primary btn-sm" onClick={previewImport} disabled={importBusy || !importText.trim()}>
              {importBusy ? '解析中…' : '解析链接'}
            </button>
          )}
          {importBusy && importStatus && <div className="sn-lib-import-head">{importStatus}</div>}
          {importItems && (
            <>
              {importItems.map((it, i) => (
                <div key={i} className="sn-lib-import-item">
                  <div className="sn-lib-import-head" title={it.source || it.url}>
                    {it.ok ? `${it.title || it.table}（${it.table}）` : '无法导入'}
                    {!it.ok && <span className="sn-links-error"> {it.error}</span>}
                  </div>
                  {it.ok && (
                    <ul className="sn-comp-list">
                      {it.conditions.map((c) => (
                        <li key={c.raw}>
                          <label title={c.raw_display || c.raw}>
                            <input
                              type="checkbox"
                              checked={importPicked.includes(`${i}|${c.raw}`)}
                              onChange={() => toggleImportPick(`${i}|${c.raw}`)}
                            />
                            <span className="sn-comp-label">{c.label}</span>
                            {c.exists && <span className="sn-lib-used">已在库中</span>}
                          </label>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
              <div className="sn-lib-edit-actions">
                <button className="btn-primary btn-sm" onClick={confirmImport} disabled={importBusy || !importPicked.length}>
                  {importBusy ? '导入中…' : `导入选中的 ${importPicked.length} 个条件`}
                </button>
                <button className="btn-outline btn-sm" onClick={() => setImportItems(null)} disabled={importBusy}>重新解析</button>
              </div>
            </>
          )}
        </div>
      )}
    </>
  )

  // Live preview of the generated link.
  useEffect(() => {
    if (!table || !selected.length) { setPreview(null); return undefined }
    let cancelled = false
    const timer = setTimeout(async () => {
      const res = await api('/api/servicenow/links/build', 'POST', {
        table, conditions: selected, format, title: name.trim() || table,
      })
      if (!cancelled) setPreview(res.ok ? { url: res.data.url } : { error: res.error })
    }, 250)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [table, selected, format, name]) // eslint-disable-line react-hooks/exhaustive-deps

  const copy = async () => {
    if (!preview?.url) return
    try {
      await navigator.clipboard.writeText(preview.url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setError('复制失败，请手动复制')
    }
  }

  const save = async () => {
    const trimmedAlias = alias.trim()
    if (!preview?.url) { setError(preview?.error || '请至少选择一个条件'); return }
    if (!name.trim()) { setError('请输入名称'); return }
    if (trimmedAlias && !ALIAS_RE.test(trimmedAlias)) {
      setError('缩写只能包含字母、数字、下划线，不能以数字开头，最长 20 个字符')
      return
    }
    if (trimmedAlias && links.some((l) => (l.alias || '').toLowerCase() === trimmedAlias.toLowerCase())) {
      setError(`缩写「${trimmedAlias}」已被其他链接使用`)
      return
    }
    setBusy(true)
    const err = await onSave({ name: name.trim(), url: preview.url, note: note.trim(), alias: trimmedAlias })
    setBusy(false)
    if (err) { setError(err); return }
    setError('')
    setInfo(`已保存「${name.trim()}」到常用链接`)
    setAlias('')
    setNote('')
  }

  if (loadError) {
    return (
      <div className="sn-comp">
        <p className="sn-links-error">{loadError}</p>
        <button className="btn-outline btn-sm" onClick={onClose}>关闭</button>
      </div>
    )
  }
  if (!data) return <div className="sn-comp"><p className="sn-links-empty">加载条件中…</p></div>

  return (
    <div className="sn-comp sn-lib">
      <div className="sn-lib-head">
        <div className="sn-comp-title">条件库</div>
        <button className="btn-outline btn-sm" onClick={onClose} disabled={busy}>关闭</button>
      </div>
      <p className="sn-hint">保存链接时会自动收录它的条件。勾选同一张表的条件，直接生成新的 classic 或 simplelist 链接。</p>
      {error && <p className="sn-links-error" role="alert">{error}</p>}
      {info && <p className="sn-hint sn-lib-info">{info}</p>}

      {data.tables.length === 0 ? (
        <div className="sn-lib-empty">
          <p className="sn-links-empty">条件库里还没有条件。可以先在「常用链接」里添加调好 filter 的链接，或者从链接导入条件。</p>
          {importSection}
        </div>
      ) : (
        <div className="sn-lib-grid">
          <div className="sn-lib-left">
            <div className="sn-lib-tools">
              <select value={table} onChange={(e) => changeTable(e.target.value)} aria-label="选择表">
                {data.tables.map((t) => <option key={t} value={t}>{t} ({countByTable[t] || 0})</option>)}
              </select>
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索条件、备注或链接名" />
            </div>
            <div className="sn-lib-tools">
              <button className="btn-outline btn-sm" onClick={() => { setAdding((a) => !a); setError('') }}>
                {adding ? '取消新增' : '＋ 新增条件'}
              </button>
              {selected.length > 0 && (
                <button className="btn-outline btn-sm" onClick={() => setSelected([])}>清除已选</button>
              )}
              {unusedCount > 0 && (
                <button className="btn-outline btn-sm" onClick={cleanup} title="删除从链接收录、但现在没有链接使用的条件；导入和手动新增的不会删除">
                  清理未使用 ({unusedCount})
                </button>
              )}
              <button
                className="btn-outline btn-sm"
                onClick={() => readNames(false)}
                disabled={namesBusy}
                title="从 ServiceNow 读取条件里的人名、状态等的可读名称，只用于显示，不会改动条件本身（需要已登录的身份，已登录时在后台运行）"
              >
                {namesBusy ? (namesStatus || '读取中…') : data.names_missing > 0 ? `读取名称 (${data.names_missing})` : '刷新名称'}
              </button>
            </div>
            <div className="sn-lib-tools">{importSection}</div>
            {adding && (
              <div className="sn-lib-add">
                <input
                  value={newRaw}
                  onChange={(e) => setNewRaw(e.target.value)}
                  placeholder="条件，如 state!=1000 或 sub_phase=1^ORsub_phase=2"
                  maxLength={1000}
                  autoFocus
                />
                <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="显示名称（可选）" maxLength={100} />
                <button className="btn-primary btn-sm" onClick={submitNew}>加入条件库</button>
              </div>
            )}

            <div className="sn-lib-filters">
              <div className="sn-lib-chips" role="group" aria-label="按使用状态筛选">
                {STATUS_FILTERS.map(([key, label]) => (
                  <button
                    key={key}
                    className={`sn-lib-chip ${statusFilter === key ? 'active' : ''}`}
                    aria-pressed={statusFilter === key}
                    onClick={() => setStatusFilter(key)}
                  >
                    {label} <span className="sn-kind-count">{statusCount(key)}</span>
                  </button>
                ))}
              </div>
              <div className="sn-lib-chips" role="group" aria-label="按条件类型筛选">
                {TYPE_FILTERS.filter(([key]) => key === 'all' || typeCount(key) > 0).map(([key, label]) => (
                  <button
                    key={key}
                    className={`sn-lib-chip ${typeFilter === key ? 'active' : ''}`}
                    aria-pressed={typeFilter === key}
                    onClick={() => setTypeFilter(key)}
                  >
                    {label} <span className="sn-kind-count">{typeCount(key)}</span>
                  </button>
                ))}
              </div>
              {sourceKinds.length > 1 && (
                <div className="sn-lib-chips" role="group" aria-label="按条件来源筛选">
                  {['all', ...sourceKinds].map((key) => (
                    <button
                      key={key}
                      className={`sn-lib-chip ${sourceFilter === key ? 'active' : ''}`}
                      aria-pressed={sourceFilter === key}
                      title={SOURCE_HINT[key]}
                      onClick={() => setSourceFilter(key)}
                    >
                      {key === 'all' ? '全部来源' : `来源：${SOURCE_LABEL[key]}`} <span className="sn-kind-count">{sourceCount(key)}</span>
                    </button>
                  ))}
                </div>
              )}
              <div className="sn-lib-tools">
                <select value={fieldFilter} onChange={(e) => setFieldFilter(e.target.value)} aria-label="按字段筛选">
                  <option value="">字段：全部</option>
                  {fieldOptions.map(([f, n]) => <option key={f} value={f}>{f} ({n})</option>)}
                </select>
                <select value={linkFilter} onChange={(e) => setLinkFilter(e.target.value)} aria-label="按来源链接筛选">
                  <option value="">来源链接：全部</option>
                  {linkOptions.map(([id, l]) => <option key={id} value={id}>{l.name} ({l.n})</option>)}
                </select>
              </div>
              <div className="sn-lib-summary">
                <span>显示 {visible.length} / {inTable.length} 个条件，{groups.length} 个字段</span>
                {filtersActive && <button className="sn-links-reset" onClick={clearFilters}>清除筛选</button>}
                {groups.length > 1 && !filtersActive && (
                  <>
                    <button className="sn-links-reset" onClick={() => setCollapsed(groups.map((g) => g.name))}>全部折叠</button>
                    <button className="sn-links-reset" onClick={() => setCollapsed([])}>全部展开</button>
                  </>
                )}
              </div>
            </div>

            {visible.length === 0 && (
              <p className="sn-links-empty">{inTable.length ? '没有匹配的条件。' : '这张表还没有条件。'}</p>
            )}
            <div className="sn-lib-list">
              {groups.map((g) => {
                const picked = g.items.filter((c) => selected.includes(c.raw)).length
                const folded = isCollapsed(g.name)
                return (
                  <section key={g.name} className="sn-lib-group">
                    <button className="sn-lib-group-head" onClick={() => toggleGroup(g.name)} aria-expanded={!folded}>
                      <span className="sn-links-caret">{folded ? '▸' : '▾'}</span>
                      <span className="sn-lib-group-name">{g.name}</span>
                      <span className="sn-kind-count">{g.items.length}</span>
                      {picked > 0 && <span className="sn-lib-picked">已选 {picked}</span>}
                    </button>
                    {!folded && (
                      <ul className="sn-comp-list">
                        {g.items.map((c) => (
                <li key={c.raw} className="sn-lib-item">
                  {editing === c.raw ? (
                    <div className="sn-lib-edit">
                      <code className="sn-lib-raw">{c.raw_display || c.raw}</code>
                      {c.raw_display && c.raw_display !== c.raw && (
                        <button className="btn-outline btn-sm" onClick={() => copyRaw(c.raw)} title="显示的文字里 ID 已换成名称，不能直接当条件用；这里复制真实的条件原文">
                          {rawCopied === c.raw ? '已复制' : '复制原文'}
                        </button>
                      )}
                      <input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="显示名称（留空恢复默认）" maxLength={100} />
                      <input value={editNote} onChange={(e) => setEditNote(e.target.value)} placeholder="备注（可选）" maxLength={500} />
                      <div className="sn-lib-edit-actions">
                        <button className="btn-primary btn-sm" onClick={() => saveEdit(c)}>保存</button>
                        <button className="btn-outline btn-sm" onClick={() => setEditing(null)}>取消</button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <label title={c.raw_display || c.raw}>
                        <input type="checkbox" checked={selected.includes(c.raw)} onChange={() => toggle(c)} />
                        <span className="sn-comp-label">{c.label}</span>
                        {(c.used_by.length > 0 || c.source === 'link') && (
                          <span
                            className={`sn-lib-used ${c.used_by.length ? '' : 'sn-lib-unused'}`}
                            title={c.used_by.length ? c.used_by.map((u) => u.name).join('\n') : '没有链接使用这个条件'}
                          >
                            {c.used_by.length ? `${c.used_by.length} 条链接` : '未使用'}
                          </span>
                        )}
                        {c.source !== 'link' && (
                          <span className="sn-lib-used sn-lib-source" title={SOURCE_HINT[c.source]}>{SOURCE_LABEL[c.source]}</span>
                        )}
                        {c.note && <span className="sn-ref-note">{c.note}</span>}
                      </label>
                      <div className="sn-lib-actions">
                        <button className="sn-link-act" title="修改名称和备注" aria-label="修改名称和备注" onClick={() => startEdit(c)}>✎</button>
                        <button
                          className="sn-link-act sn-danger"
                          title={c.used_by.length ? '有链接在使用，不能删除' : '从条件库删除'}
                          aria-label="删除条件"
                          onClick={() => remove(c)}
                          disabled={c.used_by.length > 0}
                        >✕</button>
                      </div>
                    </>
                          )}
                        </li>
                        ))}
                      </ul>
                    )}
                  </section>
                )
              })}
            </div>
          </div>

          <div className="sn-lib-right">
            <div className="sn-comp-sec">用所选条件生成链接{selected.length > 0 ? `（已选 ${selected.length} 个）` : ''}</div>
            <div className="sn-comp-fields">
              <div className="sn-comp-format">
                <span>生成格式</span>
                <label><input type="radio" checked={format === 'classic'} onChange={() => setFormat('classic')} /> 经典</label>
                <label><input type="radio" checked={format === 'simplelist'} onChange={() => setFormat('simplelist')} /> simplelist</label>
              </div>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="新链接的名称" maxLength={100} />
              <input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="缩写（可选，用于公式）" maxLength={20} />
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注（可选）" maxLength={500} />
            </div>
            <div className="sn-comp-preview">
              {!selected.length ? '请至少选择一个条件'
                : preview?.error ? <span className="sn-links-error">{preview.error}</span>
                : preview?.url ? <code title={preview.url}>{preview.url}</code> : '生成中…'}
            </div>
            <div className="sn-links-form-actions">
              <button className="btn-primary btn-sm" onClick={save} disabled={busy || !preview?.url}>保存为新链接</button>
              <button className="btn-outline btn-sm" onClick={copy} disabled={!preview?.url}>{copied ? '已复制' : '复制链接'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default ConditionLibrary
