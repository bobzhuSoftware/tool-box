import { useEffect, useMemo, useState } from 'react'

// Built-in categories used when the user hasn't customised their layout yet.
const BUILTIN_CATEGORIES = [
  { id: 'tools', label: '工具' },
  { id: 'scripts', label: '脚本工具' },
]

// Stable fallback id for tools without an explicit assignment. Must NOT depend
// on category order, otherwise reordering categories would drag unassigned tools.
const DEFAULT_CATEGORY_ID = 'tools'

function HomePage({ onSelectTool, token }) {
  const [query, setQuery] = useState('')
  const [activeCat, setActiveCat] = useState('tools')
  const [categories, setCategories] = useState(BUILTIN_CATEGORIES)
  const [assignments, setAssignments] = useState({}) // toolId -> categoryId
  const [editMode, setEditMode] = useState(false)
  const [dragToolId, setDragToolId] = useState(null)
  const [dropCat, setDropCat] = useState(null)
  const tools = [
    {
      id: 'transcript',
      icon: '🎬',
      title: 'Video Transcript',
      description:
        'Generate transcripts from YouTube & Bilibili videos or local audio/video files using AI speech recognition.',
      tags: ['YouTube', 'Bilibili', 'Whisper AI'],
    },
    {
      id: 'subtitle',
      icon: '📝',
      title: '字幕处理',
      description:
        '上传已有的字幕文件（VTT / SRT / TXT），转换为纯文本、带时间戳文本，或按分钟拆分的多文件 ZIP 下载。',
      tags: ['VTT', 'SRT', '字幕', 'TXT'],
    },
    {
      id: 'webtopdf',
      icon: '🌐',
      title: 'Web → PDF（智能提取正文）',
      description:
        '输入任意网页 URL，自动智能提取正文与图片（去除广告、导航和杂乱内容），生成干净易读的 PDF。支持登录态抓取 X / Twitter 文章。',
      tags: ['PDF', 'Web', 'Readability', 'X/Twitter'],
    },
    {
      id: 'dsvpdf',
      icon: '🏢',
      title: 'DSV Page to PDF',
      description:
        'Unwraps a DSV ServiceNow frame URL to the bare page so you can open it in your signed-in Edge and print to PDF (Ctrl+P).',
      tags: ['PDF', 'DSV', 'Internal'],
    },
    {
      id: 'teams',
      icon: '📋',
      title: 'Teams Transcript',
      description:
        'Paste a Teams recording URL and download the meeting transcript as a clean VTT file — no manual steps needed.',
      tags: ['Teams', 'SharePoint', 'VTT'],
    },
    {
      id: 'teamschat',
      icon: '💼',
      title: 'Teams 聊天记录导出',
      description:
        '通过已登录的 Edge 会话抓取 Teams 网页版聊天记录，选择聊天后导出为 HTML/TXT 文件。需要已在 Edge 登录 Teams。',
      tags: ['Teams', '聊天记录', 'Export'],
    },
    {
      id: 'copilotchat',
      icon: '🤖',
      title: 'Copilot 对话导出',
      description:
        '粘贴一条 Microsoft 365 Copilot 对话链接，通过已登录的 Edge 会话抓取整段对话并导出为 HTML/TXT。需要已在 Edge 登录 Microsoft 365。',
      tags: ['Copilot', 'M365', 'Export'],
    },
    {
      id: 'bookconvert',
      icon: '📚',
      title: 'Book Format Converter',
      description:
        'Convert books between PDF and EPUB formats. Upload a file and download the converted version instantly.',
      tags: ['PDF', 'EPUB', 'eBook'],
    },
    {
      id: 'wechat',
      icon: '💬',
      title: '微信聊天记录导出',
      description:
        '从本地微信中提取聊天记录，选择联系人或群聊后导出为 TXT 文件下载。需要微信正在运行。',
      tags: ['WeChat', '聊天记录', 'Export'],
    },
    {
      id: 'discord',
      icon: '🎮',
      title: 'Discord 聊天记录导出',
      description:
        '导出 Discord 服务器频道的聊天记录为 HTML 文件。粘贴频道 URL 和 Token 即可开始导出。',
      tags: ['Discord', 'Chat Export', 'HTML'],
    },
    {
      id: 'threads',
      icon: '🧵',
      title: 'Threads 视频下载',
      description:
        '粘贴一个 Threads 帖子链接，把视频下载到本地。支持单个或多视频轮播，仅适用于公开帖子。',
      tags: ['Threads', 'Video', 'Download'],
    },
    {
      id: 'servicenow',
      icon: '📊',
      title: 'ServiceNow 数据表',
      description:
        '通过已登录的 Edge 会话读取 ServiceNow 列表的记录数，按行/列填入表格，一键刷新并复制。首次需在弹出的窗口中登录（含 2FA）。',
      tags: ['ServiceNow', 'DSV', 'Table'],
    },
    {
      id: 'audio',
      icon: '🎙️',
      title: '全声道录音',
      description:
        '录制电脑扬声器输出的全部声音（含 Teams/会议、视频、音乐等），可同时混入麦克风，结束后导出为 WAV / MP3。仅本机可用。',
      tags: ['录音', '系统音频', '会议'],
    },
    {
      id: 'screen',
      icon: '🎬',
      title: '窗口录屏',
      description:
        '录制单个窗口的画面，并同时录入电脑全部声音（含 Teams/会议、视频等），可混入麦克风，导出为带声音的 MP4。仅本机可用。',
      tags: ['录屏', '窗口', '会议', 'MP4'],
    },
    {
      id: 'excelsearch',
      category: 'scripts',
      icon: '🔎',
      title: 'Excel 字符串定位',
      description:
        '给定一个字符串和一个文件夹路径，查找它在该路径（含子文件夹）下所有 Excel 各 sheet 中出现的位置（文件名 / sheet / 单元格）。',
      tags: ['Excel', 'openpyxl', '脚本', '查找'],
    },
    {
      id: 'filecompare',
      category: 'scripts',
      icon: '🔍',
      title: '文件对比',
      description:
        '上传两个纯文本文件，并排（side-by-side）查看逐行差异，高亮显示新增、删除与修改的内容。',
      tags: ['Diff', '对比', '文本', 'difflib'],
    },
    {
      id: 'timezone',
      icon: '🌍',
      title: '时区转换',
      description:
        '在两个时区之间快速换算时间（如北京 ↔ 柏林 / 孟买），可选附加列出更多时区的当地时间，方便安排跨时区会议。',
      tags: ['时区', 'Timezone', '会议'],
    },
    {
      id: 'richtext2html',
      icon: '🧬',
      title: '富文本转 HTML',
      description:
        '复制任意富文本后在页面按 Ctrl+V，右侧直接显示剪贴板里的原始 HTML 源码，无需再到浏览器控制台注入监听代码。支持预览、美化缩进、复制与下载。',
      tags: ['HTML', '剪贴板', '富文本'],
    },
    {
      id: 'sessionreader',
      icon: '📖',
      title: 'Copilot 会话阅读器',
      description:
        '把 VS Code Copilot 的 JSONL 聊天记录解析成清晰易读的对话界面：区分用户/助手气泡、可折叠的思考过程与工具调用，支持搜索与导出 Markdown。',
      tags: ['Copilot', '聊天记录', '阅读', 'Markdown'],
    },
  ]

  // 分区定义：现有功能归入「工具」，脚本类工具放入「脚本工具」。
  // 新增脚本工具时，给该 tool 对象加 category: 'scripts' 即可（作为默认归类）。
  const emptyHints = {
    scripts: '脚本类工具将放在这里（例如 Excel ProcessID 交叉比对）。',
  }

  // Load the user's saved layout on mount.
  useEffect(() => {
    if (!token) return
    fetch('/api/home-layout', { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data) return
        if (Array.isArray(data.categories) && data.categories.length > 0) {
          setCategories(data.categories)
        }
        if (data.assignments && typeof data.assignments === 'object') {
          setAssignments(data.assignments)
        }
      })
      .catch(() => {})
  }, [token])

  // Persist the layout to the backend (fire-and-forget).
  const persist = (nextCategories, nextAssignments) => {
    if (!token) return
    fetch('/api/home-layout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ categories: nextCategories, assignments: nextAssignments }),
    }).catch(() => {})
  }

  const catIds = useMemo(() => new Set(categories.map((c) => c.id)), [categories])

  // Resolve which category a tool belongs to, falling back gracefully.
  const resolveCat = (tool) => {
    const a = assignments[tool.id]
    if (a && catIds.has(a)) return a
    if (tool.category && catIds.has(tool.category)) return tool.category
    if (catIds.has(DEFAULT_CATEGORY_ID)) return DEFAULT_CATEGORY_ID
    return categories[0]?.id
  }

  // Give every tool an explicit assignment so reordering categories can never
  // move unassigned tools around (they'd otherwise follow the positional fallback).
  const enterEditMode = () => {
    const next = { ...assignments }
    tools.forEach((t) => {
      if (!next[t.id]) next[t.id] = resolveCat(t)
    })
    setAssignments(next)
    persist(categories, next)
    setEditMode(true)
    setQuery('')
  }

  // Keep the active tab valid if categories change.
  useEffect(() => {
    if (categories.length && !catIds.has(activeCat)) {
      setActiveCat(categories[0].id)
    }
  }, [categories, catIds, activeCat])

  // While dragging a card, auto-scroll the window near the top/bottom edges so a
  // card in the middle of a long category can still reach any other category.
  useEffect(() => {
    if (!dragToolId) return
    const EDGE = 100 // px from a viewport edge where auto-scroll kicks in
    const MAX_SPEED = 20 // px per frame at the very edge
    let speed = 0
    let raf = 0
    const step = () => {
      if (speed === 0) {
        raf = 0
        return
      }
      window.scrollBy(0, speed)
      raf = requestAnimationFrame(step)
    }
    const onDragOver = (e) => {
      const y = e.clientY
      const h = window.innerHeight
      if (y < EDGE) {
        speed = -Math.ceil(((EDGE - y) / EDGE) * MAX_SPEED)
      } else if (y > h - EDGE) {
        speed = Math.ceil(((y - (h - EDGE)) / EDGE) * MAX_SPEED)
      } else {
        speed = 0
      }
      if (speed !== 0 && !raf) raf = requestAnimationFrame(step)
    }
    document.addEventListener('dragover', onDragOver)
    return () => {
      document.removeEventListener('dragover', onDragOver)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [dragToolId])

  const q = query.trim().toLowerCase()
  const matchesQuery = (t) =>
    !q ||
    t.title.toLowerCase().includes(q) ||
    t.description.toLowerCase().includes(q) ||
    t.tags.some((tag) => tag.toLowerCase().includes(q))

  const toolsInCategory = (catId, applyQuery = true) =>
    tools.filter((t) => resolveCat(t) === catId && (!applyQuery || matchesQuery(t)))

  // --- Category management ---
  const addCategory = () => {
    const id = `cat_${Date.now().toString(36)}`
    const next = [...categories, { id, label: '新分类' }]
    setCategories(next)
    persist(next, assignments)
  }

  const renameCategory = (id, label) => {
    const next = categories.map((c) => (c.id === id ? { ...c, label } : c))
    setCategories(next)
  }

  const deleteCategory = (id) => {
    if (categories.length <= 1) return
    const remaining = categories.filter((c) => c.id !== id)
    const fallback = remaining[0].id
    // Reassign every tool currently resolving to the deleted category.
    const nextAssignments = { ...assignments }
    tools.forEach((t) => {
      if (resolveCat(t) === id) nextAssignments[t.id] = fallback
    })
    setCategories(remaining)
    setAssignments(nextAssignments)
    persist(remaining, nextAssignments)
  }

  const moveCategory = (id, dir) => {
    const idx = categories.findIndex((c) => c.id === id)
    const swap = idx + dir
    if (idx < 0 || swap < 0 || swap >= categories.length) return
    const next = [...categories]
    ;[next[idx], next[swap]] = [next[swap], next[idx]]
    setCategories(next)
    persist(next, assignments)
  }

  const assignTool = (toolId, catId) => {
    if (!catId || resolveCat(tools.find((t) => t.id === toolId)) === catId) return
    const next = { ...assignments, [toolId]: catId }
    setAssignments(next)
    persist(categories, next)
  }

  const renderToolCard = (tool) => (
    <div
      key={tool.id}
      className={`tool-card${editMode ? ' tool-card-editing' : ''}${
        dragToolId === tool.id ? ' tool-card-dragging' : ''
      }`}
      onClick={() => !editMode && onSelectTool(tool.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => !editMode && e.key === 'Enter' && onSelectTool(tool.id)}
      draggable={editMode}
      onDragStart={(e) => {
        if (!editMode) return
        setDragToolId(tool.id)
        e.dataTransfer.effectAllowed = 'move'
        e.dataTransfer.setData('text/plain', tool.id)
      }}
      onDragEnd={() => {
        setDragToolId(null)
        setDropCat(null)
      }}
    >
      {editMode && <div className="tool-card-grip" title="拖动到其他分类">⠿</div>}
      <div className="tool-card-icon">{tool.icon}</div>
      <div className="tool-card-body">
        <h3>{tool.title}</h3>
        <p>{tool.description}</p>
        <div className="tool-tags">
          {tool.tags.map((tag) => (
            <span key={tag} className="tool-tag">
              {tag}
            </span>
          ))}
        </div>
      </div>
      {!editMode && <div className="tool-card-arrow">→</div>}
    </div>
  )

  return (
    <div className="home-page">
      <div className="home-intro">
        <p>Choose a tool to get started.</p>
      </div>

      <div className="home-toolbar">
        {!editMode && (
          <div className="home-search-bar">
            <span className="home-search-icon">🔍</span>
            <input
              type="text"
              className="home-search-input"
              placeholder="Filter tools..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button className="home-search-clear" onClick={() => setQuery('')} aria-label="Clear">
                ✕
              </button>
            )}
          </div>
        )}
        <button
          className={`btn-outline btn-sm home-edit-btn${editMode ? ' active' : ''}`}
          onClick={() => {
            if (editMode) {
              setEditMode(false)
            } else {
              enterEditMode()
            }
          }}
        >
          {editMode ? '✓ 完成' : '✎ 管理分类'}
        </button>
      </div>

      {editMode ? (
        <div className="category-editor">
          <p className="category-editor-hint">
            拖动工具卡片到任意分类即可归类。可新建、重命名、删除或调整分类顺序。
          </p>
          {categories.map((cat, i) => {
            const items = toolsInCategory(cat.id, false)
            return (
              <div
                key={cat.id}
                className={`category-section${dropCat === cat.id ? ' drag-over' : ''}`}
                onDragOver={(e) => {
                  if (!dragToolId) return
                  e.preventDefault()
                  e.dataTransfer.dropEffect = 'move'
                  if (dropCat !== cat.id) setDropCat(cat.id)
                }}
                onDragLeave={(e) => {
                  if (!e.currentTarget.contains(e.relatedTarget)) setDropCat(null)
                }}
                onDrop={(e) => {
                  e.preventDefault()
                  const id = dragToolId || e.dataTransfer.getData('text/plain')
                  if (id) assignTool(id, cat.id)
                  setDragToolId(null)
                  setDropCat(null)
                }}
              >
                <div className="category-section-head">
                  <input
                    className="category-name-input"
                    value={cat.label}
                    onChange={(e) => renameCategory(cat.id, e.target.value)}
                    onBlur={() => persist(categories, assignments)}
                  />
                  <span className="category-section-count">{items.length}</span>
                  <div className="category-section-actions">
                    <button
                      className="cat-icon-btn"
                      disabled={i === 0}
                      onClick={() => moveCategory(cat.id, -1)}
                      title="上移"
                    >
                      ↑
                    </button>
                    <button
                      className="cat-icon-btn"
                      disabled={i === categories.length - 1}
                      onClick={() => moveCategory(cat.id, 1)}
                      title="下移"
                    >
                      ↓
                    </button>
                    <button
                      className="cat-icon-btn cat-icon-danger"
                      disabled={categories.length <= 1}
                      onClick={() => deleteCategory(cat.id)}
                      title="删除分类"
                    >
                      🗑
                    </button>
                  </div>
                </div>
                {items.length === 0 ? (
                  <p className="category-drop-empty">把工具拖到这里</p>
                ) : (
                  <div className="tools-grid">{items.map(renderToolCard)}</div>
                )}
              </div>
            )
          })}
          <button className="category-add-btn" onClick={addCategory}>
            + 新建分类
          </button>
        </div>
      ) : (
        <>
          <div className="category-tabs">
            {categories.map((cat) => {
              const count = toolsInCategory(cat.id).length
              return (
                <button
                  key={cat.id}
                  className={`category-tab${activeCat === cat.id ? ' active' : ''}`}
                  onClick={() => setActiveCat(cat.id)}
                >
                  {cat.label}
                  <span className="category-tab-count">{count}</span>
                </button>
              )
            })}
          </div>
          {(() => {
            const cat = categories.find((c) => c.id === activeCat) || categories[0]
            if (!cat) return null
            const items = toolsInCategory(cat.id)
            if (items.length === 0) {
              return (
                <p className="category-empty">
                  {q ? `No tools match \u201c${query}\u201d.` : emptyHints[cat.id] || '暂无工具。'}
                </p>
              )
            }
            return <div className="tools-grid">{items.map(renderToolCard)}</div>
          })()}
        </>
      )}
    </div>
  )
}

export default HomePage
