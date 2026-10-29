// Cell formulas: "=" followed by additions of integers and [alias] references, with parentheses.
//   expr := term ('+' term)*      term := INTEGER | '[' alias ']' | '(' expr ')'
// Aliases are the short names set on saved links. Parsed by hand; never evaluated as code.

const ALIAS_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

const tokenize = (src) => {
  const tokens = []
  let i = 0
  while (i < src.length) {
    const ch = src[i]
    if (/\s/.test(ch)) { i += 1; continue }
    if (ch === '+' || ch === '(' || ch === ')') {
      tokens.push({ type: ch })
      i += 1
    } else if (ch === '[') {
      const end = src.indexOf(']', i)
      if (end < 0) return { error: '缺少 ]' }
      const alias = src.slice(i + 1, end).trim()
      if (!ALIAS_RE.test(alias)) return { error: alias ? `缩写 [${alias}] 格式不正确` : '[] 里没有缩写' }
      tokens.push({ type: 'var', alias })
      i = end + 1
    } else if (/\d/.test(ch)) {
      let j = i
      while (j < src.length && /\d/.test(src[j])) j += 1
      tokens.push({ type: 'num', value: Number(src.slice(i, j)) })
      i = j
    } else {
      return { error: `公式里只支持加法、括号和整数，遇到了「${ch}」` }
    }
  }
  return { tokens }
}

// Returns { ast, aliases } (aliases lower-cased, unique) or { error }.
export const parseFormula = (src) => {
  const { tokens, error } = tokenize(src)
  if (error) return { error }
  if (!tokens.length) return { error: '公式不能为空' }
  let pos = 0
  const aliases = new Set()

  const parseTerm = () => {
    const t = tokens[pos]
    if (!t) throw new Error('公式不完整')
    pos += 1
    if (t.type === 'num') return t
    if (t.type === 'var') {
      aliases.add(t.alias.toLowerCase())
      return t
    }
    if (t.type === '(') {
      const inner = parseExpr()
      if (tokens[pos]?.type !== ')') throw new Error('括号不匹配')
      pos += 1
      return inner
    }
    throw new Error(t.type === ')' ? '括号不匹配' : '+ 前后需要有内容')
  }

  const parseExpr = () => {
    const terms = [parseTerm()]
    while (tokens[pos]?.type === '+') {
      pos += 1
      terms.push(parseTerm())
    }
    return { type: 'add', terms }
  }

  try {
    const ast = parseExpr()
    if (pos < tokens.length) throw new Error(tokens[pos].type === ')' ? '括号不匹配' : '公式里缺少 +')
    return { ast, aliases: [...aliases] }
  } catch (e) {
    return { error: e.message }
  }
}

// getValue(alias) -> number, or null when that variable has no data. Missing variables count as 0
// and are reported, so callers can show a partial sum.
export const evaluateFormula = (ast, getValue) => {
  const missing = new Set()
  const walk = (node) => {
    if (node.type === 'num') return node.value
    if (node.type === 'var') {
      const v = getValue(node.alias.toLowerCase())
      if (v === null || v === undefined) { missing.add(node.alias); return 0 }
      return v
    }
    return node.terms.reduce((sum, t) => sum + walk(t), 0)
  }
  return { total: walk(ast), missing: [...missing] }
}

export const formulaMentions = (formula, alias) =>
  !!alias && new RegExp(`\\[\\s*${alias}\\s*\\]`, 'i').test(formula)
