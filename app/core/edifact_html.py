"""Render a parsed EDIFACT message definition as a single self-contained HTML file.

The page embeds the message data as JSON and renders it client-side (no
external scripts, fonts or ads). Segment details and code lists are stored once
and built lazily, so a message that reuses DTM/RFF/NAD dozens of times stays
small.
"""
import html
import json

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { --bd:#d9dee7; --muted:#667085; --m:#b42318; --c:#475467; --accent:#2563eb; --bg:#f6f8fb; }
* { box-sizing: border-box; }
body { margin:0; font:14px/1.5 "Segoe UI","Microsoft YaHei",system-ui,sans-serif; color:#1d2939; background:var(--bg); }
header { position:sticky; top:0; z-index:5; background:#fff; border-bottom:1px solid var(--bd); padding:10px 20px; }
h1 { margin:0 0 6px; font-size:18px; }
h1 small { font-weight:400; color:var(--muted); margin-left:8px; }
.bar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.bar input[type=search] { width:300px; padding:6px 10px; border:1px solid var(--bd); border-radius:6px; font:inherit; }
.bar button { padding:5px 12px; border:1px solid var(--bd); border-radius:6px; background:#fff; cursor:pointer; font:inherit; }
.bar button:hover { border-color:var(--accent); color:var(--accent); }
.bar label { color:var(--c); cursor:pointer; user-select:none; }
#count { color:var(--muted); }
.legend { margin:6px 0 0; color:var(--muted); font-size:12px; }
main { padding:14px 20px 40px; max-width:1200px; }
.group { margin:4px 0; border-left:3px solid var(--gc, #98a2b3); background:#fff; border-radius:0 6px 6px 0; box-shadow:0 0 0 1px var(--bd); }
.group > .gh { padding:5px 10px; background:#eef2f7; font-weight:600; cursor:pointer; user-select:none; display:flex; gap:10px; align-items:baseline; }
.group > .gh::before { content:"▾"; color:var(--muted); width:12px; }
.group.collapsed > .gh::before { content:"▸"; }
.group.collapsed > .gb { display:none; }
.gb { padding:2px 0 2px 14px; }
.main-seg { margin:4px 0; }
details.seg { border:1px solid var(--bd); border-radius:6px; background:#fff; margin:3px 8px 3px 0; }
details.seg > summary { padding:5px 10px; cursor:pointer; display:flex; gap:10px; align-items:baseline; list-style:none; }
details.seg > summary::-webkit-details-marker { display:none; }
details.seg > summary::before { content:"▸"; color:var(--muted); width:12px; }
details.seg[open] > summary::before { content:"▾"; }
details.seg[open] > summary { background:#f2f6ff; border-bottom:1px solid var(--bd); }
.tag { font:600 14px Consolas,monospace; color:var(--accent); min-width:36px; }
.sname { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sdesc { color:var(--muted); font-size:12px; font-weight:400; }
.gh .sdesc { flex:1; }
.st { font:600 12px Consolas,monospace; padding:0 6px; border-radius:4px; }
.st.M { color:#fff; background:var(--m); }
.st.C { color:var(--c); background:#e4e7ec; }
.st.B { color:#7a5200; background:#fde8b0; }
.rep { font:12px Consolas,monospace; color:var(--muted); min-width:38px; text-align:right; }
.body { padding:8px 12px 12px; }
.body p { margin:0 0 8px; color:var(--c); }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { border:1px solid var(--bd); padding:3px 8px; text-align:left; vertical-align:top; }
th { background:#f2f4f7; font-weight:600; }
tr.comp td { background:#f8fafc; font-weight:600; }
td.ref { font-family:Consolas,monospace; white-space:nowrap; }
td.ind { padding-left:24px; }
details.codes { margin:4px 0 2px; }
details.codes > summary { cursor:pointer; color:var(--accent); font-size:12px; }
details.codes table { margin-top:4px; }
tr.hit td { background:#fff6c2; }
.hidden { display:none !important; }
mark { background:#fde047; padding:0 1px; }
.empty { padding:30px; text-align:center; color:var(--muted); }
footer { padding:10px 20px 30px; color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>__MESSAGE__ <small>UN/EDIFACT __VERSION__ 消息结构</small></h1>
  <div class="bar">
    <input id="q" type="search" placeholder="搜索 段 / 元素 / 代码值（至少 2 个字符）…" autocomplete="off">
    <button id="expandAll">展开全部</button>
    <button id="collapseAll">折叠全部</button>
    <label><input id="onlyM" type="checkbox"> 仅显示必选 (M)</label>
    <span id="count"></span>
  </div>
  <div class="legend">
    <span class="st M">M</span> 必选 &nbsp; <span class="st C">C</span> 条件 &nbsp; <span class="st B">B</span> 数据源中的原始标记 &nbsp;|&nbsp;
    ×N = 最大重复次数 &nbsp;|&nbsp; 格式：an 字母数字 / n 数字 / a 字母，<code>..N</code> 为最大长度（数据源未区分定长）
  </div>
</header>
<main id="root"></main>
<footer>数据来源：UNECE UNTDID __VERSION__（经 php-edifact/edifact-mapping 整理）。由 tool-box 离线生成，无外部依赖。</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  var D = JSON.parse(document.getElementById('data').textContent);
  var root = document.getElementById('root');
  var qEl = document.getElementById('q');
  var onlyM = document.getElementById('onlyM');
  var colors = ['#2563eb', '#16a34a', '#d97706', '#9333ea', '#dc2626', '#0891b2'];
  var hayCache = {};
  var nodes = [];   // {kind, el, seg?, required, children?, parent?}

  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function status(s) { return h('span', 'st ' + (s === 'M' || s === 'C' ? s : 'B'), s); }

  function haystack(segId) {
    if (hayCache[segId] != null) return hayCache[segId];
    var s = D.segments[segId], parts = [segId, s ? s.name : '', s ? s.desc : ''];
    if (s) s.items.forEach(function (it) {
      var els = it.kind === 'element' ? [it] : [it].concat(it.elements);
      els.forEach(function (e) {
        parts.push(e.id, e.name, e.desc);
        (D.codes[e.id] || []).forEach(function (c) { parts.push(c.id, c.title); });
      });
    });
    return (hayCache[segId] = parts.join('\n').toLowerCase());
  }

  function fmt(e) { return e.type ? e.type + (e.maxlength ? '..' + e.maxlength : '') : ''; }

  function elementRows(tb, e, indent, q) {
    var tr = h('tr'), tdRef = h('td', 'ref' + (indent ? ' ind' : ''), e.id);
    tr.appendChild(tdRef);
    var tdName = h('td'); tdName.appendChild(h('div', null, e.name));
    var codes = D.codes[e.id];
    if (codes && codes.length) {
      var hits = q ? codes.filter(function (c) { return (c.id + ' ' + c.title).toLowerCase().indexOf(q) >= 0; }) : [];
      var det = h('details', 'codes');
      det.appendChild(h('summary', null, '代码值（' + codes.length + '）'));
      var ct = h('table'), head = h('tr');
      ['代码', '名称', '说明'].forEach(function (t) { head.appendChild(h('th', null, t)); });
      ct.appendChild(head);
      codes.forEach(function (c) {
        var r = h('tr', hits.indexOf(c) >= 0 ? 'hit' : '');
        r.appendChild(h('td', 'ref', c.id)); r.appendChild(h('td', null, c.title)); r.appendChild(h('td', null, c.desc));
        ct.appendChild(r);
      });
      det.appendChild(ct);
      if (hits.length) det.open = true;
      tdName.appendChild(det);
    }
    tr.appendChild(tdName);
    var tdS = h('td'); tdS.appendChild(status(e.status)); tr.appendChild(tdS);
    tr.appendChild(h('td', 'ref', fmt(e)));
    tr.appendChild(h('td', null, e.desc));
    tb.appendChild(tr);
  }

  function buildBody(segId, q) {
    var s = D.segments[segId], body = h('div', 'body');
    if (!s) { body.appendChild(h('p', null, '数据源中没有该段的定义。')); return body; }
    if (s.desc) body.appendChild(h('p', null, s.desc));
    var tb = h('table'), head = h('tr');
    ['标识', '名称', '状态', '格式', '说明'].forEach(function (t) { head.appendChild(h('th', null, t)); });
    tb.appendChild(head);
    s.items.forEach(function (it) {
      if (it.kind === 'element') { elementRows(tb, it, false, q); return; }
      var tr = h('tr', 'comp');
      tr.appendChild(h('td', 'ref', it.id)); tr.appendChild(h('td', null, it.name));
      var tdS = h('td'); tdS.appendChild(status(it.status)); tr.appendChild(tdS);
      tr.appendChild(h('td')); tr.appendChild(h('td', null, it.desc));
      tb.appendChild(tr);
      it.elements.forEach(function (e) { elementRows(tb, e, true, q); });
    });
    body.appendChild(tb);
    return body;
  }

  function renderSegBody(n) {
    if (!n.el.open) return;
    if (n.body) n.body.remove();
    n.body = buildBody(n.seg, currentQuery());
    n.el.appendChild(n.body);
  }

  function addSegment(parent, node, parentNode) {
    var s = D.segments[node.id];
    var d = h('details', 'seg');
    var sum = h('summary');
    sum.appendChild(h('span', 'tag', node.id));
    var nm = h('span', 'sname', s ? s.name : '');
    if (s && s.desc) nm.appendChild(h('span', 'sdesc', ' — ' + s.desc));
    sum.appendChild(nm);
    sum.appendChild(status(node.required ? 'M' : 'C'));
    sum.appendChild(h('span', 'rep', '×' + node.maxrepeat));
    d.appendChild(sum);
    var n = { kind: 'seg', el: d, seg: node.id, required: node.required, parent: parentNode };
    d.addEventListener('toggle', function () { renderSegBody(n); });
    parent.appendChild(d);
    nodes.push(n);
    return n;
  }

  function addGroup(parent, node, depth, parentNode) {
    var g = h('div', 'group');
    g.style.setProperty('--gc', colors[depth % colors.length]);
    var gh = h('div', 'gh');
    gh.appendChild(h('span', null, node.id));
    if (node.children.length && node.children[0].type === 'segment') {
      gh.appendChild(h('span', 'sdesc', '触发段 ' + node.children[0].id));
    }
    gh.appendChild(status(node.required ? 'M' : 'C'));
    gh.appendChild(h('span', 'rep', '×' + node.maxrepeat));
    gh.addEventListener('click', function () { g.classList.toggle('collapsed'); });
    var gb = h('div', 'gb');
    g.appendChild(gh); g.appendChild(gb);
    parent.appendChild(g);
    var n = { kind: 'group', el: g, required: node.required, children: [], parent: parentNode };
    nodes.push(n);
    build(gb, node.children, depth + 1, n);
    return n;
  }

  function build(parent, list, depth, parentNode) {
    list.forEach(function (node) {
      var n = node.type === 'group' ? addGroup(parent, node, depth, parentNode) : addSegment(parent, node, parentNode);
      if (parentNode) parentNode.children.push(n);
    });
  }

  function currentQuery() {
    var q = qEl.value.trim().toLowerCase();
    return q.length >= 2 ? q : '';
  }

  function visible(n, q, mOnly) {
    if (mOnly && !n.required) return false;
    if (n.kind === 'seg') return !q || haystack(n.seg).indexOf(q) >= 0;
    return n.children.some(function (c) { return visible(c, q, mOnly); });
  }

  function applyFilter() {
    var q = currentQuery(), mOnly = onlyM.checked, shown = 0;
    nodes.forEach(function (n) {
      var v = visible(n, q, mOnly);
      n.el.classList.toggle('hidden', !v);
      if (v && q && n.kind === 'group') n.el.classList.remove('collapsed');
      if (v && n.kind === 'seg') {
        shown++;
        if (q && !n.el.open) n.el.open = true; else renderSegBody(n);
      }
    });
    document.getElementById('count').textContent = shown + ' 个段';
    var none = document.getElementById('none');
    if (!shown) {
      if (!none) { none = h('div', 'empty', '没有匹配的内容'); none.id = 'none'; root.appendChild(none); }
    } else if (none) none.remove();
  }

  build(root, D.structure, 0, null);

  qEl.addEventListener('input', applyFilter);
  onlyM.addEventListener('change', applyFilter);
  document.getElementById('expandAll').addEventListener('click', function () {
    nodes.forEach(function (n) {
      if (n.kind === 'group') n.el.classList.remove('collapsed');
      else if (!n.el.classList.contains('hidden')) n.el.open = true;
    });
  });
  document.getElementById('collapseAll').addEventListener('click', function () {
    nodes.forEach(function (n) {
      if (n.kind === 'seg') n.el.open = false;
    });
  });
  applyFilter();
})();
</script>
</body>
</html>
"""


def render_html(data: dict) -> str:
    """Build the standalone HTML page for a message returned by ``load_message``."""
    title = f"{data['message']} · UN/EDIFACT {data['version']}"
    payload = json.dumps(
        {k: data[k] for k in ("structure", "segments", "codes")},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Keep the JSON inert inside <script>: no premature </script> or comment openers.
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return (
        _TEMPLATE.replace("__TITLE__", html.escape(title))
        .replace("__MESSAGE__", html.escape(data["message"]))
        .replace("__VERSION__", html.escape(data["version"]))
        .replace("__DATA__", payload)
    )
