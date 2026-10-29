import { useEffect, useMemo, useRef, useState } from 'react'
import { linkKind } from './useSnLinks'

const ALIAS_RE = /^[A-Za-z_][A-Za-z0-9_]{0,19}$/

// Two conditions on the same field that can never both hold (field = a and field = b, or = v and != v).
const conflicts = (a, b) =>
  !!a.field && a.field === b.field && (
    (a.op === '=' && b.op === '=' && a.value !== b.value) ||
    (((a.op === '=' && b.op === '!=') || (a.op === '!=' && b.op === '=')) && a.value === b.value)
  )

// Builds a new saved link from the conditions of existing links: pick the conditions to keep or add,
// choose the output format (classic or simplelist), and save the result as a new link.
function LinkComposer({ base, links, api, onSave, onCancel }) {
  const [data, setData] = useState(null) // conditions of every saved link
  const [loadError, setLoadError] = useState('')
  const [selected, setSelected] = useState([])
  const [format, setFormat] = useState(linkKind(base.url))
  const [name, setName] = useState(`${base.name} 组合`)
  const [alias, setAlias] = useState('')
  const [note, setNote] = useState('')
  const [preview, setPreview] = useState(null) // { url } | { error }
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const initialized = useRef(false)

  const load = async () => {
    const res = await api('/api/servicenow/links/conditions')
    if (!res.ok) { setLoadError(res.error); return }
    setData(res.data.links)
    if (!initialized.current) {
      initialized.current = true
      const mine = res.data.links.find((l) => l.id === base.id)
      setSelected((mine?.conditions || []).map((c) => c.raw))
    }
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const baseInfo = data?.find((l) => l.id === base.id)

  // Every distinct condition on the base link's table, base link's own first.
  const library = useMemo(() => {
    if (!baseInfo) return []
    const map = new Map()
    const ordered = [baseInfo, ...data.filter((l) => l.id !== base.id && l.table === baseInfo.table)]
    for (const l of ordered) {
      for (const c of l.conditions) {
        const entry = map.get(c.raw) || { ...c, from: [], own: false }
        entry.from.push(l.name)
        if (l.id === base.id) entry.own = true
        map.set(c.raw, entry)
      }
    }
    return [...map.values()]
  }, [data, baseInfo, base.id])

  const byRaw = useMemo(() => new Map(library.map((c) => [c.raw, c])), [library])

  const toggle = (cond) => {
    if (selected.includes(cond.raw)) {
      setSelected(selected.filter((r) => r !== cond.raw))
      return
    }
    const clash = selected.map((r) => byRaw.get(r)).filter((c) => c && conflicts(c, cond))
    if (clash.length) {
      const names = clash.map((c) => `「${c.label}」`).join('、')
      if (!window.confirm(`「${cond.label}」与已选的${names}冲突（同一字段，结果会为空）。\n\n用它替换已选的条件？`)) return
      setSelected([...selected.filter((r) => !clash.some((c) => c.raw === r)), cond.raw])
      return
    }
    setSelected([...selected, cond.raw])
  }

  const rename = async (cond) => {
    const next = window.prompt('条件名称（留空恢复默认）', cond.label)
    if (next === null) return
    const res = await api('/api/servicenow/links/condition-names', 'POST', { raw: cond.raw, name: next })
    if (res.ok) load()
    else setError(res.error)
  }

  // Live preview of the generated link.
  useEffect(() => {
    if (!selected.length) { setPreview(null); return undefined }
    let cancelled = false
    const timer = setTimeout(async () => {
      const res = await api('/api/servicenow/links/build', 'POST', {
        base_id: base.id, conditions: selected, format, title: name.trim() || base.name,
      })
      if (!cancelled) setPreview(res.ok ? { url: res.data.url } : { error: res.error })
    }, 250)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [selected, format, name]) // eslint-disable-line react-hooks/exhaustive-deps

  const copy = async () => {
    if (!preview?.url) return
    try {
      await navigator.clipboard.writeText(preview.url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setError('复制失败，请手动复制')
    }
  }

  const save = async () => {
    const trimmedAlias = alias.trim()
    if (!preview?.url) { setError(preview?.error || '请至少选择一个条件'); return }
    if (!name.trim()) { setError('请输入名称'); return }
    if (trimmedAlias && !ALIAS_RE.test(trimmedAlias)) {
      setError('缩写只能包含字母、数字、下划线，不能以数字开头，最长 20 个字符')
      return
    }
    if (trimmedAlias && links.some((l) => (l.alias || '').toLowerCase() === trimmedAlias.toLowerCase())) {
      setError(`缩写「${trimmedAlias}」已被其他链接使用`)
      return
    }
    setBusy(true)
    const err = await onSave({ name: name.trim(), url: preview.url, note: note.trim(), alias: trimmedAlias })
    setBusy(false)
    if (err) setError(err)
  }

  if (loadError) {
    return (
      <div className="sn-comp">
        <p className="sn-links-error">{loadError}</p>
        <button className="btn-outline btn-sm" onClick={onCancel}>关闭</button>
      </div>
    )
  }
  if (!data) return <div className="sn-comp"><p className="sn-links-empty">加载条件中…</p></div>
  if (baseInfo?.unsupported) {
    return (
      <div className="sn-comp">
        <p className="sn-links-error">「{base.name}」{baseInfo.unsupported}。</p>
        <button className="btn-outline btn-sm" onClick={onCancel}>关闭</button>
      </div>
    )
  }

  const own = library.filter((c) => c.own)
  const others = library.filter((c) => !c.own)

  const row = (c) => (
    <li key={c.raw} className="sn-comp-cond">
      <label title={c.raw}>
        <input type="checkbox" checked={selected.includes(c.raw)} onChange={() => toggle(c)} />
        <span className="sn-comp-label">{c.label}</span>
        {!c.own && <span className="sn-ref-note">来自：{c.from.join('、')}</span>}
      </label>
      <button className="sn-link-act" title="修改这个条件的显示名称" onClick={() => rename(c)}>✎</button>
    </li>
  )

  return (
    <div className="sn-comp">
      <div className="sn-comp-title">以「{base.name}」为基础组合条件</div>
      <p className="sn-hint">勾选要保留的条件；下面还可以加入其他链接里的条件。只组合同一张表的条件。</p>
      {error && <p className="sn-links-error" role="alert">{error}</p>}

      <div className="sn-comp-sec">基础链接的条件</div>
      <ul className="sn-comp-list">{own.map(row)}</ul>
      {others.length > 0 && (
        <>
          <div className="sn-comp-sec">其他链接里的条件</div>
          <ul className="sn-comp-list">{others.map(row)}</ul>
        </>
      )}

      <div className="sn-comp-fields">
        <div className="sn-comp-format">
          <span>生成格式</span>
          <label><input type="radio" checked={format === 'classic'} onChange={() => setFormat('classic')} /> 经典</label>
          <label><input type="radio" checked={format === 'simplelist'} onChange={() => setFormat('simplelist')} /> simplelist</label>
        </div>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="新链接的名称" maxLength={100} />
        <input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="缩写（可选，用于公式）" maxLength={20} />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注（可选）" maxLength={500} />
      </div>

      <div className="sn-comp-preview">
        {!selected.length ? '请至少选择一个条件'
          : preview?.error ? <span className="sn-links-error">{preview.error}</span>
          : preview?.url ? <code title={preview.url}>{preview.url}</code> : '生成中…'}
      </div>

      <div className="sn-links-form-actions">
        <button className="btn-primary btn-sm" onClick={save} disabled={busy || !preview?.url}>保存为新链接</button>
        <button className="btn-outline btn-sm" onClick={copy} disabled={!preview?.url}>{copied ? '已复制' : '复制链接'}</button>
        <button className="btn-outline btn-sm" onClick={onCancel} disabled={busy}>取消</button>
        {selected.length > 0 && <span className="sn-ref-note">已选 {selected.length} 个条件</span>}
      </div>
    </div>
  )
}

export default LinkComposer
