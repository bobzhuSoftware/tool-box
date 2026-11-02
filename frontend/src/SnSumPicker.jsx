import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

// Edit-mode picker for the rows/columns a total adds up. The list opens in a fixed-position popover
// (portaled to <body>) so it neither stretches the header cell nor gets clipped by the scrolling table.
export default function SumPicker({ names, isSum, members, onToggle }) {
  const [pos, setPos] = useState(null)
  const btnRef = useRef(null)
  const menuRef = useRef(null)
  const open = !!pos
  const candidates = names.map((name, i) => ({ name, i })).filter(({ i }) => !isSum(i))

  useEffect(() => {
    if (!open) return undefined
    const inside = (e) => btnRef.current?.contains(e.target) || menuRef.current?.contains(e.target)
    const onDown = (e) => { if (!inside(e)) setPos(null) }
    const onScroll = (e) => { if (!menuRef.current?.contains(e.target)) setPos(null) }
    const onKey = (e) => { if (e.key === 'Escape') setPos(null) }
    const close = () => setPos(null)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  const toggleOpen = () => {
    if (open) { setPos(null); return }
    const rect = btnRef.current.getBoundingClientRect()
    setPos({ top: rect.bottom + 2, left: Math.min(rect.left, window.innerWidth - 260) })
  }

  return (
    <>
      <button ref={btnRef} className="sn-sum-btn" aria-expanded={open} onClick={toggleOpen}>
        汇总 {members.filter((m) => !isSum(m)).length}/{candidates.length} ▾
      </button>
      {open && createPortal(
        <div className="sn-sum-menu" ref={menuRef} style={{ top: pos.top, left: pos.left }}>
          {candidates.map(({ name, i }) => (
            <label key={i}>
              <input type="checkbox" checked={members.includes(i)} onChange={() => onToggle(i)} />
              <span>{name || '（空）'}</span>
            </label>
          ))}
          {!candidates.length && <span className="sn-ref-none">没有可汇总的行/列</span>}
        </div>,
        document.body
      )}
    </>
  )
}
