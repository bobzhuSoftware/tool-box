import { useState } from 'react'

function HomePage({ onSelectTool }) {
  const [query, setQuery] = useState('')
  const [activeCat, setActiveCat] = useState('tools')
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
      id: 'sessionreader',
      icon: '📖',
      title: 'Copilot 会话阅读器',
      description:
        '把 VS Code Copilot 的 JSONL 聊天记录解析成清晰易读的对话界面：区分用户/助手气泡、可折叠的思考过程与工具调用，支持搜索与导出 Markdown。',
      tags: ['Copilot', '聊天记录', '阅读', 'Markdown'],
    },
  ]

  // 分区定义：现有功能归入「工具」，脚本类工具放入「脚本工具」。
  // 新增脚本工具时，给该 tool 对象加 category: 'scripts' 即可。
  const categories = [
    { id: 'tools', label: '工具' },
    {
      id: 'scripts',
      label: '脚本工具',
      emptyHint: '脚本类工具将放在这里（例如 Excel ProcessID 交叉比对）。',
    },
  ]

  const q = query.trim().toLowerCase()
  const matchesQuery = (t) =>
    !q ||
    t.title.toLowerCase().includes(q) ||
    t.description.toLowerCase().includes(q) ||
    t.tags.some((tag) => tag.toLowerCase().includes(q))

  const toolsInCategory = (catId) =>
    tools.filter((t) => (t.category || 'tools') === catId && matchesQuery(t))


  return (
    <div className="home-page">
      <div className="home-intro">
        <p>Choose a tool to get started.</p>
      </div>
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
        const items = toolsInCategory(cat.id)
        if (items.length === 0) {
          return (
            <p className="category-empty">
              {q ? `No tools match \u201c${query}\u201d.` : cat.emptyHint || '暂无工具。'}
            </p>
          )
        }
        return (
          <div className="tools-grid">
            {items.map((tool) => (
              <div
                key={tool.id}
                className="tool-card"
                onClick={() => onSelectTool(tool.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && onSelectTool(tool.id)}
              >
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
                <div className="tool-card-arrow">→</div>
              </div>
            ))}
          </div>
        )
      })()}
    </div>
  )
}

export default HomePage
