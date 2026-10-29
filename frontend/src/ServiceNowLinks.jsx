import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import LinkComposer from './LinkComposer'
import ConditionLibrary from './ConditionLibrary'
import { linkKind, KIND_LABEL } from './useSnLinks'

const OPEN_KEY = 'sn_links_open'
const KIND_KEY = 'sn_links_kind'
const MAX_LINKS = 200

const emptyDraft = { name: '', url: '', note: '', alias: '' }

const ALIAS_RE = /^[A-Za-z_][A-Za-z0-9_]{0,19}$/

const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}${Math.random().toString(16).slice(2)}`)

// Mirrors the server rule: https://*.service-now.com
const urlProblem = (url) => {
  const u = url.trim()
  if (!u) return '请输入链接'
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
  return ''
}

// simplelist URLs carry a readable title: .../params/list-title/<title>/query/...
const guessName = (url) => {
  const m = url.match(/\/list-title\/([^/]+)/)
  if (!m) return ''
  try {
    return decodeURIComponent(m[1])
  } catch {
    return m[1]
  }
}

// `store` is the shared useSnLinks() state; `usageCount(link)` returns how many cells reference a link
// directly ({ refs }) and how many formulas use its alias ({ formulas }).
function ServiceNowLinks({ store, usageCount }) {
  const { links, loaded, loadError, saving } = store
  const [open, setOpen] = useState(() => localStorage.getItem(OPEN_KEY) !== '0')
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState(() => {
    const saved = localStorage.getItem(KIND_KEY)
    return saved === 'classic' || saved === 'simplelist' ? saved : 'all'
  })
  const [editingId, setEditingId] = useState(null) // link id, 'new', or null
  const [composingId, setComposingId] = useState(null) // link whose conditions are being recombined
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [draft, setDraft] = useState(emptyDraft)
  const [saveError, setSaveError] = useState('')
  const [copiedId, setCopiedId] = useState(null)
  const error = saveError || loadError
  const setError = setSaveError

  useEffect(() => {
    localStorage.setItem(OPEN_KEY, open ? '1' : '0')
  }, [open])

  useEffect(() => {
    localStorage.setItem(KIND_KEY, kind)
  }, [kind])

  const modalOpen = editingId !== null || composingId !== null || libraryOpen
  useEffect(() => {
    if (!modalOpen) return undefined
    const onKey = (e) => {
      if (e.key !== 'Escape' || saving) return
      setEditingId(null)
      setComposingId(null)
      setLibraryOpen(false)
      setSaveError('')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [modalOpen, saving])

  // Persists the full list; returns true on success.
  const persist = async (next) => {
    setError('')
    const res = await store.persist(next)
    if (!res.ok) setError(res.error)
    return res.ok
  }

  const startAdd = () => {
    setOpen(true)
    setEditingId('new')
    setDraft(emptyDraft)
    setError('')
  }

  const startEdit = (link) => {
    setComposingId(null)
    setEditingId(link.id)
    setDraft({ name: link.name, url: link.url, note: link.note, alias: link.alias || '' })
    setError('')
  }

  const cancelEdit = () => {
    setEditingId(null)
    setError('')
  }

  const submit = async () => {
    const problem = urlProblem(draft.url)
    if (problem) { setError(problem); return }
    const url = draft.url.trim()
    const alias = draft.alias.trim()
    if (alias && !ALIAS_RE.test(alias)) {
      setError('缩写只能包含字母、数字、下划线，不能以数字开头，最长 20 个字符')
      return
    }
    if (alias && links.some((l) => l.id !== editingId && (l.alias || '').toLowerCase() === alias.toLowerCase())) {
      setError(`缩写「${alias}」已被其他链接使用`)
      return
    }
    const entry = { name: draft.name.trim() || guessName(url) || '未命名', url, note: draft.note.trim(), alias }
    if (editingId === 'new' && links.length >= MAX_LINKS) { setError(`最多保存 ${MAX_LINKS} 条链接`); return }
    const old = links.find((l) => l.id === editingId)
    if (old?.alias && old.alias.toLowerCase() !== alias.toLowerCase()) {
      const { formulas = 0 } = usageCount?.(old) || {}
      if (formulas && !window.confirm(
        `有 ${formulas} 个公式在使用缩写 [${old.alias}]，修改后这些公式会报错，需要手动更新。继续？`
      )) return
    }
    const next = editingId === 'new'
      ? [...links, { id: newId(), ...entry }]
      : links.map((l) => (l.id === editingId ? { ...l, ...entry } : l))
    if (await persist(next)) setEditingId(null)
  }

  // Saves a link built by the composer; resolves to an error message, or '' on success.
  const saveComposed = async (entry) => {
    if (links.length >= MAX_LINKS) return `最多保存 ${MAX_LINKS} 条链接`
    const res = await store.persist([...links, { id: newId(), ...entry }])
    if (!res.ok) return res.error || '保存失败'
    setComposingId(null)
    return ''
  }

  // Saves a link built in the condition library; the library stays open so more links can be built.
  const saveFromLibrary = async (entry) => {
    if (links.length >= MAX_LINKS) return `最多保存 ${MAX_LINKS} 条链接`
    const res = await store.persist([...links, { id: newId(), ...entry }])
    return res.ok ? '' : res.error || '保存失败'
  }

  const startCompose = (link) => {
    setEditingId(null)
    setComposingId(link.id)
    setError('')
  }

  const remove = async (link) => {
    const { refs = 0, formulas = 0 } = usageCount?.(link) || {}
    const used = refs + formulas
    const warn = used ? `\n\n有 ${used} 个表格单元格正在使用它，删除后这些单元格会显示错误。` : ''
    if (!window.confirm(`删除链接「${link.name}」？${warn}`)) return
    await persist(links.filter((l) => l.id !== link.id))
  }

  const copy = async (link) => {
    try {
      await navigator.clipboard.writeText(link.url)
      setCopiedId(link.id)
      setTimeout(() => setCopiedId((id) => (id === link.id ? null : id)), 1500)
    } catch {
      setError('复制失败，请手动复制')
    }
  }

  const kindCounts = useMemo(() => {
    const c = { classic: 0, simplelist: 0 }
    links.forEach((l) => { c[linkKind(l.url)] += 1 })
    return c
  }, [links])
  // The switch only makes sense when both kinds exist; otherwise everything is shown.
  const showKinds = kindCounts.classic > 0 && kindCounts.simplelist > 0
  const activeKind = showKinds ? kind : 'all'

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return links.filter((l) => (
      (activeKind === 'all' || linkKind(l.url) === activeKind)
      && (!q || `${l.name} ${l.alias || ''} ${l.note} ${l.url}`.toLowerCase().includes(q))
    ))
  }, [links, query, activeKind])

  const form = (
    <div className="sn-links-form">
      {error && <p className="sn-links-error" role="alert">{error}</p>}
      <label className="sn-field">
        <span>链接</span>
        <input
          value={draft.url}
          onChange={(e) => setDraft({ ...draft, url: e.target.value })}
          onBlur={() => {
            if (!draft.name.trim()) {
              const guess = guessName(draft.url)
              if (guess) setDraft((d) => ({ ...d, name: guess }))
            }
          }}
          placeholder="粘贴 ServiceNow 链接（https://xxx.service-now.com/…）"
          autoFocus
        />
      </label>
      <label className="sn-field">
        <span>名称</span>
        <input
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          placeholder="留空则取链接里的列表标题"
          maxLength={100}
        />
      </label>
      <label className="sn-field">
        <span>缩写</span>
        <input
          value={draft.alias}
          onChange={(e) => setDraft({ ...draft, alias: e.target.value })}
          placeholder="可选，公式里用 [缩写] 引用，如 PB"
          maxLength={20}
        />
      </label>
      <label className="sn-field">
        <span>备注</span>
        <input
          value={draft.note}
          onChange={(e) => setDraft({ ...draft, note: e.target.value })}
          placeholder="可选"
          maxLength={500}
        />
      </label>
      <div className="sn-links-form-actions">
        <button className="btn-primary btn-sm" onClick={submit} disabled={saving}>保存</button>
        <button className="btn-outline btn-sm" onClick={cancelEdit} disabled={saving}>取消</button>
      </div>
    </div>
  )

  const composingLink = composingId ? links.find((l) => l.id === composingId) : null

  return (
    <div className={`sn-links ${open ? 'sn-links-open' : 'sn-links-closed'}`}>
      <div className="sn-links-bar">
        <button
          className="sn-links-toggle"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          title={open ? '收起常用链接' : '展开常用链接'}
        >
          <span className="sn-links-caret">{open ? '▸' : '◂'}</span>
          🔖 常用链接{loaded ? ` (${links.length})` : ''}
        </button>
        <div className="sn-links-bar-actions">
          <button className="btn-outline btn-sm" onClick={() => setLibraryOpen(true)} disabled={!loaded} title="管理条件，并用条件直接生成链接">
            🧩 条件
          </button>
          <button className="btn-outline btn-sm" onClick={startAdd} disabled={!loaded || editingId === 'new'}>
            ＋ 添加
          </button>
        </div>
      </div>

      {open && (
        <div className="sn-links-panel">
          {error && !modalOpen && <p className="sn-links-error" role="alert">{error}</p>}
          {showKinds && (
            <div className="sn-kind-tabs" role="tablist" aria-label="按链接格式筛选">
              {[
                ['all', '全部', links.length],
                ['classic', KIND_LABEL.classic, kindCounts.classic],
                ['simplelist', KIND_LABEL.simplelist, kindCounts.simplelist],
              ].map(([key, label, n]) => (
                <button
                  key={key}
                  role="tab"
                  aria-selected={activeKind === key}
                  className={`sn-kind-tab ${activeKind === key ? 'active' : ''}`}
                  onClick={() => setKind(key)}
                >
                  {label} <span className="sn-kind-count">{n}</span>
                </button>
              ))}
            </div>
          )}
          {links.length > 0 && (
            <input
              className="sn-links-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索名称、缩写、备注或链接"
            />
          )}
          {loaded && links.length === 0 && (
            <p className="sn-links-empty">还没有保存的链接，点「＋ 添加」粘贴一个调好 filter 的链接。</p>
          )}
          {links.length > 0 && filtered.length === 0 && (
            <p className="sn-links-empty">
              {activeKind !== 'all' && !query.trim()
                ? `没有${KIND_LABEL[activeKind]}链接。`
                : '没有匹配的链接。'}
              {activeKind !== 'all' && <> <button className="sn-links-reset" onClick={() => setKind('all')}>查看全部</button></>}
            </p>
          )}
          <ul className="sn-links-list">
            {filtered.map((l) => (
              <li key={l.id} className="sn-links-item">
                <div className="sn-links-main">
                  <div className="sn-links-head">
                    <a href={l.url} target="_blank" rel="noopener noreferrer" className="sn-links-name" title={l.url}>
                      {l.name}
                    </a>
                    {l.alias && <span className="sn-links-alias" title="公式里用 [缩写] 引用">[{l.alias}]</span>}
                    <span className={`sn-kind sn-kind-${linkKind(l.url)}`}>{KIND_LABEL[linkKind(l.url)]}</span>
                  </div>
                  {l.note && <span className="sn-links-note" title={l.note}>{l.note}</span>}
                </div>
                <div className="sn-links-actions">
                  <button className="sn-link-act" title="复制链接" aria-label="复制链接" onClick={() => copy(l)}>
                    {copiedId === l.id ? '✓' : '⧉'}
                  </button>
                  <button className="sn-link-act" title="组合条件生成新链接" aria-label="组合" onClick={() => startCompose(l)} disabled={saving}>⊞</button>
                  <button className="sn-link-act" title="编辑" aria-label="编辑" onClick={() => startEdit(l)} disabled={saving}>✎</button>
                  <button className="sn-link-act sn-danger" title="删除" aria-label="删除" onClick={() => remove(l)} disabled={saving}>✕</button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {editingId !== null && createPortal(
        <div className="sn-modal-backdrop">
          <div className="sn-modal" role="dialog" aria-modal="true" aria-label={editingId === 'new' ? '添加链接' : '编辑链接'}>
            <div className="sn-modal-title">{editingId === 'new' ? '添加链接' : '编辑链接'}</div>
            {form}
          </div>
        </div>,
        document.body,
      )}

      {composingLink && createPortal(
        <div className="sn-modal-backdrop">
          <div className="sn-modal sn-modal-wide" role="dialog" aria-modal="true" aria-label="组合条件">
            <LinkComposer
              base={composingLink}
              links={links}
              api={store.api}
              onSave={saveComposed}
              onCancel={() => setComposingId(null)}
            />
          </div>
        </div>,
        document.body,
      )}
      {libraryOpen && createPortal(
        <div className="sn-modal-backdrop">
          <div className="sn-modal sn-modal-xl" role="dialog" aria-modal="true" aria-label="条件库">
            <ConditionLibrary
              links={links}
              api={store.api}
              stream={store.stream}
              onSave={saveFromLibrary}
              onClose={() => setLibraryOpen(false)}
            />
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}

export default ServiceNowLinks
