import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import './EdifactViewer.css'
import './GefegViewer.css'
import { QuestionsSection, QuestionsTab, RefContext, openCount, isDirty, loadDrafts, saveDrafts } from './GefegQuestions'

const occurrenceText = (n) => {
  if (n.kind === 'attribute') return n.use || ''
  if (n.min_occurs == null) return ''
  return `${n.min_occurs}..${n.max_occurs === 'unbounded' ? '*' : n.max_occurs}`
}

// Status of one Map Instruction: todo | done | question | review
const mStatus = (m) => {
  const p = m.progress
  if (!p || !p.status) return 'todo'
  return p.status === 'done' && p.needs_review ? 'review' : p.status
}
const tally = (n) => {
  const c = { total: 0, done: 0, question: 0, review: 0, todo: 0 }
  n.mappings.forEach((m) => { c.total += 1; c[mStatus(m)] += 1 })
  return c
}
// Status of a node, derived from its instructions. null = not trackable (no Map Instructions);
// todo | partial | done | question | review
const effStatus = (n) => {
  if (!n.mappings.length) return null
  const c = tally(n)
  if (c.question) return 'question'
  if (c.review) return 'review'
  if (c.done === c.total) return 'done'
  return c.done ? 'partial' : 'todo'
}
const STATUS_ICON = { todo: '○', partial: '◐', done: '✅', question: '❓', review: '⚠️' }
const STATUS_LABEL = { todo: 'Not started', partial: 'In progress', done: 'Implemented', question: 'Question', review: 'Needs review' }

const emptyCounts = { total: 0, done: 0, question: 0, review: 0, todo: 0 }

const DEFAULT_TREE_WIDTH = 440
const TREE_WIDTH_KEY = 'gfg-tree-width'

function GefegViewer({ token, onAuthError }) {
  const authHeaders = useMemo(() => (token ? { Authorization: `Bearer ${token}` } : {}), [token])
  const inputRef = useRef(null)
  const importRef = useRef(null)
  const pageRef = useRef(null)
  const [treeWidth, setTreeWidth] = useState(() => Number(localStorage.getItem(TREE_WIDTH_KEY)) || DEFAULT_TREE_WIDTH)

  // The viewer fills the rest of the window so the tree and the detail pane scroll on their own.
  useLayoutEffect(() => {
    const fit = () => {
      const el = pageRef.current
      if (!el) return
      const top = el.getBoundingClientRect().top + window.scrollY
      el.style.height = `${Math.max(520, window.innerHeight - top - 20)}px`
    }
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [])

  useEffect(() => { localStorage.setItem(TREE_WIDTH_KEY, String(treeWidth)) }, [treeWidth])

  const startResize = (e) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = treeWidth
    const move = (ev) => setTreeWidth(Math.min(1000, Math.max(240, startWidth + ev.clientX - startX)))
    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
  }
  const [list, setList] = useState([])
  const [activeId, setActiveId] = useState('')
  const [data, setData] = useState(null) // { guideline, nodes, changes }
  const [uploading, setUploading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [inheritPanel, setInheritPanel] = useState(null) // { candidates, manual }

  const [tab, setTab] = useState('structure')
  const [query, setQuery] = useState('')
  const [onlyMapped, setOnlyMapped] = useState(false)
  const [onlyChanged, setOnlyChanged] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const [collapsed, setCollapsed] = useState(() => new Set())
  const [selected, setSelected] = useState(null)
  const [dtab, setDtab] = useState('details') // sub-tab of the detail pane; kept while switching top tabs / fields
  const [composer, setComposer] = useState(null) // the question box that receives field references from the tree
  const [revealed, setRevealed] = useState(null) // { seq } of the field to scroll to in the tree

  // Unsent question texts, keyed `${guidelineId}|${draftKey}`, so they survive tab and field switches.
  const [allDrafts, setAllDrafts] = useState(loadDrafts)
  useEffect(() => { saveDrafts(allDrafts) }, [allDrafts])
  const draftPrefix = `${activeId}|`
  const drafts = useMemo(() => Object.fromEntries(
    Object.entries(allDrafts).filter(([k]) => k.startsWith(draftPrefix)).map(([k, d]) => [k.slice(draftPrefix.length), d]),
  ), [allDrafts, draftPrefix])
  // `upd` is null (discard), a new draft, or a function patching an existing draft
  const setDraft = useCallback((key, upd) => setAllDrafts((prev) => {
    const k = draftPrefix + key
    if (upd === null) {
      if (!(k in prev)) return prev
      const { [k]: _gone, ...rest } = prev
      return rest
    }
    if (typeof upd === 'function') return k in prev ? { ...prev, [k]: upd(prev[k]) } : prev
    return { ...prev, [k]: upd }
  }), [draftPrefix])
  const api = useCallback(async (url, options = {}) => {
    const res = await fetch(url, { ...options, headers: { ...authHeaders, ...(options.headers || {}) } })
    if (res.status === 401) {
      onAuthError?.()
      throw new Error('Session expired, please log in again')
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `Server error ${res.status}`)
    }
    return res.json()
  }, [authHeaders, onAuthError])

  const loadList = useCallback(async () => {
    const res = await api('/api/gefeg')
    setList(res.guidelines)
    return res.guidelines
  }, [api])

  const openGuideline = useCallback(async (id) => {
    setLoading(true)
    setError('')
    try {
      const res = await api(`/api/gefeg/${id}`)
      setActiveId(id)
      setData(res)
      setSelected(res.nodes[0]?.seq ?? null)
      setCollapsed(new Set())
      setQuery('')
      setStatusFilter('')
      setInheritPanel(null)
      setTab('structure')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [api])

  useEffect(() => {
    loadList()
      .then((items) => items[0] && openGuideline(items[0].id))
      .catch((err) => setError(err.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const upload = async (fileList) => {
    const file = Array.from(fileList || []).find((f) => /\.pdf$/i.test(f.name))
    if (!file) {
      setError('Please choose a .pdf file exported from GEFEG.FX')
      return
    }
    setUploading(true)
    setError('')
    setNotice('')
    try {
      const form = new FormData()
      form.append('file', file, file.name)
      const res = await api('/api/gefeg/upload', { method: 'POST', body: form })
      await loadList()
      await openGuideline(res.id)
      setNotice(res.duplicate
        ? 'This file was already imported; opened the existing record.'
        : `Parsed ${res.node_count} fields and ${res.change_count} changes.`)
      if (res.inherit_candidates?.length) setInheritPanel({ candidates: res.inherit_candidates, manual: false })
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
    }
  }

  const openManualInherit = async () => {
    try {
      const res = await api(`/api/gefeg/${activeId}/progress/candidates?all=true`)
      if (!res.candidates.length) {
        setNotice('There is no other guideline to inherit progress from.')
        return
      }
      setInheritPanel({ candidates: res.candidates, manual: true })
    } catch (err) {
      setError(err.message)
    }
  }

  const applyInherit = async (fromId, overwrite) => {
    try {
      const res = await api(`/api/gefeg/${activeId}/progress/inherit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_id: fromId, overwrite }),
      })
      setInheritPanel(null)
      await openGuideline(activeId)
      setNotice(
        `Inherited progress for ${res.copied} Map Instruction(s)` +
        `${res.skipped ? ` (skipped ${res.skipped} already marked)` : ''}` +
        `${res.review ? `; ${res.review} changed since they were marked and need review` : ''}` +
        `${res.missing ? `; ${res.missing} marked instruction(s) of the old version have no match in this one` : ''}.`,
      )
      loadList().catch(() => {})
    } catch (err) {
      setError(err.message)
    }
  }

  // Optimistic update; falls back to the previous state if saving fails.
  // targets: [{ seq, key? }] (no key = every Map Instruction of the node)
  const saveProgress = async (targets, patch) => {
    const before = data
    const sel = new Map()
    targets.forEach((t) => {
      if (t.key == null) sel.set(t.seq, null)
      else if (sel.get(t.seq) !== null) sel.set(t.seq, (sel.get(t.seq) || new Set()).add(t.key))
    })
    const applyMapping = (m) => {
      const cur = m.progress || { status: '', note: '', needs_review: false }
      const next = { ...cur, ...patch }
      if (patch.status) next.needs_review = false
      return { ...m, progress: next.status || next.note ? next : null }
    }
    const apply = (n) => {
      if (!sel.has(n.seq)) return n
      const keys = sel.get(n.seq)
      return { ...n, mappings: n.mappings.map((m) => (keys === null || keys.has(m.key) ? applyMapping(m) : m)) }
    }
    setData((d) => ({ ...d, nodes: d.nodes.map(apply) }))
    try {
      const res = await api(`/api/gefeg/${activeId}/progress`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targets, ...patch }),
      })
      setData((d) => ({
        ...d,
        nodes: d.nodes.map((n) => {
          const views = res.mappings[n.seq]
          return views ? { ...n, mappings: n.mappings.map((m) => (m.key in views ? { ...m, progress: views[m.key] } : m)) } : n
        }),
      }))
    } catch (err) {
      setError(err.message)
      setData(before)
    }
  }

  const removeActive = async () => {
    if (!activeId || !window.confirm('Delete this guideline and its progress?')) return
    try {
      await api(`/api/gefeg/${activeId}`, { method: 'DELETE' })
      setData(null)
      setActiveId('')
      setSelected(null)
      const items = await loadList()
      if (items[0]) await openGuideline(items[0].id)
    } catch (err) {
      setError(err.message)
    }
  }

  // ----- questions for the BA
  const putQuestion = (q) => setData((d) => ({
    ...d,
    questions: d.questions.some((x) => x.id === q.id)
      ? d.questions.map((x) => (x.id === q.id ? q : x))
      : [...d.questions, q],
  }))

  const jsonBody = (body) => ({
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })

  const askQuestion = async (seq, mappingNr, { text, images }) => {
    try {
      putQuestion(await api(`/api/gefeg/${activeId}/questions`, jsonBody({ seq, mapping_nr: mappingNr, text, images })))
    } catch (err) {
      setError(err.message)
      throw err
    }
  }

  const followUp = async (q, { text, images }) => {
    try {
      putQuestion(await api(`/api/gefeg/${activeId}/questions/${q.id}/messages`, jsonBody({ text, images })))
    } catch (err) {
      setError(err.message)
      throw err
    }
  }

  const editMessage = async (q, m, { text, images }) => {
    try {
      putQuestion(await api(`/api/gefeg/${activeId}/questions/${q.id}/messages/${m.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, images }),
      }))
    } catch (err) {
      setError(err.message)
      throw err
    }
  }

  const deleteMessage = async (q, m) => {
    try {
      putQuestion(await api(`/api/gefeg/${activeId}/questions/${q.id}/messages/${m.id}`, { method: 'DELETE' }))
      setDraft(`edit:${m.id}`, null)
    } catch (err) {
      setError(err.message)
    }
  }

  const setQuestionStatus = async (q, status) => {
    try {
      putQuestion(await api(`/api/gefeg/${activeId}/questions/${q.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
      }))
    } catch (err) {
      setError(err.message)
    }
  }

  const deleteQuestion = async (q) => {
    try {
      await api(`/api/gefeg/${activeId}/questions/${q.id}`, { method: 'DELETE' })
      setData((d) => ({ ...d, questions: d.questions.filter((x) => x.id !== q.id) }))
      setDraft(`reply:${q.id}`, null)
      q.messages.forEach((m) => setDraft(`edit:${m.id}`, null))
    } catch (err) {
      setError(err.message)
    }
  }

  const exportHtml = async () => {
    setError('')
    try {
      const res = await api(`/api/gefeg/${activeId}/export`)
      const url = URL.createObjectURL(new Blob([res.html], { type: 'text/html' }))
      const a = document.createElement('a')
      a.href = url
      a.download = res.filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      setNotice(`Exported ${res.question_count} question(s) to ${res.filename}. Send it to the BA; they reply in the page and save it.`)
    } catch (err) {
      setError(err.message)
    }
  }

  const importReplies = async (fileList) => {
    const file = fileList?.[0]
    if (!file) return
    setError('')
    try {
      const form = new FormData()
      form.append('file', file, file.name)
      const res = await api(`/api/gefeg/${activeId}/import`, { method: 'POST', body: form })
      setData((d) => ({ ...d, questions: res.questions }))
      setNotice(
        res.new_messages
          ? `Imported ${res.new_messages} new reply(s) on ${res.updated_questions} question(s).`
          : 'No new replies in this file.' +
            (res.unmatched ? ` ${res.unmatched} question(s) in the file are unknown here.` : ''),
      )
    } catch (err) {
      setError(err.message)
    }
  }

  // ----- derived tree state
  const nodes = data?.nodes
  const changes = data?.changes
  const questions = data?.questions
  const bySeq = useMemo(() => new Map((nodes || []).map((n) => [n.seq, n])), [nodes])
  const questionsBySeq = useMemo(() => {
    const m = new Map()
    ;(questions || []).forEach((q) => m.set(q.node_seq, [...(m.get(q.node_seq) || []), q]))
    return m
  }, [questions])
  const childCount = useMemo(() => {
    const m = new Map()
    ;(nodes || []).forEach((n) => n.parent_seq != null && m.set(n.parent_seq, (m.get(n.parent_seq) || 0) + 1))
    return m
  }, [nodes])
  // fields with unsent text (follow-ups / edits of closed or deleted questions are not shown, so they don't count)
  const draftSeqs = useMemo(() => {
    const s = new Set()
    Object.values(drafts).forEach((d) => {
      if (!isDirty(d)) return
      if (d.qid != null) {
        const q = (questions || []).find((x) => x.id === d.qid)
        if (!q || q.status === 'closed') return
      }
      s.add(d.seq)
    })
    return s
  }, [drafts, questions])
  useEffect(() => {
    if (!draftSeqs.size) return undefined
    const warn = (e) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [draftSeqs])
  const changedSeqs = useMemo(() => {
    const s = new Set()
    ;(nodes || []).forEach((n) => n.mappings.some((m) => m.flag) && s.add(n.seq))
    ;(changes || []).forEach((c) => c.node_seq != null && s.add(c.node_seq))
    return s
  }, [nodes, changes])

  const counts = useMemo(() => {
    const c = { ...emptyCounts }
    ;(nodes || []).forEach((n) => {
      const t = tally(n)
      Object.keys(c).forEach((k) => { c[k] += t[k] })
    })
    return c
  }, [nodes])

  // keep the dropdown's progress in sync with local edits
  useEffect(() => {
    if (!activeId || !nodes) return
    setList((prev) => prev.map((it) => (it.id === activeId ? { ...it, progress: counts } : it)))
  }, [counts, activeId, nodes])

  // per node: how many Map Instructions its descendants have and how many are implemented
  const rollup = useMemo(() => {
    const r = new Map()
    const list = nodes || []
    list.forEach((n) => r.set(n.seq, { total: 0, done: 0 }))
    for (let i = list.length - 1; i >= 0; i--) {
      const n = list[i]
      if (n.parent_seq == null) continue
      const t = tally(n)
      const mine = r.get(n.seq)
      const up = r.get(n.parent_seq)
      up.total += mine.total + t.total
      up.done += mine.done + t.done
    }
    return r
  }, [nodes])

  // Map Instructions of the descendants that have not been started
  const pendingOf = useCallback((seq) => {
    const out = []
    const inside = new Set([seq])
    for (const n of nodes || []) {
      if (n.parent_seq != null && inside.has(n.parent_seq)) {
        inside.add(n.seq)
        n.mappings.forEach((m) => mStatus(m) === 'todo' && out.push({ seq: n.seq, key: m.key }))
      }
    }
    return out
  }, [nodes])


  const visibleSeqs = useMemo(() => {
    if (!nodes) return []
    const q = query.trim().toLowerCase()
    const filtering = q || onlyMapped || onlyChanged || statusFilter
    if (!filtering) {
      const hidden = new Set()
      const out = []
      for (const n of nodes) {
        if (n.parent_seq != null && (hidden.has(n.parent_seq) || collapsed.has(n.parent_seq))) {
          hidden.add(n.seq)
          continue
        }
        out.push(n.seq)
      }
      return out
    }
    const matches = (n) => {
      if (onlyMapped && !n.mappings.length) return false
      if (onlyChanged && !changedSeqs.has(n.seq)) return false
      if (statusFilter && !n.mappings.some((m) => mStatus(m) === statusFilter)) return false
      if (!q) return true
      const hay = [n.path, n.type, n.notes, ...n.mappings.flatMap((m) => [m.condition, m.instruction, m.additional_info, m.flag, m.progress?.note])]
      return hay.some((t) => t && t.toLowerCase().includes(q))
    }
    const keep = new Set()
    for (const n of nodes) {
      if (!matches(n)) continue
      for (let cur = n; cur; cur = cur.parent_seq != null ? bySeq.get(cur.parent_seq) : null) keep.add(cur.seq)
    }
    return nodes.filter((n) => keep.has(n.seq)).map((n) => n.seq)
  }, [nodes, query, onlyMapped, onlyChanged, statusFilter, collapsed, changedSeqs, bySeq])

  const toggle = (seq) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(seq) ? next.delete(seq) : next.add(seq)
      return next
    })

  const collapseAll = () => setCollapsed(new Set([...childCount.keys()]))
  const expandAll = () => setCollapsed(new Set())

  const gotoNode = (seq) => {
    if (seq == null) return
    // make sure every ancestor is expanded and no filter hides the node
    setCollapsed((prev) => {
      const next = new Set(prev)
      for (let n = bySeq.get(seq); n?.parent_seq != null; n = bySeq.get(n.parent_seq)) next.delete(n.parent_seq)
      return next
    })
    setQuery('')
    setOnlyMapped(false)
    setOnlyChanged(false)
    setStatusFilter('')
    setSelected(seq)
    setTab('structure')
    setRevealed({ seq })
  }
  useEffect(() => {
    if (!revealed) return
    pageRef.current?.querySelector(`.gfg-row[data-seq="${revealed.seq}"]`)?.scrollIntoView({ block: 'center' })
  }, [revealed])
  const refCtx = { nodes: nodes || [], bySeq, selected, goto: gotoNode, composer, setComposer }

  const node = selected != null ? bySeq.get(selected) : null
  const nodeChanges = useMemo(() => (changes || []).filter((c) => c.node_seq === selected), [changes, selected])
  const g = data?.guideline

  return (
    <RefContext.Provider value={refCtx}>
    <div className="gfg-page" ref={pageRef}>
      <div
        className="gfg-top"
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); upload(e.dataTransfer.files) }}
        style={dragOver ? { outline: '2px dashed #2563eb', outlineOffset: 2 } : undefined}
      >
        <h2 className="gfg-title">📑 GEFEG Guideline Viewer</h2>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,application/pdf"
          style={{ display: 'none' }}
          onChange={(e) => { upload(e.target.files); e.target.value = '' }}
        />
        <select className="gfg-guideline-select" value={activeId} onChange={(e) => e.target.value && openGuideline(e.target.value)} disabled={!list.length}>
          {!list.length && <option value="">No guideline imported yet (you can also drop a PDF here)</option>}
          {list.map((it) => (
            <option key={it.id} value={it.id}>
              {it.progress ? `✅ ${it.progress.done}/${it.progress.total} · ` : ''}
              {it.version} · {it.variant} · {it.issue_date} · {it.title || it.filename}
            </option>
          ))}
        </select>
        <button className="edx-go" onClick={() => inputRef.current?.click()} disabled={uploading}>
          {uploading ? 'Parsing...' : 'Upload PDF'}
        </button>
        <button className="btn-outline btn-sm btn-danger" onClick={removeActive} disabled={!activeId}>Delete</button>
      </div>
      {!list.length && (
        <p className="tool-description">
          Upload a <strong>Message Implementation Guideline PDF exported from GEFEG.FX</strong>. It is parsed into a field tree
          (occurrence, type, Map Instructions with condition / instruction / additional info, notes) plus the
          <strong> New Changes Summary</strong>, saved to the database, and you can track implementation progress per Map Instruction
          and collect questions for the BA.
        </p>
      )}
      {error && <div className="error-message gfg-msgline">{error}</div>}
      {notice && <div className="edx-hint gfg-msgline">{notice}</div>}

      {loading && <p className="tool-description">Loading...</p>}

      {data && !loading && (
        <>
          {inheritPanel && (
            <InheritPanel
              key={`${activeId}-${inheritPanel.manual}`}
              panel={inheritPanel}
              onApply={applyInherit}
              onClose={() => setInheritPanel(null)}
            />
          )}
          <div className="gfg-info">
            <div className="gfg-info-main" title={`${g.title}\nAuthor: ${g.author}`}>
              <strong>{g.title}</strong>
              <span>
                {[g.model, g.version, g.variant, g.issue_date].filter(Boolean).join(' · ')}
                {' · '}{g.node_count} fields / {g.change_count} changes
              </span>
            </div>
            <ProgressBar counts={counts} />
            <div className="gfg-info-actions">
              <button className="btn-outline btn-sm" onClick={openManualInherit}>Inherit progress…</button>
              <button className="btn-outline btn-sm" onClick={exportHtml} disabled={!questions.some((q) => q.status !== 'closed')}
                title="Export the questions as an HTML file the BA can reply in">⬇ Export questions</button>
              <button className="btn-outline btn-sm" onClick={() => importRef.current?.click()}
                title="Merge the BA's replies from the saved HTML file">⬆ Import replies</button>
              <input ref={importRef} type="file" accept=".html,.htm,.json" style={{ display: 'none' }}
                onChange={(e) => { importReplies(e.target.files); e.target.value = '' }} />
            </div>
          </div>

          <div className="gfg-tabs">
            <button className={tab === 'structure' ? 'active' : ''} onClick={() => setTab('structure')}>Structure</button>
            <button className={tab === 'questions' ? 'active' : ''} onClick={() => setTab('questions')}>
              Questions ({questions.length})
              {openCount(questions) > 0 && <span className="gfg-qcount"> · {openCount(questions)} waiting</span>}
            </button>
            <button className={tab === 'changes' ? 'active' : ''} onClick={() => setTab('changes')}>
              Changes ({changes.length})
            </button>
            {tab === 'structure' && (
              <div className="gfg-toolbar gfg-tabs-tools">
                <input
                  className="gfg-search"
                  placeholder="Search fields, conditions, notes (e.g. SG9/DTM)"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <select className="gfg-select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                  <option value="">All statuses</option>
                  {['todo', 'done', 'question', 'review'].map((s) => (
                    <option key={s} value={s}>{STATUS_ICON[s]} {STATUS_LABEL[s]}</option>
                  ))}
                </select>
                <label><input type="checkbox" checked={onlyMapped} onChange={(e) => setOnlyMapped(e.target.checked)} /> Mapped only</label>
                <label><input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} /> Changed only</label>
                <button className="btn-outline btn-sm" onClick={expandAll}>Expand</button>
                <button className="btn-outline btn-sm" onClick={collapseAll}>Collapse</button>
                <span className="gfg-count">{visibleSeqs.length} / {nodes.length}</span>
              </div>
            )}
          </div>

          {tab === 'structure' && (
            <>
              <div className="gfg-split" style={{ '--tree-w': `${treeWidth}px` }}>
                <div className="gfg-tree">
                  {visibleSeqs.map((seq) => {
                    const n = bySeq.get(seq)
                    const expandable = childCount.has(seq)
                    const st = effStatus(n)
                    return (
                      <div
                        key={seq}
                        data-seq={seq}
                        className={`gfg-row${seq === selected ? ' selected' : ''}`}
                        style={{ paddingLeft: 8 + n.depth * 16 }}
                        title={n.path}
                        onClick={() => setSelected(seq)}
                      >
                        <span
                          className={`gfg-twisty${expandable ? '' : ' leaf'}`}
                          onClick={(e) => { if (expandable) { e.stopPropagation(); toggle(seq) } }}
                        >
                          {expandable ? (collapsed.has(seq) ? '▸' : '▾') : '·'}
                        </span>
                        {st ? (
                          <span
                            className={`gfg-status ${st}`}
                            title={`${STATUS_LABEL[st]} · ${tally(n).done}/${n.mappings.length} Map Instructions implemented (click to ${st === 'done' ? 'clear all' : 'mark all as implemented'})`}
                            onClick={(e) => { e.stopPropagation(); saveProgress([{ seq }], { status: st === 'done' ? '' : 'done' }) }}
                          >
                            {STATUS_ICON[st]}
                          </span>
                        ) : (
                          <span className="gfg-status none" />
                        )}
                        <span className={`gfg-name ${n.kind}`}>{n.kind === 'attribute' ? '@' : ''}{n.name}</span>
                        <span className="gfg-occ">{occurrenceText(n)}</span>
                        {n.mappings.length > 0 && <span className="gfg-badge" title="Map Instructions">{n.mappings.length}</span>}
                        {rollup.get(seq).total > 0 && (
                          <span className={`gfg-rollup${rollup.get(seq).done === rollup.get(seq).total ? ' full' : ''}`} title="Implemented Map Instructions of descendant nodes">
                            {rollup.get(seq).done}/{rollup.get(seq).total}
                          </span>
                        )}
                        {n.mappings.some((m) => m.progress?.note) && <span title="Has notes">📝</span>}
                        {questionsBySeq.has(seq) && (
                          <span
                            className={`gfg-qbadge ${openCount(questionsBySeq.get(seq)) ? 'open' : 'answered'}`}
                            title={openCount(questionsBySeq.get(seq)) ? 'Waiting for the BA' : 'The BA has replied'}
                          >
                            {openCount(questionsBySeq.get(seq)) ? '❓' : '💬'} {questionsBySeq.get(seq).length}
                          </span>
                        )}
                        {composer && (
                          <span
                            className="gfg-ref-btn"
                            title="Insert a reference to this field into your question"
                            onClick={(e) => { e.stopPropagation(); composer.insert(n) }}
                          >@</span>
                        )}
                        {draftSeqs.has(seq) && <span className="gfg-draft" title="Has an unsent question draft">✎</span>}
                        {changedSeqs.has(seq) && <span className="gfg-changed" title="Changed in the latest version">●</span>}
                      </div>
                    )
                  })}
                  {!visibleSeqs.length && <div className="gfg-empty">No matching fields</div>}
                </div>

                <div
                  className="gfg-divider"
                  role="separator"
                  title="Drag to resize"
                  onPointerDown={startResize}
                  onDoubleClick={() => setTreeWidth(DEFAULT_TREE_WIDTH)}
                />

                <div className="gfg-detail">
                  {node ? (
                    <NodeDetail
                      node={node}
                      changes={nodeChanges}
                      onSave={(patch, key) => saveProgress([{ seq: node.seq, key }], patch)}
                      pending={pendingOf(node.seq)}
                      onMarkAll={(targets) => saveProgress(targets, { status: 'done' })}
                      questions={questionsBySeq.get(node.seq) || []}
                      dtab={dtab}
                      setDtab={setDtab}
                      hasDraft={draftSeqs.has(node.seq)}
                      qActions={{ drafts, setDraft, onAsk: askQuestion, onFollowUp: followUp, onEditMessage: editMessage, onDeleteMessage: deleteMessage, onStatus: setQuestionStatus, onDelete: deleteQuestion, onError: setError }}
                    />
                  ) : (
                    <div className="gfg-empty">Select a field on the left to see its details</div>
                  )}
                </div>
              </div>
            </>
          )}

          {tab === 'questions' && (
            <div className="gfg-scroll"><QuestionsTab questions={questions} bySeq={bySeq} onGoto={gotoNode} /></div>
          )}

          {tab === 'changes' && (
            <div className="gfg-scroll gfg-changes">
              {!changes.length && <div className="gfg-empty">This guideline has no changes summary.</div>}
              {changes.map((c) => {
                const target = c.node_seq != null ? bySeq.get(c.node_seq) : null
                const st = target ? effStatus(target) : null
                return (
                  <div className="gfg-card" key={c.no + c.path + c.number}>
                    <div className="gfg-card-head">
                      <span className="gfg-no">{c.no}</span>
                      <strong>{c.tag}</strong>
                      <span className="gfg-flag">{c.flag}</span>
                      <span className="edx-spacer" />
                      {st && <span className="gfg-status-label" title="Implementation status of this field">{STATUS_ICON[st]} {STATUS_LABEL[st]}</span>}
                      {c.node_seq != null && (
                        <button className="btn-outline btn-sm" onClick={() => gotoNode(c.node_seq)}>Go to field</button>
                      )}
                    </div>
                    <div className="gfg-path">{c.path}</div>
                    <Fields fields={[
                      ['Instruction Number', c.number],
                      ['Condition', c.condition],
                      ['Instruction', c.instruction],
                      ['Additional Information', c.additional_info],
                      ['General Notes', c.notes],
                    ]} />
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
    </RefContext.Provider>
  )
}

function Fields({ fields }) {
  return (
    <dl className="gfg-fields">
      {fields.filter(([, v]) => v).map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function ProgressBar({ counts }) {
  const pct = (n) => (counts.total ? (n / counts.total) * 100 : 0)
  return (
    <div className="gfg-progress">
      <div className="gfg-bar" title="Counted over Map Instructions">
        <span className="done" style={{ width: `${pct(counts.done)}%` }} />
        <span className="review" style={{ width: `${pct(counts.review)}%` }} />
        <span className="question" style={{ width: `${pct(counts.question)}%` }} />
      </div>
      <div className="gfg-progress-text">
        <strong>{counts.done}/{counts.total}</strong> implemented ({Math.round(pct(counts.done))}%)
        <span title="Questions">❓ {counts.question}</span>
        <span title="Needs review">⚠️ {counts.review}</span>
        <span title="Not started">○ {counts.todo}</span>
      </div>
    </div>
  )
}

function InheritPanel({ panel, onApply, onClose }) {
  const [fromId, setFromId] = useState(panel.candidates[0]?.id || '')
  const [overwrite, setOverwrite] = useState(false)
  const c = panel.candidates.find((x) => x.id === fromId)
  return (
    <div className="gfg-inherit">
      <strong>
        {panel.manual
          ? 'Inherit progress from another guideline'
          : 'An older version of the same Model was found. Inherit its progress?'}
      </strong>
      <div className="edx-row">
        <div className="edx-field edx-grow">
          <select value={fromId} onChange={(e) => setFromId(e.target.value)}>
            {panel.candidates.map((x) => (
              <option key={x.id} value={x.id}>
                {panel.manual ? `${x.model} · ` : ''}{x.version} · {x.variant} · {x.issue_date}
              </option>
            ))}
          </select>
        </div>
        <label className="gfg-inline">
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)}           /> Overwrite existing marks
        </label>
                 <button className="edx-go" disabled={!c} onClick={() => onApply(fromId, overwrite)}>Inherit progress</button>
                 <button className="btn-outline btn-sm" onClick={onClose}>{panel.manual ? 'Cancel' : 'Do not inherit'}</button>
      </div>
      {c && (
        <div className="gfg-inherit-stats">
          Old version: {c.progress.done} / {c.progress.total} implemented, {c.progress.question} questions, {c.progress.review} needing review.{' '}
          <strong>{c.inheritable}</strong> Map Instruction(s) can be inherited; <strong>{c.review}</strong> of them changed since they were marked
          and will be flagged "Needs review".{' '}
          {c.missing > 0 && <>{c.missing} marked instruction(s) of the old version have no match in this one. </>}
          Field path overlap between the two versions: {Math.round(c.overlap * 100)}%.
        </div>
      )}
    </div>
  )
}

function MappingCard({ m, index, asked, onSave }) {
  const st = mStatus(m)
  const saved = m.progress?.note || ''
  const [note, setNote] = useState(saved)
  const [noteOpen, setNoteOpen] = useState(!!saved)
  useEffect(() => setNote(saved), [saved])
  const prev = m.progress?.previous

  return (
    <div className={`gfg-card${asked ? ' asked' : ''}`}>
      <div className="gfg-card-head">
        <span className="gfg-no">#{m.nr || index + 1}</span>
        {m.flag && <span className="gfg-flag">{m.flag}</span>}
        {asked && <span className="gfg-qbadge open">❓ has a question</span>}
        <span className="edx-spacer" />
        <div className="gfg-status-buttons compact">
          {['todo', 'done', 'question'].map((s) => (
            <button
              key={s}
              className={st === s || (s === 'done' && st === 'review') ? `active ${st === 'review' ? 'review' : s}` : ''}
              onClick={() => onSave({ status: s === 'todo' ? '' : s }, m.key)}
            >
              {STATUS_ICON[s]} {STATUS_LABEL[s]}
            </button>
          ))}
          <button className={saved ? 'has-note' : ''} title="Note" onClick={() => setNoteOpen((o) => !o)}>📝</button>
        </div>
      </div>
      {st === 'review' && (
        <div className="gfg-review">
          ⚠️ This instruction changed after it was marked as implemented. If nothing needs to change,
          click "Implemented" to confirm it again.
          {prev && (
            <details>
              <summary>Show the content at the time it was marked</summary>
              <Fields fields={[['Condition', prev.condition], ['Instruction', prev.instruction], ['Additional Info', prev.additional_info]]} />
            </details>
          )}
        </div>
      )}
      <Fields fields={[
        ['Condition', m.condition],
        ['Instruction', m.instruction],
        ['Additional Info', m.additional_info],
      ]} />
      {(noteOpen || saved) && (
        <textarea
          className="gfg-note"
          placeholder={st === 'question' ? 'Write the question: who to ask and what to confirm…' : 'Note (optional)'}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => note.trim() !== saved && onSave({ note }, m.key)}
          rows={2}
        />
      )}
      {m.progress?.updated_at && (
        <div className="gfg-updated">Updated {new Date(m.progress.updated_at + 'Z').toLocaleString()}</div>
      )}
    </div>
  )
}

function NodeDetail({ node, changes, onSave, pending, onMarkAll, questions, qActions, dtab, setDtab, hasDraft }) {
  const status = effStatus(node)
  const c = tally(node)
  const bodyRef = useRef(null)
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = 0 }, [node.seq, dtab])
  const waiting = openCount(questions)
  const asked = new Set(questions.filter((q) => q.status !== 'closed' && q.mapping_nr).map((q) => q.mapping_nr))

  return (
    <>
      <div className="gfg-detail-head">
        <h3 className="gfg-detail-title">
          <span className={`gfg-name ${node.kind}`}>{node.kind === 'attribute' ? '@' : ''}{node.name}</span>
          {changes.length > 0 && <span className="gfg-flag">Changed</span>}
        </h3>
        <div className="gfg-path">{node.path}</div>
        {status && (
          <div className="gfg-status-buttons">
            <span className="gfg-status-label" title="Derived from the status of its Map Instructions">
              {STATUS_ICON[status]} {c.done}/{c.total} implemented
              {c.question > 0 && ` · ❓ ${c.question}`}
              {c.review > 0 && ` · ⚠️ ${c.review}`}
            </span>
            <button disabled={c.done === c.total} onClick={() => onSave({ status: 'done' })}>✅ Mark all implemented</button>
            <button disabled={c.todo === c.total} onClick={() => onSave({ status: '' })}>○ Clear all</button>
          </div>
        )}
      </div>

      <div className="gfg-dtabs">
        <button className={dtab === 'details' ? 'active' : ''} onClick={() => setDtab('details')}>Details</button>
        <button className={dtab === 'maps' ? 'active' : ''} onClick={() => setDtab('maps')}>
          Map Instructions ({node.mappings.length})
        </button>
        <button className={dtab === 'questions' ? 'active' : ''} onClick={() => setDtab('questions')}>
          Questions ({questions.length}){waiting > 0 && <span className="gfg-qcount"> ❓{waiting}</span>}
          {hasDraft && <span className="gfg-draft" title="Unsent draft"> ✎ Draft</span>}
        </button>
      </div>

      <div className={`gfg-detail-body${dtab === 'questions' ? ' fill' : ''}`} ref={bodyRef}>
        {dtab === 'details' && (
          <>
            {node.mappings.length > 0 ? (
              <div className="gfg-map-summary">
                {node.mappings.map((m, i) => {
                  const st = mStatus(m)
                  return (
                    <div key={m.key} className="gfg-map-line" onClick={() => setDtab('maps')} title="Open Map Instructions">
                      <span className="gfg-no">#{m.nr || i + 1}</span>
                      <span className={`gfg-status ${st}`}>{STATUS_ICON[st]}</span>
                      <span className="gfg-map-text">{m.instruction || m.condition}</span>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="gfg-hint">This node has no Map Instructions, so there is nothing to implement and it is not counted in the progress.</div>
            )}
            {pending.length > 0 && (
              <button
                className="btn-outline btn-sm"
                onClick={() => window.confirm(`Mark ${pending.length} not-started Map Instruction(s) of the descendant nodes as implemented?`) && onMarkAll(pending)}
              >
                ✅ Mark {pending.length} not-started descendant instruction(s) as implemented
              </button>
            )}

            <Fields fields={[
              ['Kind', node.kind === 'attribute' ? 'Attribute' : 'Element'],
              ['Occurrence', node.kind === 'element' ? occurrenceText(node) : ''],
              ['Use', node.use],
              ['Type', node.type],
              ['PDF page', node.page ? String(node.page) : ''],
              ['General Notes', node.notes],
            ]} />

            {changes.length > 0 && (
              <>
                <h4 className="gfg-section">New Changes Summary</h4>
                {changes.map((c) => (
                  <div className="gfg-card" key={c.no + c.number}>
                    <div className="gfg-card-head">
                      <span className="gfg-no">{c.no}</span>
                      <span>Instruction {c.number}</span>
                      <span className="gfg-flag">{c.flag}</span>
                    </div>
                  </div>
                ))}
              </>
            )}
          </>
        )}

        {dtab === 'maps' && (
          <>
            {!node.mappings.length && <div className="gfg-empty">No Map Instructions</div>}
            {node.mappings.map((m, i) => (
              <MappingCard
                key={`${node.seq}-${m.key}`}
                m={m}
                index={i}
                asked={asked.has(m.nr || String(i + 1))}
                onSave={onSave}
              />
            ))}
          </>
        )}

        {dtab === 'questions' && <QuestionsSection key={node.seq} node={node} questions={questions} {...qActions} />}
      </div>
    </>
  )
}

export default GefegViewer
