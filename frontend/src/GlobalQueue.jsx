import { useState } from 'react'
import { createPortal } from 'react-dom'
import './TeamsQueue.css'

const SOURCE_META = {
  teams:      { icon: '📋', label: 'Teams 字幕' },
  pdf:        { icon: '📄', label: 'Web PDF'   },
  transcript: { icon: '🎬', label: '视频字幕'  },
}

function shortenUrl(url) {
  try {
    const u = new URL(url)
    const parts = u.pathname.split('/').filter(Boolean)
    return decodeURIComponent(parts[parts.length - 1] || u.hostname)
  } catch {
    return url.length > 40 ? '…' + url.slice(-38) : url
  }
}

function statusIcon(status) {
  if (status === 'running') return '⏳'
  if (status === 'done')    return '✅'
  return '❌'
}

/**
 * Unified floating task-queue panel.
 *
 * Props:
 *   jobs        – combined array; each item must have a `source` field ('teams' | 'pdf')
 *   onOpenJob   – (job) => void  – called when user clicks any job (navigate to tool)
 *   onDeleteJob – (job) => void  – called to cancel/remove a job
 */
export default function GlobalQueue({ jobs, onOpenJob, onDeleteJob }) {
  const [open, setOpen] = useState(false)

  if (jobs.length === 0) return null

  const runningCount = jobs.filter(j => j.status === 'running').length

  function handleClick(job) {
    onOpenJob(job)
  }

  function clickHint(job) {
    if (job.source === 'pdf') {
      if (job.status === 'done')    return '点击跳转下载 PDF →'
      if (job.status === 'running') return '点击查看进度'
      return '点击查看详情'
    }
    if (job.status === 'done')    return '点击此处跳转下载 →'
    if (job.status === 'running') return '点击查看进度'
    return '点击查看详情'
  }

  return createPortal(
    <div className="tq-root">
      {open && (
        <div className="tq-panel">
          <div className="tq-panel-head">
            <span>📬 任务队列</span>
            <button className="tq-panel-close" onClick={() => setOpen(false)}>✕</button>
          </div>
          <div className="tq-list">
            {jobs.map(job => {
              const meta = SOURCE_META[job.source] || SOURCE_META.teams
              return (
                <div
                  key={job.job_id}
                  className={`tq-item tq-item--${job.status}`}
                  onClick={() => handleClick(job)}
                  title={clickHint(job)}
                >
                  <div className="tq-item-top">
                    <span className="tq-status-icon">{statusIcon(job.status)}</span>
                    <span className="tq-item-source">{meta.icon}</span>
                    <span className="tq-item-name">
                      {job.source === 'teams'
                        ? (job.result?.name || shortenUrl(job.url))
                        : job.source === 'transcript'
                          ? (job.result?.title || job.label || shortenUrl(job.url || ''))
                          : (job.result?.title || shortenUrl(job.url))}
                    </span>
                    <button
                      className={`tq-item-del${job.status === 'running' ? ' tq-item-stop' : ''}`}
                      onClick={e => { e.stopPropagation(); onDeleteJob(job) }}
                      title={job.status === 'running' ? '停止任务' : '移除'}
                    >
                      {job.status === 'running' ? '■' : '×'}
                    </button>
                  </div>
                  {job.last_message && (
                    <div className="tq-item-msg">{job.last_message}</div>
                  )}
                  {(job.status === 'done') && (
                    <div className="tq-item-hint">{clickHint(job)}</div>
                  )}
                  {job.status === 'error' && job.error_message && (
                    <div className="tq-item-err">{job.error_message}</div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      <button
        className={`tq-toggle${runningCount > 0 ? ' tq-toggle--running' : ''}`}
        onClick={() => setOpen(v => !v)}
        title="任务队列"
      >
        📬
        {runningCount > 0
          ? <span className="tq-badge-count">⏳ {runningCount}/{jobs.length} 运行中</span>
          : <span className="tq-badge-count">{jobs.length} 个任务</span>
        }
      </button>
    </div>,
    document.body
  )
}
