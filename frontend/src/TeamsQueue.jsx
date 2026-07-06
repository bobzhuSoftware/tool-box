import { useState } from 'react'
import { createPortal } from 'react-dom'
import './TeamsQueue.css'

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
  if (status === 'done') return '✅'
  return '❌'
}

export default function TeamsQueue({ jobs, onOpenJob, onDeleteJob }) {
  const [open, setOpen] = useState(false)

  if (jobs.length === 0) return null

  const runningCount = jobs.filter(j => j.status === 'running').length

  return createPortal(
    <div className="tq-root">
      {open && (
        <div className="tq-panel">
          <div className="tq-panel-head">
            <span>📋 Teams 字幕任务</span>
            <button className="tq-panel-close" onClick={() => setOpen(false)}>✕</button>
          </div>
          <div className="tq-list">
            {jobs.map(job => (
              <div
                key={job.job_id}
                className={`tq-item tq-item--${job.status}`}
                onClick={() => onOpenJob(job)}
                title={job.status === 'done' ? '点击跳转下载' : job.status === 'running' ? '点击查看进度' : '点击查看详情'}
              >
                <div className="tq-item-top">
                  <span className="tq-status-icon">{statusIcon(job.status)}</span>
                  <span className="tq-item-name">
                    {job.result?.name || shortenUrl(job.url)}
                  </span>
                  <button
                    className={`tq-item-del${job.status === 'running' ? ' tq-item-stop' : ''}`}
                    onClick={e => { e.stopPropagation(); onDeleteJob(job.job_id) }}
                    title={job.status === 'running' ? '停止任务' : '移除'}
                  >
                    {job.status === 'running' ? '■' : '×'}
                  </button>
                </div>
                {job.last_message && (
                  <div className="tq-item-msg">{job.last_message}</div>
                )}
                {job.status === 'done' && (
                  <div className="tq-item-hint">点击此处跳转下载 →</div>
                )}
                {job.status === 'error' && job.error_message && (
                  <div className="tq-item-err">{job.error_message}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <button
        className={`tq-toggle${runningCount > 0 ? ' tq-toggle--running' : ''}`}
        onClick={() => setOpen(v => !v)}
        title="Teams 字幕任务队列"
      >
        📋
        {runningCount > 0
          ? <span className="tq-badge-count">⏳ {runningCount} 运行中</span>
          : <span className="tq-badge-count">{jobs.length} 个任务</span>
        }
      </button>
    </div>,
    document.body
  )
}
