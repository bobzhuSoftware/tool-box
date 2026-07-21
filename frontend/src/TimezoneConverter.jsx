import { useEffect, useMemo, useRef, useState } from 'react'
import './TimezoneConverter.css'

// --- Timezone helpers (pure browser Intl, no dependencies) --------------------

// All IANA timezone names supported by the runtime. Fallback to a small list on
// very old browsers that lack Intl.supportedValuesOf.
function getAllZones() {
  try {
    if (typeof Intl.supportedValuesOf === 'function') {
      return Intl.supportedValuesOf('timeZone')
    }
  } catch {
    /* ignore */
  }
  return [
    'UTC',
    'Asia/Shanghai',
    'Asia/Kolkata',
    'Europe/Berlin',
    'Europe/London',
    'America/New_York',
    'America/Los_Angeles',
  ]
}

// Friendly Chinese labels for the most common zones (used for quick buttons and
// nicer display). Any zone not listed just shows its IANA name.
const ZONE_LABELS = {
  'Asia/Shanghai': '北京 / 上海',
  'Asia/Kolkata': '印度（孟买）',
  'Asia/Tokyo': '东京',
  'Asia/Singapore': '新加坡',
  'Asia/Dubai': '迪拜',
  'Europe/Berlin': '柏林 / 法兰克福',
  'Europe/Paris': '巴黎',
  'Europe/London': '伦敦',
  'Europe/Amsterdam': '阿姆斯特丹',
  'Europe/Warsaw': '华沙',
  'America/New_York': '纽约',
  'America/Chicago': '芝加哥',
  'America/Los_Angeles': '洛杉矶',
  UTC: 'UTC（协调世界时）',
}

// Quick-pick zones surfaced as buttons.
const QUICK_ZONES = ['Asia/Shanghai', 'Europe/Berlin', 'Asia/Kolkata', 'Europe/London', 'UTC']

// Common time-zone abbreviations used in emails / meeting invites -> zone(s).
// The first zone listed is the business-context default. CET & CEST (and other
// standard/daylight pairs) map to the same IANA zone; the tool auto-applies the
// correct offset for the selected date, so you never pick the wrong season.
const ABBREV_ZONES = {
  CET: ['Europe/Berlin', 'Europe/Paris'],
  CEST: ['Europe/Berlin', 'Europe/Paris'],
  WET: ['Europe/Lisbon'],
  WEST: ['Europe/Lisbon'],
  EET: ['Europe/Athens', 'Europe/Helsinki'],
  EEST: ['Europe/Athens', 'Europe/Helsinki'],
  GMT: ['Europe/London', 'UTC'],
  BST: ['Europe/London'],
  UTC: ['UTC'],
  IST: ['Asia/Kolkata'],
  GST: ['Asia/Dubai'],
  JST: ['Asia/Tokyo'],
  KST: ['Asia/Seoul'],
  SGT: ['Asia/Singapore'],
  HKT: ['Asia/Hong_Kong'],
  CST: ['Asia/Shanghai', 'America/Chicago'],
  AEST: ['Australia/Sydney'],
  AEDT: ['Australia/Sydney'],
  EST: ['America/New_York'],
  EDT: ['America/New_York'],
  CDT: ['America/Chicago'],
  MST: ['America/Denver'],
  MDT: ['America/Denver'],
  PST: ['America/Los_Angeles'],
  PDT: ['America/Los_Angeles'],
}

// Reverse index: IANA zone -> list of abbreviations that point at it.
const ZONE_ABBREVS = (() => {
  const map = {}
  for (const [ab, zones] of Object.entries(ABBREV_ZONES)) {
    for (const z of zones) {
      if (!map[z]) map[z] = []
      if (!map[z].includes(ab)) map[z].push(ab)
    }
  }
  return map
})()

// Country / city common-name aliases (English + 中文) -> zone. IANA names are
// city-based (e.g. Asia/Shanghai), so searching a COUNTRY like "China" / "中国"
// or "Germany" / "德国" would otherwise fail. These make such searches work.
const ZONE_ALIASES = {
  'Asia/Shanghai': ['china', '中国', '中國', 'prc', 'beijing', '北京', 'shanghai', '上海'],
  'Asia/Kolkata': ['india', '印度', 'mumbai', '孟买', 'delhi', '新德里', 'kolkata', 'bengaluru', 'bangalore', '班加罗尔', 'hyderabad'],
  'Europe/Berlin': ['germany', '德国', 'berlin', '柏林', 'frankfurt', '法兰克福', 'munich', '慕尼黑'],
  'Europe/Paris': ['france', '法国', 'paris', '巴黎'],
  'Europe/London': ['uk', 'united kingdom', 'england', 'britain', '英国', 'london', '伦敦'],
  'Europe/Amsterdam': ['netherlands', '荷兰', 'holland', 'amsterdam', '阿姆斯特丹'],
  'Europe/Warsaw': ['poland', '波兰', 'warsaw', '华沙'],
  'Europe/Madrid': ['spain', '西班牙', 'madrid', '马德里'],
  'Europe/Rome': ['italy', '意大利', 'rome', '罗马', 'milan', '米兰'],
  'Europe/Zurich': ['switzerland', '瑞士', 'zurich', '苏黎世'],
  'Europe/Brussels': ['belgium', '比利时', 'brussels', '布鲁塞尔'],
  'Europe/Vienna': ['austria', '奥地利', 'vienna', '维也纳'],
  'Europe/Stockholm': ['sweden', '瑞典', 'stockholm', '斯德哥尔摩'],
  'Europe/Moscow': ['russia', '俄罗斯', 'moscow', '莫斯科'],
  'Europe/Athens': ['greece', '希腊', 'athens', '雅典'],
  'Europe/Lisbon': ['portugal', '葡萄牙', 'lisbon', '里斯本'],
  'Europe/Helsinki': ['finland', '芬兰', 'helsinki', '赫尔辛基'],
  'Europe/Dublin': ['ireland', '爱尔兰', 'dublin', '都柏林'],
  'Asia/Dubai': ['uae', '阿联酋', 'dubai', '迪拜', 'abu dhabi', '阿布扎比'],
  'Asia/Tokyo': ['japan', '日本', 'tokyo', '东京'],
  'Asia/Seoul': ['korea', 'south korea', '韩国', 'seoul', '首尔'],
  'Asia/Singapore': ['singapore', '新加坡'],
  'Asia/Hong_Kong': ['hong kong', '香港', 'hk'],
  'Asia/Taipei': ['taiwan', '台湾', '臺灣', 'taipei', '台北'],
  'Asia/Bangkok': ['thailand', '泰国', 'bangkok', '曼谷'],
  'Asia/Jakarta': ['indonesia', '印尼', '印度尼西亚', 'jakarta', '雅加达'],
  'Asia/Kuala_Lumpur': ['malaysia', '马来西亚', 'kuala lumpur', '吉隆坡'],
  'Asia/Manila': ['philippines', '菲律宾', 'manila', '马尼拉'],
  'Asia/Ho_Chi_Minh': ['vietnam', '越南', 'ho chi minh', '胡志明', 'saigon'],
  'America/New_York': ['usa', 'us', 'united states', '美国', 'new york', '约', '约约', 'east coast', '美东'],
  'America/Chicago': ['chicago', '芬加哥', '美中'],
  'America/Denver': ['denver', '丹佛'],
  'America/Los_Angeles': ['los angeles', '洛杉矶', 'san francisco', '旧金山', 'california', '加州', 'west coast', '美西'],
  'America/Sao_Paulo': ['brazil', '巴西', 'sao paulo', '圣保罗'],
  'America/Toronto': ['canada', '加拿大', 'toronto', '多伦多'],
  'America/Mexico_City': ['mexico', '墨西哥', 'mexico city', '墨西哥城'],
  'Australia/Sydney': ['australia', '澳大利亚', '澳洲', 'sydney', '悉尼', 'melbourne', '墨尔本'],
  'Pacific/Auckland': ['new zealand', '新西兰', 'auckland', '奥克兰'],
  UTC: ['utc', 'gmt', '协调世界时', '世界时', '格林尼治'],
}

// Return the short UTC offset label for a zone at a given instant, e.g. "GMT+8".
function offsetLabel(timeZone, date) {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      timeZoneName: 'shortOffset',
    }).formatToParts(date)
    const off = parts.find((p) => p.type === 'timeZoneName')?.value
    return off ? off.replace('GMT', 'UTC') : ''
  } catch {
    return ''
  }
}

// Human label for a zone dropdown/option including its live offset and any
// well-known abbreviations (IST / CET ...), so email codes are easy to confirm.
function zoneLabel(timeZone, date) {
  const friendly = ZONE_LABELS[timeZone]
  const off = offsetLabel(timeZone, date)
  const abbr = ZONE_ABBREVS[timeZone]
  const base = friendly ? `${friendly} · ${timeZone}` : timeZone
  let s = off ? `${base}（${off}）` : base
  if (abbr && abbr.length) s = `${s} [${abbr.join('/')}]`
  return s
}

// Convert a "wall clock" time entered for `timeZone` into a real UTC instant.
// dateStr is a datetime-local string: "YYYY-MM-DDTHH:mm".
function zonedWallTimeToUtc(dateStr, timeZone) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(dateStr)
  if (!m) return null
  const [, y, mo, d, h, mi] = m.map(Number)
  // Treat the wall time as if it were UTC, then correct by the zone's offset.
  const utcGuess = Date.UTC(y, mo - 1, d, h, mi)
  const asZoned = new Date(new Date(utcGuess).toLocaleString('en-US', { timeZone }))
  const asUtc = new Date(new Date(utcGuess).toLocaleString('en-US', { timeZone: 'UTC' }))
  const offset = asZoned.getTime() - asUtc.getTime()
  return new Date(utcGuess - offset)
}

// Format a real instant as the local date/time in a given zone.
function formatInZone(date, timeZone) {
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone,
      dateStyle: 'full',
      timeStyle: 'short',
      hour12: false,
    }).format(date)
  } catch {
    return ''
  }
}

// Compact form used inside the copy-to-clipboard text, e.g. "2026-07-21 09:00".
function formatCompact(date, timeZone) {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(date)
    const g = (t) => parts.find((p) => p.type === t)?.value || ''
    return `${g('year')}-${g('month')}-${g('day')} ${g('hour')}:${g('minute')}`
  } catch {
    return ''
  }
}

// Turn a Date into the "YYYY-MM-DDTHH:mm" wall-clock string for a given zone,
// suitable for a <input type="datetime-local"> value.
function toLocalInputValue(date, timeZone) {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(date)
    const g = (t) => parts.find((p) => p.type === t)?.value || ''
    return `${g('year')}-${g('month')}-${g('day')}T${g('hour')}:${g('minute')}`
  } catch {
    return ''
  }
}

// Round a "YYYY-MM-DDTHH:mm" wall-clock string to the nearest half hour
// (:00 or :30), since meetings are almost always booked on the hour/half hour.
function roundToHalfHour(dateStr) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(dateStr)
  if (!m) return dateStr
  const [, y, mo, d, h, mi] = m.map(Number)
  // Use a UTC date purely as an arithmetic carry helper (no timezone math here).
  const base = new Date(Date.UTC(y, mo - 1, d, h, 0, 0))
  const rounded = Math.round(mi / 30) * 30 // 0, 30, or 60
  base.setUTCMinutes(rounded)
  const p = (n) => String(n).padStart(2, '0')
  return (
    `${base.getUTCFullYear()}-${p(base.getUTCMonth() + 1)}-${p(base.getUTCDate())}` +
    `T${p(base.getUTCHours())}:${p(base.getUTCMinutes())}`
  )
}

function shortName(timeZone) {
  return ZONE_LABELS[timeZone] || timeZone.split('/').pop().replace(/_/g, ' ')
}

// Half-hour time slots ("00:00" ... "23:30") for the time dropdown. The native
// datetime-local picker ignores `step` for its minute list in some browsers
// (e.g. Edge), so we offer only these values explicitly.
const TIME_SLOTS = (() => {
  const slots = []
  for (let h = 0; h < 24; h++) {
    for (const m of [0, 30]) {
      slots.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
    }
  }
  return slots
})()

// --- Reusable searchable zone picker (combobox) ------------------------------
// A text input that shows a live, clickable dropdown as you type. Selecting an
// option calls onChange. This replaces the old separate search-box + <select>,
// which was confusing (typing filtered a hidden select with no visible result).

function ZonePicker({ value, onChange, allZones, refDate, id, placeholder }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [highlight, setHighlight] = useState(0)
  const rootRef = useRef(null)

  const results = useMemo(() => {
    const raw = query.trim()
    const f = raw.toLowerCase()
    if (!f) return allZones.slice(0, 60)
    const up = raw.toUpperCase()

    // Zones whose abbreviation matches the query (e.g. "IST", "CET", "CEST").
    // Prefix match so "CE" surfaces CET/CEST zones too. These rank first.
    const abbrZones = []
    for (const [ab, zones] of Object.entries(ABBREV_ZONES)) {
      if (ab === up || ab.startsWith(up)) {
        for (const z of zones) if (!abbrZones.includes(z)) abbrZones.push(z)
      }
    }

    // Country / city common-name matches (e.g. "China" / "中国", "Germany" / "德国").
    const aliasZones = []
    for (const [z, aliases] of Object.entries(ZONE_ALIASES)) {
      if (aliases.some((a) => a.includes(f))) {
        if (!aliasZones.includes(z)) aliasZones.push(z)
      }
    }

    // Text matches on IANA name or friendly label.
    const textZones = allZones.filter(
      (z) =>
        z.toLowerCase().includes(f) ||
        (ZONE_LABELS[z] && ZONE_LABELS[z].toLowerCase().includes(f))
    )

    // Merge: abbreviation hits, then country/city aliases, then text; de-duped.
    const seen = new Set()
    const merged = []
    for (const z of [...abbrZones, ...aliasZones, ...textZones]) {
      if (!seen.has(z)) {
        seen.add(z)
        merged.push(z)
      }
    }
    return merged.slice(0, 60) // cap rendered options for performance
  }, [query, allZones])

  // Close when clicking outside.
  useEffect(() => {
    if (!open) return
    const onDocClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  const pick = (z) => {
    onChange(z)
    setQuery('')
    setOpen(false)
  }

  const onKeyDown = (e) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) {
      setOpen(true)
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlight((h) => Math.min(h + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (results[highlight]) pick(results[highlight])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  // While open, show what the user typed; while closed, show the selected zone.
  const displayValue = open ? query : value ? zoneLabel(value, refDate) : ''

  return (
    <div className="tz-picker" ref={rootRef}>
      <input
        id={id}
        type="text"
        className="tz-combo-input"
        placeholder={placeholder || '点击搜索时区（城市 / IANA 名）…'}
        value={displayValue}
        onChange={(e) => {
          setQuery(e.target.value)
          setHighlight(0)
          if (!open) setOpen(true)
        }}
        onFocus={() => {
          setQuery('')
          setOpen(true)
          setHighlight(0)
        }}
        onKeyDown={onKeyDown}
        autoComplete="off"
      />
      <span className="tz-combo-caret" aria-hidden="true">
        ▾
      </span>
      {open && (
        <ul className="tz-combo-list" role="listbox">
          {results.length === 0 ? (
            <li className="tz-combo-empty">未找到匹配的时区</li>
          ) : (
            results.map((z, i) => (
              <li
                key={z}
                role="option"
                aria-selected={z === value}
                className={`tz-combo-option${i === highlight ? ' active' : ''}${
                  z === value ? ' selected' : ''
                }`}
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => {
                  e.preventDefault() // keep focus; avoid blur firing before click
                  pick(z)
                }}
              >
                {zoneLabel(z, refDate)}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}

// --- Main component ----------------------------------------------------------

const LS_KEY = 'timezoneConverter'

function loadPrefs() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return JSON.parse(raw)
  } catch {
    /* ignore */
  }
  return {}
}

function TimezoneConverter() {
  const allZones = useMemo(() => getAllZones(), [])
  const localZone = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
    } catch {
      return 'UTC'
    }
  }, [])

  const prefs = useMemo(() => loadPrefs(), [])

  const [fromZone, setFromZone] = useState(prefs.fromZone || localZone || 'Asia/Shanghai')
  const [toZone, setToZone] = useState(prefs.toZone || 'Europe/Berlin')
  const [extraZones, setExtraZones] = useState(prefs.extraZones || [])
  const [dateStr, setDateStr] = useState(() =>
    roundToHalfHour(toLocalInputValue(new Date(), prefs.fromZone || localZone || 'Asia/Shanghai'))
  )
  const [copied, setCopied] = useState(false)

  // Persist preferences.
  useEffect(() => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ fromZone, toZone, extraZones }))
    } catch {
      /* ignore */
    }
  }, [fromZone, toZone, extraZones])

  // The real instant represented by the entered wall-clock time in the source zone.
  const instant = useMemo(() => zonedWallTimeToUtc(dateStr, fromZone), [dateStr, fromZone])

  const setNow = () => setDateStr(roundToHalfHour(toLocalInputValue(new Date(), fromZone)))

  const swap = () => {
    if (!instant) {
      setFromZone(toZone)
      setToZone(fromZone)
      return
    }
    // Keep the same physical instant: re-express it as wall time in the new source zone.
    const newFrom = toZone
    const newTo = fromZone
    setDateStr(toLocalInputValue(instant, newFrom))
    setFromZone(newFrom)
    setToZone(newTo)
  }

  const addExtra = (z) => {
    if (!z || extraZones.includes(z) || z === fromZone || z === toZone) return
    setExtraZones([...extraZones, z])
  }
  const removeExtra = (z) => setExtraZones(extraZones.filter((x) => x !== z))

  const copyResult = async () => {
    if (!instant) return
    const line = (z) => `${formatCompact(instant, z)} ${shortName(z)}（${offsetLabel(z, instant)}）`
    const rows = [fromZone, toZone, ...extraZones].map(line)
    const text = rows.join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      /* clipboard may be blocked; ignore */
    }
  }

  return (
    <>
      <h2 className="tool-page-title">🌍 时区转换</h2>

      <div className="input-section">
        <p className="tz-intro">
          在两个时区之间快速换算时间（如北京 ↔ 柏林 / 孟买），并可选附加更多时区，方便安排跨时区会议。
        </p>

        {/* Source time */}
        <div className="tz-field">
          <label className="tz-label" htmlFor="tz-datetime">
            源时间
          </label>
          <div className="tz-time-row">
            <input
              id="tz-datetime"
              type="date"
              className="tz-datetime tz-date-input"
              value={dateStr.slice(0, 10)}
              onChange={(e) => {
                const d = e.target.value
                if (d) setDateStr(`${d}T${dateStr.slice(11, 16) || '09:00'}`)
              }}
            />
            <select
              className="tz-time-select"
              value={dateStr.slice(11, 16)}
              onChange={(e) => setDateStr(`${dateStr.slice(0, 10)}T${e.target.value}`)}
            >
              {TIME_SLOTS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button type="button" className="btn-secondary" onClick={setNow}>
              用现在时间
            </button>
          </div>
        </div>

        {/* From / swap / To */}
        <div className="tz-convert-row">
          <div className="tz-field tz-col">
            <label className="tz-label" htmlFor="tz-from">
              从（源时区）
            </label>
            <ZonePicker
              id="tz-from"
              value={fromZone}
              onChange={setFromZone}
              allZones={allZones}
              refDate={instant || new Date()}
            />
          </div>

          <button
            type="button"
            className="tz-swap-btn"
            onClick={swap}
            title="交换源 / 目标时区"
            aria-label="交换时区"
          >
            ⇄
          </button>

          <div className="tz-field tz-col">
            <label className="tz-label" htmlFor="tz-to">
              到（目标时区）
            </label>
            <ZonePicker
              id="tz-to"
              value={toZone}
              onChange={setToZone}
              allZones={allZones}
              refDate={instant || new Date()}
            />
          </div>
        </div>

        {/* Quick zone buttons for the target */}
        <div className="tz-quick">
          <span className="tz-quick-label">快速选择目标：</span>
          {QUICK_ZONES.map((z) => (
            <button
              key={z}
              type="button"
              className={`tz-chip${toZone === z ? ' active' : ''}`}
              onClick={() => setToZone(z)}
            >
              {shortName(z)}
            </button>
          ))}
        </div>
      </div>

      {/* Result */}
      {instant ? (
        <div className="result-section">
          <div className="tz-result-grid">
            <div className="tz-result-card tz-from-card">
              <div className="tz-card-tag">源</div>
              <div className="tz-card-zone">
                {shortName(fromZone)} <span className="tz-card-off">{offsetLabel(fromZone, instant)}</span>
              </div>
              <div className="tz-card-time">{formatInZone(instant, fromZone)}</div>
            </div>

            <div className="tz-arrow">→</div>

            <div className="tz-result-card tz-to-card">
              <div className="tz-card-tag">目标</div>
              <div className="tz-card-zone">
                {shortName(toZone)} <span className="tz-card-off">{offsetLabel(toZone, instant)}</span>
              </div>
              <div className="tz-card-time">{formatInZone(instant, toZone)}</div>
            </div>
          </div>

          <div className="tz-actions">
            <button type="button" className="btn-primary" onClick={copyResult}>
              {copied ? '已复制 ✓' : '复制结果'}
            </button>
          </div>

          {/* Optional: more zones */}
          <details className="tz-extra" open={extraZones.length > 0}>
            <summary>其它时区（可选拓展）</summary>

            <div className="tz-extra-add">
              <ZonePicker
                value=""
                onChange={addExtra}
                allZones={allZones.filter(
                  (z) => z !== fromZone && z !== toZone && !extraZones.includes(z)
                )}
                refDate={instant}
                placeholder="点击添加要附加显示的时区…"
              />
            </div>

            {extraZones.length > 0 && (
              <ul className="tz-extra-list">
                {extraZones.map((z) => (
                  <li key={z} className="tz-extra-item">
                    <div className="tz-extra-zone">
                      {shortName(z)}{' '}
                      <span className="tz-card-off">{offsetLabel(z, instant)}</span>
                    </div>
                    <div className="tz-extra-time">{formatInZone(instant, z)}</div>
                    <button
                      type="button"
                      className="tz-extra-remove"
                      onClick={() => removeExtra(z)}
                      aria-label={`移除 ${z}`}
                      title="移除"
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </details>
        </div>
      ) : (
        <div className="result-section">
          <p className="tz-empty">请选择一个有效的源时间。</p>
        </div>
      )}
    </>
  )
}

export default TimezoneConverter
