import { useEffect, useRef, useState } from 'react'
import { REF_PREFIX } from './useSnLinks'

const MAX_FILE_BYTES = 5 * 1024 * 1024
const SECTION_LABELS = { tables: '统计表', links: '常用链接', conditions: '条件库（含名称和表来源）' }
const SECTIONS = Object.keys(SECTION_LABELS)

const pad = (n) => String(n).padStart(2, '0')
const stamp = (withTime) => {
  const d = new Date()
  const day = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`
  return withTime ? `${day}-${pad(d.getHours())}${pad(d.getMinutes())}` : day
}

const download = (data, filename) => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const exportQuery = (sections) => SECTIONS.map((s) => `${s}=${sections.includes(s)}`).join('&')

// Export / import of the tables, saved links and condition library as one JSON file.
// mode 'import' is opened from the menu or by dragging a file over the page (`autoOpened`);
// the whole backdrop accepts the drop, so the file can be released anywhere.
export default function ServiceNowMigrate({
  mode, autoOpened, api, tables, linkCount, beforeExport, onClose, onImported,
}) {
  const [picked, setPicked] = useState(Object.fromEntries(SECTIONS.map((s) => [s, true])))
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)
  const [fileName, setFileName] = useState('')
  const [bundle, setBundle] = useState(null)
  const [preview, setPreview] = useState(null)
  const [merge, setMerge] = useState('merge') // 'merge' | 'replace'
  const [done, setDone] = useState(null)
  const [rehost, setRehost] = useState(null) // preview (then result) of the host migration
  const fileInput = useRef(null)
  const seq = useRef(0)

  useEffect(() => {
    if (mode !== 'rehost') return
    setBusy(true)
    beforeExport?.().then(() => api('/api/servicenow/migrate/rehost/preview', 'POST', {})).then((res) => {
      setBusy(false)
      if (!res.ok) { setError(res.error || '无法读取当前配置'); return }
      setRehost(res.data)
    })
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const chosen = SECTIONS.filter((s) => picked[s])
  const toggle = (s) => setPicked((p) => ({ ...p, [s]: !p[s] }))

  // ---- export ----
  const usesLinks = tables.some((t) => Object.values(t.cells).some((v) => v.startsWith(REF_PREFIX) || v.startsWith('=')))

  const doExport = async () => {
    setBusy(true)
    setError('')
    await beforeExport?.()
    const res = await api(`/api/servicenow/migrate/export?${exportQuery(chosen)}`)
    setBusy(false)
    if (!res.ok) { setError(res.error || '导出失败'); return }
    download(res.data, `servicenow-migrate-${stamp(false)}.json`)
    onClose()
  }

  // ---- import ----
  const effective = preview ? preview.present.filter((s) => picked[s]) : []

  // The preview follows the chosen sections and mode; a stale response is ignored.
  useEffect(() => {
    if (!bundle) return
    const id = ++seq.current
    setBusy(true)
    api('/api/servicenow/migrate/preview', 'POST', { bundle, mode: merge, sections: chosen }).then((res) => {
      if (id !== seq.current) return
      setBusy(false)
      if (!res.ok) { setError(res.error || '无法读取文件'); setPreview(null); return }
      setError('')
      setPreview(res.data)
    })
  }, [bundle, merge, picked]) // eslint-disable-line react-hooks/exhaustive-deps

  const readFile = async (file) => {
    if (!file) return
    setDone(null)
    if (file.size > MAX_FILE_BYTES) { setError('文件太大，不是迁移文件'); return }
    let data
    try {
      data = JSON.parse(await file.text())
    } catch {
      setError('不是有效的 JSON 文件')
      return
    }
    if (!data || typeof data !== 'object' || Array.isArray(data)) { setError('不是有效的迁移文件'); return }
    setFileName(file.name)
    setPreview(null)
    setError('')
    setBundle(data)
  }

  const onDrop = (e) => {
    e.preventDefault()
    setOver(false)
    if (!busy) readFile(e.dataTransfer.files?.[0])
  }

  const onDragLeave = (e) => {
    if (e.relatedTarget) return
    setOver(false)
    if (autoOpened && !bundle) onClose()
  }

  const doImport = async () => {
    const replacing = merge === 'replace'
    if (replacing) {
      const what = effective.map((s) => SECTION_LABELS[s]).join('、')
      if (!window.confirm(`将用文件内容替换本机的「${what}」。\n替换前会先下载一份本机配置的备份，继续吗？`)) return
    }
    setBusy(true)
    setError('')
    await beforeExport?.()
    if (replacing) {
      const backup = await api(`/api/servicenow/migrate/export?${exportQuery(effective)}`)
      if (!backup.ok) { setBusy(false); setError(backup.error || '备份失败，已取消导入'); return }
      download(backup.data, `servicenow-backup-${stamp(true)}.json`)
    }
    const res = await api('/api/servicenow/migrate/import', 'POST', { bundle, mode: merge, sections: effective })
    setBusy(false)
    if (!res.ok) { setError(res.error || '导入失败'); return }
    setDone(res.data)
    await onImported(res.data)
  }

  const doRehost = async () => {
    setBusy(true)
    setError('')
    await beforeExport?.()
    const backup = await api(`/api/servicenow/migrate/export?${exportQuery(SECTIONS)}`)
    if (!backup.ok) { setBusy(false); setError(backup.error || '备份失败，已取消迁移'); return }
    download(backup.data, `servicenow-backup-${stamp(true)}.json`)
    const res = await api('/api/servicenow/migrate/rehost', 'POST')
    setBusy(false)
    if (!res.ok) { setError(res.error || '迁移失败'); return }
    setRehost(res.data)
    await onImported(res.data)
  }

  const line = (s) => {
    const have = preview.available[s]
    const cur = preview.current[s]
    const added = preview.added[s]
    const skipped = preview.skipped[s]
    const parts = [`文件中 ${have} 项`]
    if (merge === 'replace') parts.push(`将替换本机的 ${cur} 项`)
    else parts.push(`新增 ${added} 项`)
    if (s === 'links' && preview.reused_links) parts.push(`${preview.reused_links} 项与本机已有链接相同，直接复用`)
    if (skipped) parts.push(`${skipped} 项无效被跳过`)
    return parts.join('，')
  }

  const info = done || preview
  const notes = info && effective.length > 0 ? [
    ...(effective.includes('tables') && info.renamed_tables.length
      ? [`${info.renamed_tables.length} 个表格与本机同名，已重命名：${info.renamed_tables.join('；')}`] : []),
    ...(effective.includes('links') && info.changed_aliases.length
      ? [`缩写与本机冲突，已改名（公式同步改写）：${info.changed_aliases.join('；')}`] : []),
    ...(info.dropped_cells ? [`${info.dropped_cells} 个无效的单元格或合计被丢弃`] : []),
    ...(info.dangling_refs ? [`${info.dangling_refs} 个单元格引用的链接找不到，导入后会显示为失效引用（建议同时导入常用链接）`] : []),
  ] : []

  const title = { export: '导出配置', import: '导入配置', rehost: '迁移到新平台' }[mode]

  return (
    <div
      className="sn-modal-backdrop"
      onDragEnter={(e) => { e.preventDefault(); if (mode === 'import') setOver(true) }}
      onDragOver={(e) => { e.preventDefault() }}
      onDragLeave={onDragLeave}
      onDrop={mode === 'import' ? onDrop : (e) => e.preventDefault()}
    >
      <div className="sn-modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="sn-modal-title">{title}</div>

        {mode === 'export' && (
          <>
            <p className="sn-hint">导出为一个 JSON 文件，可在另一台电脑上导入。不包含登录状态和刷新得到的数字。</p>
            <div className="sn-mig-sections">
              <label>
                <input type="checkbox" checked={picked.tables} onChange={() => toggle('tables')} />
                统计表 <span className="sn-mig-count">（{tables.length} 个）</span>
              </label>
              <label>
                <input type="checkbox" checked={picked.links} onChange={() => toggle('links')} />
                常用链接 <span className="sn-mig-count">（{linkCount} 条）</span>
              </label>
              <label>
                <input type="checkbox" checked={picked.conditions} onChange={() => toggle('conditions')} />
                {SECTION_LABELS.conditions}
              </label>
            </div>
            {picked.tables && !picked.links && usesLinks && (
              <p className="sn-mig-warn">有单元格引用了常用链接，不一起导出的话，在另一台电脑上这些单元格会失效。</p>
            )}
            {error && <p className="sn-links-error" role="alert">{error}</p>}
            <div className="sn-links-form-actions">
              <button className="btn-primary sn-btn" onClick={doExport} disabled={busy || chosen.length === 0}>
                {busy ? '导出中…' : '导出'}
              </button>
              <button className="btn-outline btn-sm" onClick={onClose} disabled={busy}>取消</button>
            </div>
          </>
        )}

        {mode === 'rehost' && (
          <>
            <p className="sn-hint">
              把统计表单元格、常用链接和条件库里指向 {rehost?.old_host || 'dsv.service-now.com'} 的链接，
              改成 {rehost?.new_host || 'servicecentral.dsv.com'}，路径和筛选条件不变。旧平台的数据已经停止更新，继续用旧链接读到的数字是错的。
            </p>
            {rehost && !rehost.applied && (
              <div className="sn-mig-sections">
                <div>常用链接：{rehost.links_changed} 条改为新域名{rehost.links_merged ? `，其中 ${rehost.links_merged} 条与别的链接重复，会合并为一条` : ''}</div>
                <div>统计表单元格：{rehost.cells_changed} 个改为新域名{rehost.refs_remapped ? `，${rehost.refs_remapped} 个引用改指向合并后的链接` : ''}</div>
                <div>条件库的表来源：{rehost.origins_changed} 个</div>
              </div>
            )}
            {rehost && !rehost.applied && !rehost.links_changed && !rehost.cells_changed && !rehost.origins_changed && (
              <p className="sn-hint">没有需要迁移的内容，所有链接都已经是新域名。</p>
            )}
            {rehost?.applied && (
              <>
                <p className="sn-lib-info">
                  ✓ 已迁移：{rehost.links_changed} 条链接、{rehost.cells_changed} 个单元格、{rehost.origins_changed} 个表来源
                </p>
                <p className="sn-hint">旧链接的数字不会带过来，请点「刷新」读取新平台的数据。</p>
              </>
            )}
            {!rehost?.applied && <p className="sn-hint">执行前会自动下载一份当前配置的备份。</p>}
            {error && <p className="sn-links-error" role="alert">{error}</p>}
            <div className="sn-links-form-actions">
              {rehost?.applied ? (
                <button className="btn-primary sn-btn" onClick={onClose}>完成</button>
              ) : (
                <>
                  <button
                    className="btn-primary sn-btn"
                    onClick={doRehost}
                    disabled={busy || !rehost || !(rehost.links_changed || rehost.cells_changed || rehost.origins_changed)}
                  >
                    {busy ? '处理中…' : '迁移'}
                  </button>
                  <button className="btn-outline btn-sm" onClick={onClose} disabled={busy}>取消</button>
                </>
              )}
            </div>
          </>
        )}

        {mode === 'import' && !done && (
          <>
            <div
              className={`sn-drop ${over ? 'sn-drop-over' : ''}`}
              onClick={() => fileInput.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileInput.current?.click() }}
            >
              <div className="sn-drop-icon">📥</div>
              <div>{fileName ? `已选择：${fileName}` : '把迁移文件（.json）拖到这里'}</div>
              <div className="sn-drop-sub">{fileName ? '可以再拖一个文件替换' : '或点击选择文件'}</div>
              <input
                ref={fileInput}
                type="file"
                accept=".json,application/json"
                hidden
                onChange={(e) => { readFile(e.target.files?.[0]); e.target.value = '' }}
              />
            </div>

            {preview && (
              <>
                {preview.exported_at && (
                  <p className="sn-hint">文件导出时间：{new Date(preview.exported_at).toLocaleString()}</p>
                )}
                <div className="sn-mig-sections">
                  {SECTIONS.filter((s) => preview.present.includes(s)).map((s) => (
                    <label key={s}>
                      <input type="checkbox" checked={picked[s]} onChange={() => toggle(s)} disabled={busy} />
                      <span>
                        {SECTION_LABELS[s]}
                        <span className="sn-mig-count">{picked[s] ? `：${line(s)}` : ''}</span>
                      </span>
                    </label>
                  ))}
                </div>
                <div className="sn-comp-format">
                  <label>
                    <input type="radio" checked={merge === 'merge'} onChange={() => setMerge('merge')} disabled={busy} />
                    合并到现有配置
                  </label>
                  <label>
                    <input type="radio" checked={merge === 'replace'} onChange={() => setMerge('replace')} disabled={busy} />
                    替换本机配置（新电脑）
                  </label>
                </div>
                {merge === 'merge' ? (
                  <p className="sn-hint">同名表格会重命名为「xxx（导入）」，相同链接和条件不会重复。</p>
                ) : (
                  <p className="sn-mig-warn">所选内容在本机的现有数据会被替换，替换前会自动下载一份备份。</p>
                )}
                {notes.map((n) => <p key={n} className="sn-mig-note">{n}</p>)}
              </>
            )}

            {error && <p className="sn-links-error" role="alert">{error}</p>}
            <div className="sn-links-form-actions">
              <button className="btn-primary sn-btn" onClick={doImport} disabled={busy || effective.length === 0}>
                {busy ? '处理中…' : '导入'}
              </button>
              <button className="btn-outline btn-sm" onClick={onClose} disabled={busy}>取消</button>
            </div>
          </>
        )}

        {mode === 'import' && done && (
          <>
            <p className="sn-lib-info">
              ✓ 已导入
              {effective.includes('tables') ? ` ${done.added.tables} 个表格` : ''}
              {effective.includes('links') ? `、${done.added.links} 条新链接` : ''}
              {effective.includes('conditions') ? `、${done.added.conditions} 个新条件` : ''}
            </p>
            {notes.map((n) => <p key={n} className="sn-mig-note">{n}</p>)}
            <p className="sn-hint">需要登录的数据（计数）请在这台电脑上重新登录并刷新。</p>
            <div className="sn-links-form-actions">
              <button className="btn-primary sn-btn" onClick={onClose}>完成</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
