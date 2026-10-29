import { useEffect, useState } from 'react'

// A table cell holds either a plain URL or "ref:<link id>" pointing at a saved link.
export const REF_PREFIX = 'ref:'

// 'simplelist' for Next Experience list links, otherwise the classic list format.
export const linkKind = (url) => ((url || '').includes('/simplelist/') ? 'simplelist' : 'classic')
export const KIND_LABEL = { classic: '经典', simplelist: 'simplelist' }

// Per-user saved ServiceNow links; shared by the link list and by cells that reference a link.
export default function useSnLinks(token, onAuthError) {
  const [links, setLinks] = useState([])
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [saving, setSaving] = useState(false)

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  useEffect(() => {
    fetch('/api/servicenow/links', { headers: authHeaders() })
      .then(async (res) => {
        if (res.status === 401) { onAuthError?.(); return }
        if (!res.ok) throw new Error(`加载常用链接失败 (${res.status})`)
        setLinks((await res.json()).links)
        setLoaded(true)
      })
      .catch((e) => setLoadError(e.message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Saves the full list; resolves to { ok, error }.
  const persist = async (next) => {
    setSaving(true)
    try {
      const res = await fetch('/api/servicenow/links', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ links: next }),
      })
      if (res.status === 401) { onAuthError?.(); return { ok: false, error: '' } }
      const data = await res.json().catch(() => ({}))
      if (!res.ok) return { ok: false, error: data.detail || `保存失败 (${res.status})` }
      setLinks(data.links)
      return { ok: true, error: '' }
    } catch {
      return { ok: false, error: '保存失败，请检查网络' }
    } finally {
      setSaving(false)
    }
  }

  // Authorized JSON request to another endpoint; resolves to { ok, data, error }.
  const api = async (path, method = 'GET', body) => {
    try {
      const res = await fetch(path, {
        method,
        headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...authHeaders() },
        body: body ? JSON.stringify(body) : undefined,
      })
      if (res.status === 401) { onAuthError?.(); return { ok: false, error: '' } }
      const data = await res.json().catch(() => ({}))
      if (!res.ok) return { ok: false, error: data.detail || `请求失败 (${res.status})` }
      return { ok: true, data, error: '' }
    } catch {
      return { ok: false, error: '请求失败，请检查网络' }
    }
  }

  // Authorized POST that streams server-sent events to onEvent(event); resolves to { ok, error }.
  const stream = async (path, body, onEvent) => {
    try {
      const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(body),
      })
      if (res.status === 401) { onAuthError?.(); return { ok: false, error: '' } }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        return { ok: false, error: data.detail || `请求失败 (${res.status})` }
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let failure = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''
        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (!line.startsWith('data: ')) continue
            try {
              const event = JSON.parse(line.slice(6))
              if (event.type === 'error') failure = event.message
              onEvent(event)
            } catch { /* ignore malformed SSE lines */ }
          }
        }
      }
      return failure ? { ok: false, error: failure } : { ok: true, error: '' }
    } catch {
      return { ok: false, error: '请求失败，请检查网络' }
    }
  }

  return { links, loaded, loadError, saving, persist, api, stream }
}
