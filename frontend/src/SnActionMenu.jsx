import { useEffect, useRef, useState } from 'react'

// A small dropdown menu button. `items` are { label, onClick, disabled?, danger?, sep? }.
export default function ActionMenu({ title, items, disabled, align = 'right', label = '⋯' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  useEffect(() => { if (disabled) setOpen(false) }, [disabled])

  return (
    <div className="sn-menu-wrap" ref={ref}>
      <button
        className="btn-outline btn-sm sn-menu-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={title}
        title={title}
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
      >
        {label}
      </button>
      {open && (
        <div className={`sn-menu sn-menu-${align}`} role="menu">
          {items.map((it, i) =>
            it.sep ? (
              <div key={i} className="sn-menu-sep" />
            ) : (
              <button
                key={i}
                role="menuitem"
                className={`sn-menu-item ${it.danger ? 'sn-danger' : ''}`}
                disabled={it.disabled}
                onClick={() => { setOpen(false); it.onClick() }}
              >
                {it.label}
              </button>
            )
          )}
        </div>
      )}
    </div>
  )
}
