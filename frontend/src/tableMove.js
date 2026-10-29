// Moving a row (axis 'r') or column (axis 'c') from index `from` to index `to`.
// Cell links and total rows/columns are index-based, so they are re-keyed with the same mapping.
export function moveAxis(table, axis, from, to) {
  const key = axis === 'r' ? 'rows' : 'cols'
  const names = table[key]
  if (from === to || from < 0 || to < 0 || from >= names.length || to >= names.length) return null

  const order = names.map((_, i) => i)
  order.splice(to, 0, order.splice(from, 1)[0])
  const newIndex = []
  order.forEach((oldIdx, pos) => { newIndex[oldIdx] = pos })

  const cells = {}
  for (const [k, url] of Object.entries(table.cells || {})) {
    const [r, c] = k.split(',').map(Number)
    const nr = axis === 'r' ? newIndex[r] ?? r : r
    const nc = axis === 'c' ? newIndex[c] ?? c : c
    cells[`${nr},${nc}`] = url
  }

  const sums = {}
  for (const [k, members] of Object.entries(table.sums || {})) {
    if (k[0] !== axis) { sums[k] = members; continue }
    const mapped = members.map((i) => newIndex[i] ?? i).sort((a, b) => a - b)
    sums[`${axis}${newIndex[Number(k.slice(1))]}`] = mapped
  }

  return { [key]: order.map((i) => names[i]), cells, sums }
}
