import { useEffect, useState } from 'react'
import { clampMinutes, MAX_SPLIT_MINUTES, MIN_SPLIT_MINUTES } from './useSplitMinutes'

const PRESETS = [30, 60, 120, 180]

/**
 * "Split by N minutes" button with an editable, persisted duration.
 * `minutes` / `saveMinutes` come from useSplitMinutes so several controls on a
 * page stay in sync.
 */
function SplitControl({ minutes, saveMinutes, onSplit, label = '✂ Split (ZIP)', unit = 'min / file', prefix, disabled, title }) {
  const [draft, setDraft] = useState(String(minutes))
  useEffect(() => { setDraft(String(minutes)) }, [minutes])

  const commit = () => {
    const v = clampMinutes(draft)
    setDraft(String(v))
    if (v !== minutes) saveMinutes(v)
    return v
  }

  // Without onSplit this renders as a settings-only bar (no download button).
  return (
    <span className="split-controls">
      {onSplit && (
        <button
          className="btn-outline"
          disabled={disabled}
          title={title || 'Download as a ZIP of multiple files, each covering N minutes'}
          onClick={() => onSplit(commit())}
        >
          {label}
        </button>
      )}
      {prefix && <span className="split-unit">{prefix}</span>}
      <input
        type="number"
        min={MIN_SPLIT_MINUTES}
        max={MAX_SPLIT_MINUTES}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') { const v = commit(); onSplit?.(v) } }}
        className="split-input"
        title="Minutes per file (saved automatically)"
      />
      <span className="split-unit">{unit}</span>
      {PRESETS.map((p) => (
        <button
          key={p}
          type="button"
          className={`btn-sm ${p === minutes ? 'btn-primary' : 'btn-outline'}`}
          onClick={() => { setDraft(String(p)); saveMinutes(p) }}
        >
          {p}
        </button>
      ))}
    </span>
  )
}

export default SplitControl
