import { useRef, useState } from 'react'

function ExcelSearch({ token, onAuthError }) {
  const [path, setPath] = useState('')
  const [queryText, setQueryText] = useState('')
  const [exact, setExact] = useState(false)

  const [structure, setStructure] = useState(null) // { files, errors, fileCount }
  const [selected, setSelected] = useState(() => new Set()) // keys: `${file}\u0000${sheet}`
  const [collapsed, setCollapsed] = useState(() => new Set()) // collapsed file keys
  const [colExpanded, setColExpanded] = useState(() => new Set()) // sheet keys whose column panel is open
  const [colSel, setColSel] = useState(() => new Map()) // sheetKey -> Set(letters); absent = all columns

  const [loadingStruct, setLoadingStruct] = useState(false)
  const [loadingSearch, setLoadingSearch] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [dragOver, setDragOver] = useState(false)

  const configInputRef = useRef(null)

  const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {})

  const sheetKey = (file, sheet) => `${file}\u0000${sheet}`

  const baseName = (p) => (p ? String(p).split(/[\\/]/).pop() : '')

  // Apply a saved selection to the currently loaded structure, matching by
  // FILE NAME (not absolute path) + sheet + columns. This keeps configs portable
  // across different folders. Returns { matched, missing } counts.
  const applySelectionsByName = (data, sels) => {
    const byName = new Map() // fileName -> [{ file, sheetMap }]
    for (const f of data.files) {
      const sheetMap = new Map()
      for (const s of f.sheets) sheetMap.set(s.name, s.columns.map((c) => c.letter))
      if (!byName.has(f.fileName)) byName.set(f.fileName, [])
      byName.get(f.fileName).push({ file: f.file, sheetMap })
    }
    const sel = new Set()
    const cs = new Map()
    let matched = 0
    let missing = 0
    for (const item of sels) {
      const name = item.fileName || baseName(item.file)
      const sheet = item.sheet
      const targets = byName.get(name)
      let any = false
      if (targets) {
        for (const t of targets) {
          if (!t.sheetMap.has(sheet)) continue
          any = true
          const key = sheetKey(t.file, sheet)
          sel.add(key)
          if (item.columns && item.columns.length) {
            const allLetters = t.sheetMap.get(sheet)
            const valid = item.columns.filter((l) => allLetters.includes(l))
            if (valid.length) cs.set(key, new Set(valid))
          }
        }
      }
      if (any) matched += 1
      else missing += 1
    }
    setSelected(sel)
    setColSel(cs)
    return { matched, missing }
  }

  // Core loader. Always selects every sheet by default; the user can then import
  // a sheet/column config to refine the selection.
  const loadStructure = async (targetPath) => {
    if (!targetPath.trim()) return
    setError('')
    setResult(null)
    setStructure(null)
    setSelected(new Set())
    setCollapsed(new Set())
    setColExpanded(new Set())
    setColSel(new Map())
    setLoadingStruct(true)
    try {
      const res = await fetch('/api/excel-search/structure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ path: targetPath.trim() }),
      })
      if (res.status === 401) {
        onAuthError && onAuthError()
        return
      }
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '读取失败')
      setStructure(data)
      // default: select every sheet
      const all = new Set()
      for (const f of data.files) for (const s of f.sheets) all.add(sheetKey(f.file, s.name))
      setSelected(all)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoadingStruct(false)
    }
  }

  const handleLoadStructure = () => loadStructure(path)

  const toggleSheet = (file, sheet) => {
    const key = sheetKey(file, sheet)
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const fileSelectState = (f) => {
    const total = f.sheets.length
    const sel = f.sheets.filter((s) => selected.has(sheetKey(f.file, s.name))).length
    if (sel === 0) return 'none'
    if (sel === total) return 'all'
    return 'partial'
  }

  const toggleFile = (f) => {
    const state = fileSelectState(f)
    setSelected((prev) => {
      const next = new Set(prev)
      if (state === 'all') {
        for (const s of f.sheets) next.delete(sheetKey(f.file, s.name))
      } else {
        for (const s of f.sheets) next.add(sheetKey(f.file, s.name))
      }
      return next
    })
  }

  const toggleCollapse = (file) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(file)) next.delete(file)
      else next.add(file)
      return next
    })
  }

  const selectAll = () => {
    if (!structure) return
    const all = new Set()
    for (const f of structure.files) for (const s of f.sheets) all.add(sheetKey(f.file, s.name))
    setSelected(all)
  }

  const clearAll = () => setSelected(new Set())

  // --- column selection helpers (per sheet) ---
  const toggleColExpand = (key) => {
    setColExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // a column is checked unless it was explicitly deselected
  const isColChecked = (key, letter) => {
    const set = colSel.get(key)
    return set ? set.has(letter) : true
  }

  const toggleCol = (key, letter, s) => {
    setColSel((prev) => {
      const next = new Map(prev)
      const cur = next.get(key) || new Set(s.columns.map((c) => c.letter))
      const ns = new Set(cur)
      if (ns.has(letter)) ns.delete(letter)
      else ns.add(letter)
      next.set(key, ns)
      return next
    })
  }

  const setAllCols = (key, s, all) => {
    setColSel((prev) => {
      const next = new Map(prev)
      if (all) next.delete(key) // absent = all columns selected (default)
      else next.set(key, new Set())
      return next
    })
  }

  // label like "全部" or "3/8" for a sheet's column selection
  const colSummary = (key, s) => {
    const total = s.columns.length
    const set = colSel.get(key)
    if (!set) return `全部 ${total} 列`
    return `${set.size}/${total} 列`
  }

  const buildTargets = () => {
    if (!structure) return []
    const targets = []
    for (const f of structure.files) {
      const sheetTargets = []
      for (const s of f.sheets) {
        const key = sheetKey(f.file, s.name)
        if (!selected.has(key)) continue
        const allLetters = s.columns.map((c) => c.letter)
        const set = colSel.get(key)
        let columns
        if (!set) {
          columns = [] // all columns
        } else {
          const chosen = allLetters.filter((l) => set.has(l))
          if (chosen.length === 0) continue // user deselected every column -> skip sheet
          columns = chosen.length === allLetters.length ? [] : chosen
        }
        sheetTargets.push({ name: s.name, columns })
      }
      if (sheetTargets.length) targets.push({ file: f.file, sheets: sheetTargets })
    }
    return targets
  }

  // ---- download helper ----
  const downloadBlob = (filename, content, mime) => {
    const blob = new Blob([content], { type: mime })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  // ---- config export / import ----
  const exportConfig = () => {
    if (!structure) return
    const selections = []
    for (const f of structure.files) {
      for (const s of f.sheets) {
        const key = sheetKey(f.file, s.name)
        if (!selected.has(key)) continue
        const allLetters = s.columns.map((c) => c.letter)
        const set = colSel.get(key)
        let columns = [] // [] = all columns
        if (set) {
          const chosen = allLetters.filter((l) => set.has(l))
          if (chosen.length === 0) continue // none selected -> skip
          if (chosen.length !== allLetters.length) columns = chosen
        }
        // keyed by file NAME (not absolute path) so the config is path-independent
        selections.push({ fileName: f.fileName, sheet: s.name, columns })
      }
    }
    const config = {
      type: 'excel-search-config',
      version: 2,
      savedAt: new Date().toISOString(),
      exact,
      selections,
    }
    downloadBlob('excel-search-config.json', JSON.stringify(config, null, 2), 'application/json')
  }

  const handleImportFile = async (e) => {
    const file = e.target.files && e.target.files[0]
    e.target.value = '' // allow re-importing the same file
    if (!file) return
    await importConfigFromFile(file)
  }

  const importConfigFromFile = async (file) => {
    if (!file) return
    if (!/\.json$/i.test(file.name) && file.type !== 'application/json') {
      setError('请拖入一个 .json 配置文件')
      return
    }
    if (!structure) {
      setError('请先选择路径并「读取结构」，再导入 sheet/列 配置')
      return
    }
    let cfg
    try {
      cfg = JSON.parse(await file.text())
    } catch {
      setError('配置文件解析失败（不是有效的 JSON）')
      return
    }
    if (!cfg || cfg.type !== 'excel-search-config') {
      setError('不是有效的 Excel 搜索配置文件')
      return
    }
    if (cfg.exact != null) setExact(!!cfg.exact)
    const { matched, missing } = applySelectionsByName(structure, cfg.selections || [])
    if (missing > 0) {
      setError(`配置已应用：${matched} 项匹配，${missing} 项在当前路径下未找到对应文件/sheet（已跳过）`)
    } else {
      setError('')
    }
  }

  const handleConfigDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files && e.dataTransfer.files[0]
    if (file) importConfigFromFile(file)
  }

  // ---- results export (CSV, UTF-8 BOM so Excel reads Chinese correctly) ----
  const csvCell = (v) => {
    const s = v == null ? '' : String(v)
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
  }

  const exportDetailCsv = () => {
    if (!result || !result.matches.length) return
    const rows = [['字符串', '文件', 'Sheet', '单元格', '内容']]
    for (const m of orderedMatches) rows.push([m.query, m.fileName, m.sheet, m.cell, m.value])
    const csv = '\uFEFF' + rows.map((r) => r.map(csvCell).join(',')).join('\r\n')
    downloadBlob('excel-search-results.csv', csv, 'text/csv;charset=utf-8')
  }

  const exportSummaryCsv = () => {
    if (!result) return
    const rows = [['字符串', '出现的文件']]
    for (const r of filesByQuery) rows.push([r.query, r.files.length ? r.files.join('，') : '未出现'])
    const csv = '\uFEFF' + rows.map((r) => r.map(csvCell).join(',')).join('\r\n')
    downloadBlob('excel-search-summary.csv', csv, 'text/csv;charset=utf-8')
  }

  // ---- results export (single .xlsx with 明细 + 汇总 sheets, built by backend) ----
  const exportXlsx = async () => {
    if (!result) return
    const matches = orderedMatches.map((m) => ({
      query: m.query,
      fileName: m.fileName,
      sheet: m.sheet,
      cell: m.cell,
      value: m.value == null ? '' : String(m.value),
    }))
    const summary = filesByQuery.map((r) => ({
      query: r.query,
      files: r.files.length ? r.files.join('，') : '未出现',
    }))
    try {
      const res = await fetch('/api/excel-search/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ matches, summary }),
      })
      if (res.status === 401) {
        onAuthError && onAuthError()
        return
      }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || '导出失败')
      }
      const blob = await res.blob()
      downloadBlob('excel-search-result.xlsx', blob, blob.type)
    } catch (err) {
      setError(err.message)
    }
  }

  const selectedCount = selected.size

  // Parse the textarea into a deduped list of search strings (one per line).
  const parsedQueries = (() => {
    const out = []
    const seen = new Set()
    for (const rawLine of queryText.split(/\r?\n/)) {
      const term = rawLine.trim()
      if (term && !seen.has(term)) {
        seen.add(term)
        out.push(term)
      }
    }
    return out
  })()

  const handleSearch = async () => {
    if (!parsedQueries.length) return
    const targets = buildTargets()
    if (!targets.length) {
      setError('请至少选择一个 sheet')
      return
    }
    setError('')
    setResult(null)
    setLoadingSearch(true)
    try {
      const res = await fetch('/api/excel-search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ queries: parsedQueries, exact, targets }),
      })
      if (res.status === 401) {
        onAuthError && onAuthError()
        return
      }
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '搜索失败')
      setResult(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoadingSearch(false)
    }
  }

  // Group matches by file for readability
  const groups = (() => {
    if (!result) return []
    const map = new Map()
    for (const m of result.matches) {
      if (!map.has(m.file)) map.set(m.file, { file: m.file, fileName: m.fileName, items: [] })
      map.get(m.file).items.push(m)
    }
    return Array.from(map.values())
  })()

  // For each query string, the distinct file names it appeared in (in order).
  const filesByQuery = (() => {
    if (!result) return []
    const map = new Map()
    for (const q of result.queries || []) map.set(q, [])
    for (const m of result.matches) {
      if (!map.has(m.query)) map.set(m.query, [])
      const arr = map.get(m.query)
      if (!arr.includes(m.fileName)) arr.push(m.fileName)
    }
    return Array.from(map.entries()).map(([query, files]) => ({ query, files }))
  })()

  // Matches ordered by the user's input order of query strings (stable within each).
  const orderedMatches = (() => {
    if (!result) return []
    const order = result.queries || []
    const rank = new Map(order.map((q, i) => [q, i]))
    return result.matches
      .map((m, i) => ({ m, i }))
      .sort((a, b) => {
        const ra = rank.has(a.m.query) ? rank.get(a.m.query) : order.length
        const rb = rank.has(b.m.query) ? rank.get(b.m.query) : order.length
        return ra - rb || a.i - b.i
      })
      .map((x) => x.m)
  })()

  return (
    <>
      <h2 className="tool-page-title">🔎 Excel 字符串定位</h2>

      {/* Step 1: read structure */}
      <div className="input-section">
        <p className="tool-description">
          <strong>第一步</strong>：输入文件夹路径，读取该路径（含子文件夹）下所有
          <code>.xlsx / .xlsm</code> 的 sheet 结构。
        </p>
        <label className="field-label">文件夹路径</label>
        <div className="url-row">
          <input
            type="text"
            placeholder="例如 C:\\Users\\you\\Documents\\batches"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !loadingStruct) handleLoadStructure()
            }}
            disabled={loadingStruct}
          />
          <button onClick={handleLoadStructure} disabled={loadingStruct || !path.trim()}>
            {loadingStruct ? '读取中...' : '读取结构'}
          </button>
        </div>
        <input
          ref={configInputRef}
          type="file"
          accept="application/json,.json"
          style={{ display: 'none' }}
          onChange={handleImportFile}
        />
      </div>

      {error && (
        <div className="progress-section">
          <div className="progress-log">
            <div className="log-entry log-error">
              <span className="log-icon">✕</span>
              <span className="log-message">{error}</span>
            </div>
          </div>
        </div>
      )}

      {/* Step 2: pick sheets */}
      {structure && (
        <div className="input-section" style={{ marginTop: 16 }}>
          <p className="tool-description">
            <strong>第二步</strong>：勾选需要搜索的 sheet（共 {structure.fileCount} 个文件）。已选{' '}
            <strong>{selectedCount}</strong> 个 sheet。
          </p>

          {structure.fileCount === 0 ? (
            <p className="category-empty">该路径下没有找到 Excel 文件。</p>
          ) : (
            <>
              <div style={{ display: 'flex', gap: 10, marginBottom: 10 }}>
                <button className="btn-outline btn-sm" onClick={selectAll}>
                  全选
                </button>
                <button className="btn-outline btn-sm" onClick={clearAll}>
                  全不选
                </button>
                <button className="btn-outline btn-sm" onClick={exportConfig} disabled={selectedCount === 0}>
                  ⬇ 导出配置
                </button>
              </div>

              <div
                className={`excel-dropzone${dragOver ? ' dragover' : ''}`}
                style={{ marginBottom: 12 }}
                onClick={() => configInputRef.current && configInputRef.current.click()}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragOver(true)
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleConfigDrop}
              >
                <span className="excel-dropzone-icon">⬆</span>
                <span>
                  把 <strong>.json 配置</strong>拖到这里，或<strong>点击选择</strong>导入
                </span>
                <span className="excel-dropzone-hint">
                  按「文件名 + sheet + 列」匹配当前路径下的文件，与原路径无关
                </span>
              </div>

              <div className="excel-tree">
                {structure.files.map((f) => {
                  const state = fileSelectState(f)
                  const isCollapsed = collapsed.has(f.file)
                  return (
                    <div key={f.file} className="excel-tree-file">
                      <div className="excel-tree-file-head">
                        <input
                          type="checkbox"
                          checked={state === 'all'}
                          ref={(el) => {
                            if (el) el.indeterminate = state === 'partial'
                          }}
                          onChange={() => toggleFile(f)}
                        />
                        <button
                          type="button"
                          className="excel-tree-toggle"
                          onClick={() => toggleCollapse(f.file)}
                          title={isCollapsed ? '展开' : '折叠'}
                        >
                          {isCollapsed ? '▸' : '▾'}
                        </button>
                        <span className="excel-tree-filename">
                          📄 {f.fileName}
                          {f.relDir && f.relDir !== '.' && (
                            <span className="excel-tree-reldir"> · {f.relDir}</span>
                          )}
                        </span>
                        <span className="excel-tree-count">{f.sheets.length} sheets</span>
                      </div>
                      {!isCollapsed && (
                        <div className="excel-tree-sheets">
                          {f.sheets.map((s) => {
                            const key = sheetKey(f.file, s.name)
                            const sheetOn = selected.has(key)
                            const colsOpen = colExpanded.has(key)
                            return (
                              <div key={s.name} className="excel-tree-sheet-block">
                                <div className="excel-tree-sheet-row">
                                  <label className="excel-tree-sheet">
                                    <input
                                      type="checkbox"
                                      checked={sheetOn}
                                      onChange={() => toggleSheet(f.file, s.name)}
                                    />
                                    <span>{s.name}</span>
                                  </label>
                                  {s.columns.length > 0 && (
                                    <button
                                      type="button"
                                      className="excel-col-toggle"
                                      onClick={() => toggleColExpand(key)}
                                      disabled={!sheetOn}
                                      title="选择要搜索的列"
                                    >
                                      列：{colSummary(key, s)} {colsOpen ? '▴' : '▾'}
                                    </button>
                                  )}
                                </div>
                                {colsOpen && sheetOn && (
                                  <div className="excel-tree-cols">
                                    <div className="excel-col-actions">
                                      <button
                                        type="button"
                                        className="btn-outline btn-sm"
                                        onClick={() => setAllCols(key, s, true)}
                                      >
                                        全选列
                                      </button>
                                      <button
                                        type="button"
                                        className="btn-outline btn-sm"
                                        onClick={() => setAllCols(key, s, false)}
                                      >
                                        清空列
                                      </button>
                                    </div>
                                    <div className="excel-col-list">
                                      {s.columns.map((c) => (
                                        <label key={c.letter} className="excel-col-item">
                                          <input
                                            type="checkbox"
                                            checked={isColChecked(key, c.letter)}
                                            onChange={() => toggleCol(key, c.letter, s)}
                                          />
                                          <span className="excel-col-letter">{c.letter}</span>
                                          <span className="excel-col-header">{c.header || '（空）'}</span>
                                        </label>
                                      ))}
                                    </div>
                                  </div>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>

              {structure.errors && structure.errors.length > 0 && (
                <details style={{ marginTop: 10 }}>
                  <summary style={{ cursor: 'pointer', color: '#b08900', fontSize: '0.85rem' }}>
                    {structure.errors.length} 个文件无法读取
                  </summary>
                  <ul style={{ fontSize: '0.8rem', color: '#999', marginTop: 6 }}>
                    {structure.errors.map((e, i) => (
                      <li key={i} style={{ wordBreak: 'break-all' }}>
                        {e.file} — {e.error}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </div>
      )}

      {/* Step 3: search */}
      {structure && structure.fileCount > 0 && (
        <div className="input-section" style={{ marginTop: 16 }}>
          <p className="tool-description">
            <strong>第三步</strong>：输入要搜索的字符串（<strong>每行一个</strong>，可一次粘贴多个），在已选 sheet 中搜索。
          </p>
          <label className="field-label">搜索字符串（每行一个）</label>
          <textarea
            className="excel-query-textarea"
            rows={8}
            placeholder={'例如：\nITALSCUSOR\nCOMEWASOXM\nSANMAIMTXT'}
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            disabled={loadingSearch}
          />
          <label
            style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, fontSize: '0.88rem', color: '#555' }}
          >
            <input type="checkbox" checked={exact} onChange={(e) => setExact(e.target.checked)} disabled={loadingSearch} />
            精确整格匹配（不勾选则不区分大小写子串匹配）
          </label>

          {parsedQueries.length > 0 && (
            <p className="tool-description" style={{ marginTop: 10 }}>
              将搜索 <strong>{parsedQueries.length}</strong> 个字符串：
              <span style={{ color: '#888' }}>
                {parsedQueries.slice(0, 8).join('、')}
                {parsedQueries.length > 8 && ` …等 ${parsedQueries.length} 个`}
              </span>
            </p>
          )}

          <div className="url-row" style={{ marginTop: 10 }}>
            <button
              onClick={handleSearch}
              disabled={loadingSearch || parsedQueries.length === 0 || selectedCount === 0}
            >
              {loadingSearch ? '搜索中...' : `搜索${parsedQueries.length ? ` (${parsedQueries.length})` : ''}`}
            </button>
          </div>
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="input-section" style={{ marginTop: 16 }}>
          <p className="tool-description">
            搜索 <strong>{(result.queries || []).length}</strong> 个字符串，扫描{' '}
            <strong>{result.sheetsScanned}</strong> 个 sheet（{result.filesScanned} 个文件），命中{' '}
            <strong>{result.matchCount}</strong> 处
            {result.truncated && '（已达上限，结果被截断）'}。
          </p>

          <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
            <button className="btn-primary btn-sm" onClick={exportXlsx} disabled={result.matchCount === 0}>
              ⬇ 导出 Excel（明细+汇总）
            </button>
            <button className="btn-outline btn-sm" onClick={exportDetailCsv} disabled={result.matchCount === 0}>
              ⬇ 导出明细 (CSV)
            </button>
            <button className="btn-outline btn-sm" onClick={exportSummaryCsv}>
              ⬇ 导出汇总 (CSV)
            </button>
          </div>

          {result.summary && result.summary.length > 1 && (
            <div style={{ marginBottom: 14 }}>
              <div className="field-label">按字符串汇总</div>
              <table className="excel-search-table">
                <thead>
                  <tr>
                    <th>字符串</th>
                    <th>命中数</th>
                  </tr>
                </thead>
                <tbody>
                  {result.summary.map((s) => (
                    <tr key={s.query} className={s.count === 0 ? 'excel-row-notfound' : undefined}>
                      <td style={{ fontFamily: 'monospace' }}>{s.query}</td>
                      <td>{s.count === 0 ? '未找到' : s.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {result.notFound && result.notFound.length > 0 && (
                <p className="tool-description" style={{ marginTop: 8, color: '#c0392b' }}>
                  未找到 {result.notFound.length} 个：{result.notFound.join('、')}
                </p>
              )}
            </div>
          )}

          {result.matchCount === 0 ? (
            <p className="category-empty">未找到任何字符串。</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {groups.map((g) => (
                <div key={g.file}>
                  <div style={{ fontWeight: 700, fontSize: '0.9rem', marginBottom: 6, wordBreak: 'break-all' }}>
                    📄 {g.fileName}{' '}
                    <span style={{ fontWeight: 400, color: '#999', fontSize: '0.8rem' }}>
                      （{g.items.length} 处）
                    </span>
                  </div>
                  <table className="excel-search-table">
                    <thead>
                      <tr>
                        <th>字符串</th>
                        <th>Sheet</th>
                        <th>单元格</th>
                        <th>内容</th>
                      </tr>
                    </thead>
                    <tbody>
                      {g.items.map((m, i) => (
                        <tr key={i}>
                          <td style={{ fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{m.query}</td>
                          <td>{m.sheet}</td>
                          <td style={{ fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{m.cell}</td>
                          <td style={{ wordBreak: 'break-all' }}>{m.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          )}

          {result.errors && result.errors.length > 0 && (
            <details style={{ marginTop: 12 }}>
              <summary style={{ cursor: 'pointer', color: '#b08900', fontSize: '0.85rem' }}>
                {result.errors.length} 个文件无法读取
              </summary>
              <ul style={{ fontSize: '0.8rem', color: '#999', marginTop: 6 }}>
                {result.errors.map((e, i) => (
                  <li key={i} style={{ wordBreak: 'break-all' }}>
                    {e.file} — {e.error}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div style={{ marginTop: 18 }}>
            <div className="field-label">每个字符串出现的文件</div>
            <table className="excel-search-table">
              <thead>
                <tr>
                  <th>字符串</th>
                  <th>出现的文件</th>
                </tr>
              </thead>
              <tbody>
                {filesByQuery.map((r) => (
                  <tr key={r.query} className={r.files.length === 0 ? 'excel-row-notfound' : undefined}>
                    <td style={{ fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{r.query}</td>
                    <td style={{ wordBreak: 'break-all' }}>
                      {r.files.length === 0 ? '未出现' : r.files.join('，')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}

export default ExcelSearch
