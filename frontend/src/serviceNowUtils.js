// Pure helpers for the ServiceNow tables UI (no React): count cache, cell/axis re-keying,
// URL validation and display formatting.

export const emptyCounts = { values: {}, at: null }

// Numbers differ per login identity, so each identity keeps its own cache.
export const countsKey = (accId) => `sn_counts:${accId}`

export const loadCounts = (accId) => {
  try {
    return JSON.parse(localStorage.getItem(countsKey(accId))) || emptyCounts
  } catch {
    return emptyCounts
  }
}

export const cellKey = (r, c) => `${r},${c}`

// Re-key cell URLs after removing a row (axis 'r') or column (axis 'c').
export const removeIndex = (cells, axis, idx) => {
  const out = {}
  for (const [k, url] of Object.entries(cells)) {
    const [r, c] = k.split(',').map(Number)
    const n = axis === 'r' ? r : c
    if (n === idx) continue
    const nr = axis === 'r' && r > idx ? r - 1 : r
    const nc = axis === 'c' && c > idx ? c - 1 : c
    out[cellKey(nr, nc)] = url
  }
  return out
}

// Same re-indexing for total rows/columns: a total on `axis` is keyed and lists members on that axis.
export const removeSumIndex = (sums, axis, idx) => {
  const shift = (i) => (i > idx ? i - 1 : i)
  const out = {}
  for (const [key, members] of Object.entries(sums)) {
    if (key[0] !== axis) { out[key] = members; continue }
    const n = Number(key.slice(1))
    if (n === idx) continue
    out[`${axis}${shift(n)}`] = members.filter((i) => i !== idx).map(shift)
  }
  return out
}

// Mirrors the server rule (https://*.service-now.com) plus the list forms the worker can parse.
export const urlProblem = (url) => {
  const u = url.trim()
  if (!u) return ''
  let parsed
  try {
    parsed = new URL(u)
  } catch {
    return '不是有效的链接'
  }
  const host = parsed.hostname.toLowerCase()
  if (parsed.protocol !== 'https:' || !(host === 'service-now.com' || host.endsWith('.service-now.com'))) {
    return '只支持 https://*.service-now.com 的链接'
  }
  const isList = parsed.pathname.includes('/simplelist/') || /_list\.do/i.test(`${parsed.pathname}${parsed.search}`)
  if (!isList) return '不是 simplelist 或经典 _list.do 列表链接'
  return ''
}

export const shortError = (s) => (s.length > 16 ? `${s.slice(0, 16)}…` : s)

const SOURCE_NAMES = { rest: 'REST 接口', dom: '页面' }
export const sourceSuffix = (source) => (SOURCE_NAMES[source] ? `（${SOURCE_NAMES[source]}）` : '')
export const UNCONFIRMED_HINT = 'REST 返回 0，页面未能确认，数字可能不准，请在 ServiceNow 里核对'

export const formatUpdated = (iso) => {
  const d = new Date(iso)
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString() : d.toLocaleString()
}
