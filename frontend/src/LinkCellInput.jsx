import { useEffect, useRef, useState } from 'react'
import { REF_PREFIX, linkKind, KIND_LABEL } from './useSnLinks'

const OPEN_ALIAS = /\[([A-Za-z0-9_]*)$/

// Edit-mode cell input. Accepts a plain URL, or a formula such as "=[PB]+[PR]" where each [alias]
// is the short name of a saved link; typing "[" (or the 🔖 button) offers the saved links.
// On a non-formula cell, the 🔖 button can also point the whole cell at one link ("ref:<id>").
function LinkCellInput({ value, links, ready, problem, onChange }) {
  const [menu, setMenu] = useState(false)
  const wrapRef = useRef(null)

  useEffect(() => {
    if (!menu) return undefined
    const onDown = (e) => { if (!wrapRef.current?.contains(e.target)) setMenu(false) }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menu])

  if (value.startsWith(REF_PREFIX)) {
    const link = links.find((l) => l.id === value.slice(REF_PREFIX.length))
    const name = link ? link.name : ready ? '（链接已删除）' : '…'
    return (
      <span className={`sn-ref-chip ${problem ? 'sn-url-invalid' : ''}`} title={problem || link?.url}>
        <span className="sn-ref-name">= {name}</span>
        <button title="清除引用" onClick={() => onChange('')}>✕</button>
      </span>
    )
  }

  const isFormula = value.trim().startsWith('=')
  const typing = isFormula ? OPEN_ALIAS.exec(value) : null
  const partial = typing ? typing[1].toLowerCase() : ''
  const aliased = links.filter((l) => l.alias)

  let options = []
  if (typing) {
    options = aliased.filter((l) => !partial || `${l.alias} ${l.name}`.toLowerCase().includes(partial))
  } else if (menu) {
    options = links
  }

  const pick = (link) => {
    setMenu(false)
    if (typing) {
      onChange(value.replace(OPEN_ALIAS, `[${link.alias}]`))
    } else if (isFormula) {
      const last = value.trimEnd().slice(-1)
      onChange(`${value.trimEnd()}${last === ']' || last === ')' || /\d/.test(last) ? '+' : ''}[${link.alias}]`)
    } else {
      onChange(`${REF_PREFIX}${link.id}`)
    }
  }

  const needsAlias = (l) => isFormula && !l.alias

  return (
    <span className="sn-ref-wrap" ref={wrapRef}>
      <input
        className={`sn-url-input ${problem ? 'sn-url-invalid' : ''}`}
        placeholder="链接，或公式 =[缩写]+[缩写]"
        title={problem}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && typing && options[0]) pick(options[0])
          else if (e.key === 'Escape') setMenu(false)
        }}
      />
      <button className="sn-ref-btn" title="从常用链接中选择" onClick={() => setMenu((m) => !m)}>🔖</button>
      {(menu || typing) && (
        <ul className="sn-ref-menu" role="listbox">
          {options.map((l) => (
            <li key={l.id}>
              <button
                role="option"
                onClick={() => pick(l)}
                disabled={needsAlias(l)}
                title={needsAlias(l) ? '这个链接还没有设置缩写，不能用在公式里' : l.url}
              >
                <span>
                  {l.alias && <b className="sn-ref-alias">[{l.alias}]</b>} {l.name}
                  <span className={`sn-kind sn-kind-${linkKind(l.url)}`}>{KIND_LABEL[linkKind(l.url)]}</span>
                </span>
                {l.note && <span className="sn-ref-note">{l.note}</span>}
              </button>
            </li>
          ))}
          {!options.length && (
            <li className="sn-ref-none">
              {typing && !aliased.length
                ? '还没有链接设置缩写，请先在常用链接里编辑'
                : links.length ? '没有匹配的常用链接' : '还没有常用链接'}
            </li>
          )}
        </ul>
      )}
    </span>
  )
}

export default LinkCellInput
