import { useEffect, useMemo, useState } from 'react'
import './SessionReader.css'

// ---------------------------------------------------------------------------
// Minimal content renderer: splits text into fenced code blocks (```) and
// normal prose. Prose keeps line breaks via CSS white-space: pre-wrap. This
// avoids pulling in a full markdown dependency while staying readable.
// ---------------------------------------------------------------------------
function renderContent(text) {
  if (!text) return null
  const parts = []
  const re = /```(\w*)\n?([\s\S]*?)```/g
  let last = 0
  let m
  let key = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      parts.push(
        <p className="sr-prose" key={key++}>{text.slice(last, m.index)}</p>
      )
    }
    parts.push(
      <pre className="sr-code" key={key++}>
        {m[1] && <span className="sr-code-lang">{m[1]}</span>}
        <code>{m[2].replace(/\n$/, '')}</code>
      </pre>
    )
    last = re.lastIndex
  }
  if (last < text.length) {
    parts.push(<p className="sr-prose" key={key++}>{text.slice(last)}</p>)
  }
  return parts
}

function formatArgs(raw) {
  if (!raw) return ''
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return String(raw)
  }
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString()
}

function relDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleDateString()
}

// A single collapsible tool-call chip.
function ToolCall({ tool }) {
  const [open, setOpen] = useState(false)
  const ok = tool.success
  const badge = ok === true ? '✓' : ok === false ? '✕' : '·'
  const cls = ok === true ? 'ok' : ok === false ? 'fail' : 'unknown'
  return (
    <div className={`sr-tool ${cls}`}>
      <button className="sr-tool-head" onClick={() => setOpen((o) => !o)}>
        <span className={`sr-tool-badge ${cls}`}>{badge}</span>
        <span className="sr-tool-name">{tool.name}</span>
        <span className="sr-tool-toggle">{open ? '收起' : '参数'}</span>
      </button>
      {open && <pre className="sr-tool-args"><code>{formatArgs(tool.arguments)}</code></pre>}
    </div>
  )
}

// A single assistant beat (reasoning + content + tools).
function AssistantItem({ item }) {
  const [showReason, setShowReason] = useState(false)
  return (
    <div className="sr-item sr-assistant">
      <div className="sr-avatar sr-avatar-ai">AI</div>
      <div className="sr-bubble">
        {item.reasoning && (
          <div className="sr-reason">
            <button className="sr-reason-toggle" onClick={() => setShowReason((s) => !s)}>
              {showReason ? '▼' : '▶'} 思考过程
            </button>
            {showReason && <div className="sr-reason-body">{renderContent(item.reasoning)}</div>}
          </div>
        )}
        {item.content && <div className="sr-content">{renderContent(item.content)}</div>}
        {item.tools?.length > 0 && (
          <div className="sr-tools">
            {item.tools.map((t, i) => (
              <ToolCall key={i} tool={t} />
            ))}
          </div>
        )}
        {item.timestamp && <div className="sr-time">{formatTime(item.timestamp)}</div>}
      </div>
    </div>
  )
}

function UserItem({ item }) {
  return (
    <div className="sr-item sr-user">
      <div className="sr-bubble">
        <div className="sr-content">{renderContent(item.content)}</div>
        {item.attachments?.length > 0 && (
          <div className="sr-attachments">
            📎 {item.attachments.length} 个附件
          </div>
        )}
        {item.timestamp && <div className="sr-time">{formatTime(item.timestamp)}</div>}
      </div>
      <div className="sr-avatar sr-avatar-user">你</div>
    </div>
  )
}

function SessionReader({ token, onAuthError }) {
  const [sessions, setSessions] = useState([])
  const [loadingList, setLoadingList] = useState(false)
  const [listError, setListError] = useState('')
  const [query, setQuery] = useState('')
  const [roots, setRoots] = useState([]) // folders currently scanned
  const [customRoot, setCustomRoot] = useState('') // user-specified scan folder
  const [filePath, setFilePath] = useState('') // user-specified .jsonl to open
  const [showAdvanced, setShowAdvanced] = useState(false)

  const [activeId, setActiveId] = useState(null)
  const [convo, setConvo] = useState(null) // { meta, items }
  const [loadingConvo, setLoadingConvo] = useState(false)
  const [convoError, setConvoError] = useState('')
  const [dragOver, setDragOver] = useState(false)

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const loadSessions = async () => {
    setListError('')
    setLoadingList(true)
    try {
      const cr = customRoot.trim()
      const url = cr ? `/api/sessions?root=${encodeURIComponent(cr)}` : '/api/sessions'
      const res = await fetch(url, { headers: authHeaders() })
      if (res.status === 401) return onAuthError && onAuthError()
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '加载失败')
      setSessions(data.sessions || [])
      setRoots(data.roots || [])
    } catch (err) {
      setListError(err.message)
    } finally {
      setLoadingList(false)
    }
  }

  useEffect(() => {
    loadSessions()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const openSession = async (sid) => {
    setActiveId(sid)
    setConvo(null)
    setConvoError('')
    setLoadingConvo(true)
    try {
      const cr = customRoot.trim()
      const url = cr
        ? `/api/sessions/${encodeURIComponent(sid)}?root=${encodeURIComponent(cr)}`
        : `/api/sessions/${encodeURIComponent(sid)}`
      const res = await fetch(url, { headers: authHeaders() })
      if (res.status === 401) return onAuthError && onAuthError()
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '加载失败')
      setConvo(data)
    } catch (err) {
      setConvoError(err.message)
    } finally {
      setLoadingConvo(false)
    }
  }

  // Open an arbitrary .jsonl transcript by absolute file path.
  const openFileByPath = async () => {
    const p = filePath.trim()
    if (!p) return
    setActiveId(`file:${p}`)
    setConvo(null)
    setConvoError('')
    setLoadingConvo(true)
    try {
      const res = await fetch('/api/sessions/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ path: p }),
      })
      if (res.status === 401) return onAuthError && onAuthError()
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '加载失败')
      setConvo(data)
    } catch (err) {
      setConvoError(err.message)
    } finally {
      setLoadingConvo(false)
    }
  }

  // Parse a dropped/selected File object (reads content client-side, then
  // sends the raw JSONL text to the backend for consistent parsing).
  const openDroppedFile = async (file) => {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.jsonl')) {
      setConvoError('请拖入 .jsonl 会话文件')
      setActiveId(`drop:${file.name}`)
      setConvo(null)
      return
    }
    setActiveId(`drop:${file.name}`)
    setConvo(null)
    setConvoError('')
    setLoadingConvo(true)
    try {
      const text = await file.text()
      const res = await fetch('/api/sessions/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ content: text, filename: file.name }),
      })
      if (res.status === 401) return onAuthError && onAuthError()
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '解析失败')
      setConvo(data)
    } catch (err) {
      setConvoError(err.message)
    } finally {
      setLoadingConvo(false)
    }
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer?.files?.[0]
    if (file) openDroppedFile(file)
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    if (!dragOver) setDragOver(true)
  }

  const handleDragLeave = (e) => {
    // Only clear when leaving the drop container itself, not its children.
    if (e.currentTarget === e.target) setDragOver(false)
  }

  const exportMarkdown = () => {
    if (!convo) return
    const lines = [`# ${convo.meta.title || 'Copilot 会话'}`, '']
    for (const it of convo.items) {
      if (it.kind === 'user') {
        lines.push('## 🧑 用户', '', it.content, '')
      } else {
        lines.push('## 🤖 助手', '')
        if (it.reasoning) lines.push('> 思考过程：', ...it.reasoning.split('\n').map((l) => `> ${l}`), '')
        if (it.content) lines.push(it.content, '')
        for (const t of it.tools || []) {
          lines.push(`- 🔧 \`${t.name}\`${t.success === false ? ' (失败)' : ''}`)
        }
        if (it.tools?.length) lines.push('')
      }
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${(convo.meta.title || 'session').slice(0, 40).replace(/[\\/:*?"<>|]/g, '')}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return sessions
    return sessions.filter(
      (s) =>
        (s.title || '').toLowerCase().includes(q) ||
        (s.session_id || '').toLowerCase().includes(q)
    )
  }, [sessions, query])

  return (
    <div className="session-reader">
      <aside className="sr-sidebar">
        <div className="sr-sidebar-head">
          <h3>会话列表 <span className="sr-count">{sessions.length}</span></h3>
          <button className="btn-outline btn-sm" onClick={loadSessions} disabled={loadingList}>
            {loadingList ? '…' : '刷新'}
          </button>
        </div>
        <button
          className="sr-adv-toggle"
          onClick={() => setShowAdvanced((s) => !s)}
        >
          {showAdvanced ? '▼' : '▶'} 自定义路径 / 打开指定文件
        </button>
        {showAdvanced && (
          <div className="sr-adv">
            <label className="sr-adv-label">扫描文件夹（留空 = 默认位置）</label>
            <div className="sr-adv-row">
              <input
                className="sr-adv-input"
                type="text"
                placeholder="例如 …\GitHub.copilot-chat\transcripts"
                value={customRoot}
                onChange={(e) => setCustomRoot(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && loadSessions()}
              />
              <button className="btn-outline btn-sm" onClick={loadSessions} disabled={loadingList}>
                扫描
              </button>
            </div>

            <label className="sr-adv-label">直接打开某个 .jsonl 文件</label>
            <div className="sr-adv-row">
              <input
                className="sr-adv-input"
                type="text"
                placeholder="粘贴 .jsonl 文件的完整路径"
                value={filePath}
                onChange={(e) => setFilePath(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && openFileByPath()}
              />
              <button className="btn-outline btn-sm" onClick={openFileByPath} disabled={!filePath.trim()}>
                打开
              </button>
            </div>

            {roots.length > 0 && (
              <div className="sr-roots">
                <span className="sr-roots-label">当前扫描：</span>
                {roots.map((r, i) => (
                  <code key={i} className="sr-root-path">{r}</code>
                ))}
              </div>
            )}
          </div>
        )}
        <input
          className="sr-search"
          type="text"
          placeholder="搜索会话…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {listError && <p className="sr-error">{listError}</p>}
        <div className="sr-list">
          {loadingList && sessions.length === 0 && <p className="sr-muted">加载中…</p>}
          {!loadingList && filtered.length === 0 && <p className="sr-muted">没有会话</p>}
          {filtered.map((s) => (
            <button
              key={s.session_id}
              className={`sr-list-item${activeId === s.session_id ? ' active' : ''}`}
              onClick={() => openSession(s.session_id)}
            >
              <div className="sr-list-title">{s.title || '(无标题)'}</div>
              <div className="sr-list-meta">
                <span>{relDate(s.start_time)}</span>
                <span>💬 {s.user_count}</span>
                <span>🔧 {s.tool_count}</span>
              </div>
            </button>
          ))}
        </div>
      </aside>

      <main
        className={`sr-main${dragOver ? ' sr-dragover' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        {dragOver && (
          <div className="sr-drop-overlay">
            <div className="sr-drop-inner">
              <div className="sr-drop-icon">📥</div>
              <p>松开以读取此 .jsonl 会话文件</p>
            </div>
          </div>
        )}
        {!activeId && (
          <div className="sr-empty">
            <div className="sr-empty-icon">📖</div>
            <p>从左侧选择一个会话开始阅读</p>
            <p className="sr-empty-hint">或把 .jsonl 会话文件拖拽到这里</p>
          </div>
        )}
        {activeId && loadingConvo && <p className="sr-muted sr-pad">加载会话中…</p>}
        {activeId && convoError && <p className="sr-error sr-pad">{convoError}</p>}
        {activeId && convo && (
          <>
            <div className="sr-convo-head">
              <div className="sr-convo-title">
                <h2>{convo.meta.title || '会话'}</h2>
                <div className="sr-convo-sub">
                  {formatTime(convo.meta.start_time)}
                  {convo.meta.copilot_version && ` · Copilot ${convo.meta.copilot_version}`}
                  {convo.meta.vscode_version && ` · VS Code ${convo.meta.vscode_version}`}
                </div>
              </div>
              <button className="btn-outline btn-sm" onClick={exportMarkdown}>
                导出 Markdown
              </button>
            </div>
            <div className="sr-convo">
              {convo.items.map((it, i) =>
                it.kind === 'user' ? (
                  <UserItem key={i} item={it} />
                ) : (
                  <AssistantItem key={i} item={it} />
                )
              )}
              {convo.items.length === 0 && <p className="sr-muted sr-pad">该会话没有可显示的消息</p>}
            </div>
          </>
        )}
      </main>
    </div>
  )
}

export default SessionReader
