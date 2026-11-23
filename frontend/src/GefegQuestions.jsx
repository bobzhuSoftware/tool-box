import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

const MAX_SIDE = 1600
const MAX_IMAGES = 6

// Downscale a screenshot and return it as a data URL (PNG, or JPEG when the PNG is large).
export function readImage(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = reject
    reader.onload = () => {
      const img = new Image()
      img.onerror = reject
      img.onload = () => {
        const scale = Math.min(1, MAX_SIDE / Math.max(img.width, img.height))
        const canvas = document.createElement('canvas')
        canvas.width = Math.max(1, Math.round(img.width * scale))
        canvas.height = Math.max(1, Math.round(img.height * scale))
        const ctx = canvas.getContext('2d')
        ctx.fillStyle = '#fff'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
        let out = canvas.toDataURL('image/png')
        if (out.length > 700000) out = canvas.toDataURL('image/jpeg', 0.85)
        resolve(out)
      }
      img.src = reader.result
    }
    reader.readAsDataURL(file)
  })
}

const STATUS_TEXT = { open: 'Waiting for BA', answered: 'BA replied', closed: 'Closed' }
const fmtTime = (s) => {
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString()
}

export const openCount = (list) => list.filter((q) => q.status === 'open').length

// ----- field references
// A reference is stored inside the message text as `@[Name](#seq)` so it needs no extra storage
// and also works in the exported HTML. The viewer provides the tree data and navigation here.
export const RefContext = createContext({
  nodes: [], bySeq: new Map(), selected: null, goto: () => {}, composer: null, setComposer: () => {},
})

const REF_RE = /@\[([^\]]+)\]\(#(\d+)\)/g
const refToken = (n) => `@[${n.name.replace(/[[\]]/g, '')}](#${n.seq})`
const nodeLabel = (n, bySeq) => {
  const parent = n.parent_seq != null ? bySeq.get(n.parent_seq) : null
  return `${parent ? `${parent.name} › ` : ''}${n.kind === 'attribute' ? '@' : ''}${n.name}`
}
const parentPath = (n) => {
  const parts = n.path.split('/').slice(0, -1)
  return (parts.length > 3 ? '… / ' : '') + parts.slice(-3).join(' / ')
}

function RefChip({ seq, name }) {
  const { bySeq, goto } = useContext(RefContext)
  const n = bySeq.get(seq)
  if (!n) return <span className="gfg-ref gone" title="This field is no longer in the guideline">@{name}</span>
  return (
    <button type="button" className="gfg-ref" title={`${n.path}\nClick to show this field`} onClick={() => goto(seq)}>
      {nodeLabel(n, bySeq)}
    </button>
  )
}

// Message text with its field references shown as clickable chips.
export function RefText({ text }) {
  const s = text || ''
  const parts = []
  let last = 0
  for (const m of s.matchAll(REF_RE)) {
    if (m.index > last) parts.push(s.slice(last, m.index))
    parts.push(<RefChip key={m.index} seq={Number(m[2])} name={m[1]} />)
    last = m.index + m[0].length
  }
  parts.push(s.slice(last))
  return <>{parts}</>
}

// Matches every word against the field path (so "terms loc" finds TermsOfDelivery/Location);
// name matches rank first and the field selected in the tree comes before the others.
function findFields(nodes, query, selected) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  const hits = []
  for (const n of nodes) {
    const name = n.name.toLowerCase()
    if (!words.every((w) => n.path.toLowerCase().includes(w))) continue
    let score = words.length && !words.every((w) => name.includes(w)) ? 2 : 1
    if (words.length && name.startsWith(words[0])) score = 0
    if (n.seq === selected) score -= 0.5
    hits.push([score, n])
  }
  return hits.sort((a, b) => a[0] - b[0]).slice(0, 8).map((h) => h[1])
}

// The "@word" being typed at the caret, if any (the @ must start the text or follow whitespace).
const detectMention = (value, caret) => {
  const before = value.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at < 0 || (at > 0 && !/\s/.test(before[at - 1]))) return null
  const query = before.slice(at + 1)
  return /[\n[\]]/.test(query) || query.length > 40 ? null : { start: at, query }
}

// ----- unsent drafts
// Drafts live in the viewer (not in the components) so they survive tab / field switches.
// A draft is { text, images, mappingNr, seq, qid?, base? }; `base` is the original content of a message being edited.
const DRAFTS_KEY = 'gfg-drafts'

export const newDraft = (meta, base) => ({
  text: base?.text || '', images: base?.images || [], mappingNr: '', base, ...meta,
})

export const isDirty = (d) => d.text !== (d.base?.text || '')
  || d.images.length !== (d.base?.images?.length || 0)
  || d.images.some((x, i) => x !== d.base?.images?.[i])

export function loadDrafts() {
  try { return JSON.parse(sessionStorage.getItem(DRAFTS_KEY)) || {} } catch { return {} }
}

// Only the text of new questions / follow-ups is kept across a page reload; screenshots and edits stay in memory.
export function saveDrafts(drafts) {
  const out = {}
  Object.entries(drafts).forEach(([k, d]) => {
    if (!d.base && d.text.trim()) out[k] = { ...d, images: [] }
  })
  try { sessionStorage.setItem(DRAFTS_KEY, JSON.stringify(out)) } catch { /* storage unavailable or full */ }
}

// Full-size preview rendered in the current page (data: URLs can't be opened in a new tab).
function Lightbox({ images, index, onIndex, onClose }) {
  const [actual, setActual] = useState(false)
  const many = images.length > 1

  const go = (step) => {
    setActual(false)
    onIndex((index + step + images.length) % images.length)
  }

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
      else if (many && e.key === 'ArrowLeft') go(-1)
      else if (many && e.key === 'ArrowRight') go(1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return createPortal(
    <div className="gfg-lightbox" onClick={(e) => { e.stopPropagation(); onClose() }}>
      <button type="button" className="gfg-lb-btn gfg-lb-close" title="Close (Esc)" onClick={onClose}>×</button>
      {many && (
        <button type="button" className="gfg-lb-btn gfg-lb-prev" title="Previous (←)"
          onClick={(e) => { e.stopPropagation(); go(-1) }}>‹</button>
      )}
      <div className={`gfg-lb-stage${actual ? ' actual' : ''}`}>
        <img
          src={images[index]}
          alt=""
          title={actual ? 'Click to fit screen' : 'Click for actual size'}
          onClick={(e) => { e.stopPropagation(); setActual((v) => !v) }}
        />
      </div>
      {many && (
        <button type="button" className="gfg-lb-btn gfg-lb-next" title="Next (→)"
          onClick={(e) => { e.stopPropagation(); go(1) }}>›</button>
      )}
      {many && <div className="gfg-lb-count">{index + 1} / {images.length}</div>}
    </div>,
    document.body,
  )
}

function Images({ images, onRemove }) {
  const [preview, setPreview] = useState(null)
  if (!images?.length) return null
  return (
    <div className="gfg-imgs">
      {images.map((src, i) => (
        <span className="gfg-img" key={i}>
          <img src={src} alt="" onClick={() => setPreview(i)} />
          {onRemove && <button type="button" className="gfg-img-x" onClick={() => onRemove(i)}>×</button>}
        </span>
      ))}
      {preview !== null && images[preview] && (
        <Lightbox images={images} index={preview} onIndex={setPreview} onClose={() => setPreview(null)} />
      )}
    </div>
  )
}

// Text box that accepts pasted / attached screenshots and `@` references to fields.
// Controlled by a draft kept in the viewer; the caller clears the draft once the submit succeeds.
function Composer({ draft, onDraft, placeholder, submitLabel, onSubmit, onError, rows = 3, header, children }) {
  const { text, images } = draft
  const { nodes, selected, setComposer } = useContext(RefContext)
  const [busy, setBusy] = useState(false)
  const [mention, setMention] = useState(null) // { start, query } while an @ is being typed
  const [active, setActive] = useState(0)
  const fileRef = useRef(null)
  const taRef = useRef(null)
  const caretRef = useRef(null) // [start, end] of the selection, kept while the box is not focused
  const pendingCaret = useRef(null)
  const textRef = useRef(text)
  textRef.current = text
  const patch = (fn) => onDraft((cur) => ({ ...cur, ...fn(cur) }))
  const setText = (t) => patch(() => ({ text: t }))
  const setImages = (fn) => patch((cur) => ({ images: fn(cur.images) }))

  const results = useMemo(() => (mention ? findFields(nodes, mention.query, selected) : []), [mention, nodes, selected])
  const refs = useMemo(() => {
    const seen = new Map()
    for (const m of text.matchAll(REF_RE)) if (!seen.has(m[2])) seen.set(m[2], m[1])
    return [...seen]
  }, [text])

  // put the caret after an insertion once the new text has been rendered
  useLayoutEffect(() => {
    const pos = pendingCaret.current
    if (pos == null || !taRef.current) return
    pendingCaret.current = null
    taRef.current.focus()
    taRef.current.setSelectionRange(pos, pos)
  })

  const selection = () => {
    const len = textRef.current.length
    const [s, e] = caretRef.current || [len, len]
    return [Math.min(s, len), Math.min(e, len)]
  }

  // Replaces `range` (default: the selection) with a reference to `n`.
  const insertRef = useRef()
  insertRef.current = (n, range) => {
    const [s, e] = range ? [range.start, range.end] : selection()
    const cur = textRef.current
    const before = cur.slice(0, s)
    const lead = !range && before && !/\s$/.test(before) ? ' ' : ''
    const head = `${before}${lead}${refToken(n)} `
    pendingCaret.current = head.length
    setText(head + cur.slice(e))
    setMention(null)
  }
  const api = useMemo(() => ({ insert: (n) => insertRef.current(n) }), [])
  useEffect(() => {
    setComposer((cur) => cur || api)
    return () => setComposer((cur) => (cur === api ? null : cur))
  }, [api, setComposer])

  const startMention = () => {
    const [s, e] = selection()
    const cur = textRef.current
    const before = cur.slice(0, s)
    const lead = before && !/\s$/.test(before) ? ' ' : ''
    const pos = before.length + lead.length + 1
    pendingCaret.current = pos
    setText(`${before}${lead}@${cur.slice(e)}`)
    setMention({ start: pos - 1, query: '' })
    setActive(0)
  }

  const updateMention = (el) => {
    caretRef.current = [el.selectionStart, el.selectionEnd]
    setMention(detectMention(el.value, el.selectionStart))
  }

  const onKeyDown = (e) => {
    if (!mention || !results.length || e.nativeEvent.isComposing) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => (i + 1) % results.length) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => (i - 1 + results.length) % results.length) }
    else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      insertRef.current(results[Math.min(active, results.length - 1)], { start: mention.start, end: e.target.selectionStart })
    } else if (e.key === 'Escape') { e.preventDefault(); setMention(null) }
  }

  const addFiles = async (files) => {
    const imgs = Array.from(files || []).filter((f) => f.type.startsWith('image/'))
    if (!imgs.length) return
    try {
      const urls = await Promise.all(imgs.slice(0, MAX_IMAGES - images.length).map(readImage))
      setImages((prev) => [...prev, ...urls].slice(0, MAX_IMAGES))
    } catch {
      onError?.('This image could not be read.')
    }
  }

  const submit = async () => {
    if (busy || (!text.trim() && !images.length)) return
    setBusy(true)
    try {
      await onSubmit({ text, images })
    } catch {
      // the caller already showed the error; keep the draft
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="gfg-composer">
      {header}
      <textarea
        ref={taRef}
        className="gfg-note"
        rows={rows}
        placeholder={placeholder}
        value={text}
        onChange={(e) => { setText(e.target.value); setActive(0); updateMention(e.target) }}
        onKeyDown={onKeyDown}
        onKeyUp={(e) => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) updateMention(e.target) }}
        onClick={(e) => updateMention(e.target)}
        onSelect={(e) => { caretRef.current = [e.target.selectionStart, e.target.selectionEnd] }}
        onFocus={() => setComposer(api)}
        onBlur={() => setMention(null)}
        onPaste={(e) => {
          const files = Array.from(e.clipboardData?.files || []).filter((f) => f.type.startsWith('image/'))
          if (files.length) { e.preventDefault(); addFiles(files) }
        }}
      />
      {mention && results.length > 0 && (
        <div className="gfg-mention" role="listbox">
          <div className="gfg-mention-hint">Reference a field · ↑↓ choose · Enter insert · Esc close</div>
          {results.map((n, i) => (
            <div
              key={n.seq}
              role="option"
              aria-selected={i === active}
              className={`gfg-mention-item${i === active ? ' active' : ''}`}
              onMouseDown={(e) => { e.preventDefault(); insertRef.current(n, { start: mention.start, end: selection()[1] }) }}
              onMouseEnter={() => setActive(i)}
            >
              <span className={`gfg-name ${n.kind}`}>{n.kind === 'attribute' ? '@' : ''}{n.name}</span>
              {n.seq === selected && <span className="gfg-mention-cur">selected</span>}
              <span className="gfg-mention-path">{parentPath(n)}</span>
            </div>
          ))}
        </div>
      )}
      {refs.length > 0 && (
        <div className="gfg-refs">
          <span>Referenced:</span>
          {refs.map(([seq, name]) => <RefChip key={seq} seq={Number(seq)} name={name} />)}
        </div>
      )}
      <Images images={images} onRemove={(i) => setImages((prev) => prev.filter((_, j) => j !== i))} />
      <div className="gfg-composer-acts">
        <button type="button" className="btn-outline btn-sm" onClick={() => fileRef.current?.click()}>🖼 Add image</button>
        <button type="button" className="btn-outline btn-sm" title="Reference a field (or type @)" onClick={startMention}>📍 Reference field</button>
        <input ref={fileRef} type="file" accept="image/*" multiple style={{ display: 'none' }}
          onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} />
        <span className="edx-spacer" />
        {children}
        <button type="button" className="edx-go" disabled={busy || (!text.trim() && !images.length)} onClick={submit}>
          {submitLabel}
        </button>
      </div>
    </div>
  )
}

const confirmDiscard = (d) => !isDirty(d) || window.confirm('Discard your unsent text?')

function Message({ m, q, editable, draft, setDraft, onEdit, onDelete, onError }) {
  const key = `edit:${m.id}`
  if (draft) {
    const done = () => setDraft(key, null)
    return (
      <div className="gfg-msg">
        <Composer
          rows={2}
          draft={draft}
          onDraft={(fn) => setDraft(key, fn)}
          placeholder="Edit your message…"
          submitLabel="Save"
          onError={onError}
          onSubmit={async (v) => { await onEdit(m, v); done() }}
        >
          <button type="button" className="btn-outline btn-sm" onClick={() => confirmDiscard(draft) && done()}>Cancel</button>
        </Composer>
      </div>
    )
  }
  return (
    <div className={`gfg-msg ${m.by}`}>
      <div className="gfg-msg-who">
        {m.by === 'ba' ? 'BA' : 'You'}
        <span className="gfg-updated">{fmtTime(m.at)}{m.edited_at ? ' (edited)' : ''}</span>
        {editable && (
          <span className="gfg-msg-acts">
            <button type="button" className="gfg-link" onClick={() => setDraft(key, newDraft({ seq: q.node_seq, qid: q.id }, { text: m.text, images: m.images || [] }))}>✎ Edit</button>
            <button type="button" className="gfg-link danger"
              onClick={() => window.confirm('Delete this message?') && onDelete(m)}>Delete</button>
          </span>
        )}
      </div>
      <div className="gfg-msg-text"><RefText text={m.text} /></div>
      <Images images={m.images} />
    </div>
  )
}

function Thread({ q, drafts, setDraft, onFollowUp, onEditMessage, onDeleteMessage, onStatus, onDelete, onError }) {
  const replyKey = `reply:${q.id}`
  const reply = drafts[replyKey]
  const closeReply = () => setDraft(replyKey, null)
  return (
    <div className={`gfg-thread ${q.status}`}>
      <div className="gfg-thread-head">
        <span className={`gfg-qstatus ${q.status}`}>{STATUS_TEXT[q.status]}</span>
        <span>{q.mapping_nr ? `About Map Instruction #${q.mapping_nr}` : 'About this field'}</span>
        <span className="edx-spacer" />
        {q.status === 'closed'
          ? <button className="btn-outline btn-sm" onClick={() => onStatus(q, 'open')}>Reopen</button>
          : <button className="btn-outline btn-sm" onClick={() => onStatus(q, 'closed')}>✔ Close</button>}
        {q.status !== 'closed' && (
          <button className="btn-outline btn-sm btn-danger" onClick={() => window.confirm('Delete this question and its replies?') && onDelete(q)}>Delete</button>
        )}
      </div>
      {q.messages.map((m) => (
        <Message
          key={m.id}
          m={m}
          q={q}
          draft={drafts[`edit:${m.id}`]}
          setDraft={setDraft}
          editable={m.by !== 'ba' && q.status !== 'closed'}
          onEdit={(msg, v) => onEditMessage(q, msg, v)}
          onDelete={(msg) => onDeleteMessage(q, msg)}
          onError={onError}
        />
      ))}
      {q.status !== 'closed' && (
        reply ? (
          <div className="gfg-msg">
            <Composer
              rows={2}
              draft={reply}
              onDraft={(fn) => setDraft(replyKey, fn)}
              placeholder="Follow-up for the BA…"
              submitLabel="Send follow-up"
              onError={onError}
              onSubmit={async (v) => { await onFollowUp(q, v); closeReply() }}
            >
              <button type="button" className="btn-outline btn-sm" onClick={() => confirmDiscard(reply) && closeReply()}>Cancel</button>
            </Composer>
          </div>
        ) : (
          <div className="gfg-msg">
            <button className="btn-outline btn-sm" onClick={() => setDraft(replyKey, newDraft({ seq: q.node_seq, qid: q.id }))}>↩ Follow up</button>
          </div>
        )
      )}
    </div>
  )
}

export function QuestionsSection({ node, questions, drafts, setDraft, onAsk, onFollowUp, onEditMessage, onDeleteMessage, onStatus, onDelete, onError }) {
  const askKey = `ask:${node.seq}`
  const ask = drafts[askKey]
  return (
    <div className="gfg-qwrap">
      <div className="gfg-qlist">
        {!questions.length && <div className="gfg-empty">No questions for this field yet.</div>}
        {questions.map((q) => (
          <Thread key={q.id} q={q} drafts={drafts} setDraft={setDraft} onFollowUp={onFollowUp} onEditMessage={onEditMessage} onDeleteMessage={onDeleteMessage} onStatus={onStatus} onDelete={onDelete} onError={onError} />
        ))}
      </div>
      <div className="gfg-qask">
        {ask ? (
          <Composer
            draft={ask}
            onDraft={(fn) => setDraft(askKey, fn)}
            placeholder="What is unclear? Describe the question for the BA… (Ctrl+V pastes a screenshot)"
            submitLabel="Add question"
            rows={3}
            onError={onError}
            onSubmit={async (v) => { await onAsk(node.seq, ask.mappingNr, v); setDraft(askKey, null) }}
            header={node.mappings.length > 0 && (
              <select className="gfg-select gfg-qtarget" value={ask.mappingNr} onChange={(e) => setDraft(askKey, (cur) => ({ ...cur, mappingNr: e.target.value }))}>
                <option value="">About the whole field</option>
                {node.mappings.map((m, i) => {
                  const nr = m.nr || String(i + 1)
                  return <option key={nr} value={nr}>About Map Instruction #{nr}</option>
                })}
              </select>
            )}
          >
            <button type="button" className="btn-outline btn-sm" onClick={() => confirmDiscard(ask) && setDraft(askKey, null)}>Cancel</button>
          </Composer>
        ) : (
          <button className="edx-go" onClick={() => setDraft(askKey, newDraft({ seq: node.seq }))}>❓ Ask the BA a question</button>
        )}
      </div>
    </div>
  )
}

const FILTERS = [['open', 'Waiting for BA'], ['answered', 'BA replied'], ['closed', 'Closed'], ['', 'All']]

export function QuestionsTab({ questions, bySeq, onGoto }) {
  const [filter, setFilter] = useState('')
  const shown = questions.filter((q) => !filter || q.status === filter)
  return (
    <div className="gfg-changes">
      <div className="gfg-toolbar">
        {FILTERS.map(([v, label]) => (
          <button key={v} className={`btn-outline btn-sm${filter === v ? ' active' : ''}`} onClick={() => setFilter(v)}>
            {label}{v ? ` (${questions.filter((q) => q.status === v).length})` : ` (${questions.length})`}
          </button>
        ))}
      </div>
      {!shown.length && <div className="gfg-empty">No questions here.</div>}
      {shown.map((q) => {
        const first = q.messages[0]
        const last = q.messages[q.messages.length - 1]
        return (
          <div className="gfg-card" key={q.id}>
            <div className="gfg-card-head">
              <span className={`gfg-qstatus ${q.status}`}>{STATUS_TEXT[q.status]}</span>
              <strong>{bySeq.get(q.node_seq)?.name}</strong>
              {q.mapping_nr && <span>#{q.mapping_nr}</span>}
              <span className="edx-spacer" />
              <button className="btn-outline btn-sm" onClick={() => onGoto(q.node_seq)}>Go to field</button>
            </div>
            <div className="gfg-path">{q.node_path}</div>
            <div className="gfg-msg-text"><RefText text={first?.text} /></div>
            {last && last !== first && (
              <div className="gfg-msg-text gfg-last"><strong>{last.by === 'ba' ? 'BA' : 'You'}:</strong> <RefText text={last.text} /></div>
            )}
          </div>
        )
      })}
    </div>
  )
}
