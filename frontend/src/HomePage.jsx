function HomePage({ onSelectTool }) {
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
      id: 'webtopdf',
      icon: '🌐',
      title: 'Web Page to PDF',
      description:
        'Enter any webpage URL and download a fully-rendered PDF to your device in seconds.',
      tags: ['PDF', 'Web', 'Download'],
    },
    {
      id: 'webtopdf2',
      icon: '📰',
      title: 'Article to PDF',
      description:
        'Extracts only the article text and images — removes ads, nav bars, and clutter — and saves as a clean readable PDF.',
      tags: ['PDF', 'Article', 'Readability'],
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
  ]

  return (
    <div className="home-page">
      <div className="home-intro">
        <p>Choose a tool to get started.</p>
      </div>
      <div className="tools-grid">
        {tools.map((tool) => (
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
    </div>
  )
}

export default HomePage
