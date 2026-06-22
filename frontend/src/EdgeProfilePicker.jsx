import { useState, useEffect } from 'react'

/**
 * Shared Edge profile selector for tools that drive a signed-in Edge session
 * (Teams Transcript, Teams Chat). Lets the user pick which Edge profile/account
 * to use; the choice is persisted server-side per user.
 */
function EdgeProfilePicker({ token, onAuthError }) {
  const [profiles, setProfiles] = useState([])
  const [selected, setSelected] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const authHeaders = () => token ? { Authorization: `Bearer ${token}` } : {}

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch('/api/edge-profiles', { headers: authHeaders() })
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
      const res = await fetch('/api/edge-profiles/select', {
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

  const label = (p) => {
    const email = p.email ? ` — ${p.email}` : ''
    return `${p.name}${email}`
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap', marginTop: '8px' }}>
      <span style={{ fontSize: '13px', color: 'var(--text-muted, #888)', whiteSpace: 'nowrap' }}>
        Edge 账号
      </span>
      <select
        value={selected}
        onChange={handleChange}
        disabled={saving || profiles.length === 0}
        style={{ padding: '0.3rem 0.5rem', borderRadius: '6px', maxWidth: '100%' }}
      >
        {profiles.length === 0 && <option value="">未检测到 Edge 配置</option>}
        {profiles.map((p) => (
          <option key={p.dir} value={p.dir}>{label(p)}</option>
        ))}
      </select>
      {saving && <span style={{ fontSize: '12px', color: '#888' }}>保存中…</span>}
      {error && <span style={{ fontSize: '12px', color: '#c00' }}>{error}</span>}
    </div>
  )
}

export default EdgeProfilePicker
