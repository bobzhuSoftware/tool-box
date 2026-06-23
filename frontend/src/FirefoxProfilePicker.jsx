import { useState, useEffect } from 'react'

/**
 * Firefox profile selector for the Web→PDF X/Twitter mode. Lets the user pick
 * which local Firefox profile (i.e. which logged-in X account) to use; the
 * choice is persisted server-side per user. Profiles already signed into X are
 * flagged so the user can tell them apart (Firefox stores no account email).
 */
function FirefoxProfilePicker({ token, onAuthError }) {
  const [profiles, setProfiles] = useState([])
  const [selected, setSelected] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch('/api/firefox-profiles', { headers: authHeaders() })
        if (res.status === 401) { onAuthError && onAuthError(); return }
        if (!res.ok) throw new Error(`Server error ${res.status}`)
        const data = await res.json()
        if (cancelled) return
        setProfiles(data.profiles || [])
        setSelected(data.selected || '')
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
    }
    load()
    return () => { cancelled = true }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleChange = async (e) => {
    const dir = e.target.value
    setSelected(dir)
    setSaving(true)
    setError('')
    try {
      const res = await fetch('/api/firefox-profiles/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ dir }),
      })
      if (res.status === 401) { onAuthError && onAuthError(); return }
      if (!res.ok) throw new Error(`Server error ${res.status}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const label = (p) => `${p.name}${p.has_x ? ' — ✅ 已登录 X' : ' — ⚠️ 未登录 X'}`

  const selectedProfile = profiles.find((p) => p.dir === selected)
  const noneLoggedIn = profiles.length > 0 && profiles.every((p) => !p.has_x)
  const selectedNotLoggedIn = !!selectedProfile && !selectedProfile.has_x

  return (
    <div style={{ marginTop: '8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '13px', color: 'var(--text-muted, #888)', whiteSpace: 'nowrap' }}>
          Firefox 账号
        </span>
        <select
          value={selected}
          onChange={handleChange}
          disabled={saving || profiles.length === 0}
          style={{ padding: '0.3rem 0.5rem', borderRadius: '6px', maxWidth: '100%' }}
        >
          {profiles.length === 0 && <option value="">未检测到 Firefox 配置</option>}
          {profiles.map((p) => (
            <option key={p.dir} value={p.dir}>{label(p)}</option>
          ))}
        </select>
        {saving && <span style={{ fontSize: '12px', color: '#888' }}>保存中…</span>}
        {error && <span style={{ fontSize: '12px', color: '#c00' }}>{error}</span>}
      </div>

      {(selectedNotLoggedIn || noneLoggedIn) && (
        <div style={{ marginTop: '6px', fontSize: '12px', color: '#b35900', lineHeight: 1.5 }}>
          ⚠️ {noneLoggedIn
            ? '检测到的 Firefox 配置都未登录 X。'
            : '当前选择的 Firefox 配置未登录 X。'}
          请先在该 Firefox 配置里打开 <a href="https://x.com" target="_blank" rel="noreferrer">x.com</a> 登录一次，
          否则 X 文章转 PDF 时大概率会遇到登录墙。
        </div>
      )}
    </div>
  )
}

export default FirefoxProfilePicker
