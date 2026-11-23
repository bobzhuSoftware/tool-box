"""Render a parsed XSD model (see app.core.xsd_parser) as one self-contained HTML file.

The model is embedded as JSON and the tree is built client-side and lazily, so
shared types that are reused many times stay cheap. No external scripts/fonts.
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
:root { --bd:#d9dee7; --muted:#667085; --req:#b42318; --accent:#2563eb; --bg:#f6f8fb; }
* { box-sizing: border-box; }
body { margin:0; font:14px/1.5 "Segoe UI","Microsoft YaHei",system-ui,sans-serif; color:#1d2939; background:var(--bg); }
header { position:sticky; top:0; z-index:5; background:#fff; border-bottom:1px solid var(--bd); padding:10px 20px; }
h1 { margin:0 0 6px; font-size:18px; }
h1 small { font-weight:400; color:var(--muted); margin-left:8px; }
.bar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.bar input[type=search] { width:280px; padding:6px 10px; border:1px solid var(--bd); border-radius:6px; font:inherit; }
.bar select { max-width:340px; padding:5px 8px; border:1px solid var(--bd); border-radius:6px; font:inherit; background:#fff; }
.bar button { padding:5px 12px; border:1px solid var(--bd); border-radius:6px; background:#fff; cursor:pointer; font:inherit; }
.bar button:hover { border-color:var(--accent); color:var(--accent); }
.bar label { color:#475467; cursor:pointer; user-select:none; }
#count { color:var(--muted); }
.legend { margin:6px 0 0; color:var(--muted); font-size:12px; }
main { padding:14px 20px 40px; }
.row { display:flex; gap:10px; align-items:baseline; padding:2px 8px; border-radius:4px; min-height:26px; }
.row:hover { background:#eef2f7; }
.row.hit { background:#fff6c2; }
.tw { width:14px; flex:none; color:var(--muted); cursor:pointer; user-select:none; text-align:center; }
.row.leaf .tw { cursor:default; }
.nm { font:600 13px Consolas,monospace; white-space:nowrap; }
.row.attr .nm { color:#9333ea; font-weight:500; }
.row.grp .nm { color:#d97706; }
.row.req .nm { color:var(--req); }
.card { font:12px Consolas,monospace; color:var(--muted); white-space:nowrap; }
.card.req { color:var(--req); }
.ty { font:12px Consolas,monospace; color:var(--accent); white-space:nowrap; }
.ds { color:var(--muted); font-size:12px; min-width:0; }
.tag { font-size:11px; padding:0 5px; border-radius:4px; background:#e4e7ec; color:#475467; white-space:nowrap; }
.tag.warn { background:#fde8b0; color:#7a5200; }
.kids { margin-left:20px; border-left:1px dashed var(--bd); padding-left:4px; }
.kids.hidden, .hidden { display:none; }
.meta { margin:0 0 2px 32px; font-size:12px; color:#475467; }
.meta details > summary { cursor:pointer; color:var(--accent); }
.meta table { border-collapse:collapse; margin:4px 0; font-size:12px; }
.meta td { border:1px solid var(--bd); padding:1px 8px; vertical-align:top; background:#fff; }
mark { background:#fde047; padding:0 1px; }
.empty { padding:30px; text-align:center; color:var(--muted); }
footer { padding:10px 20px 30px; color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>XSD 结构 <small id="rootname"></small></h1>
  <div class="bar">
    <select id="root" title="根节点"></select>
    <input id="q" type="search" placeholder="搜索 名称 / 类型 / 说明 / 枚举值（至少 2 个字符）…" autocomplete="off">
    <button id="expandNext">展开下一层</button>
    <button id="collapseAll">折叠全部</button>
    <label><input id="onlyReq" type="checkbox"> 仅显示必填</label>
    <label><input id="showAttr" type="checkbox" checked> 显示属性</label>
    <span id="count"></span>
  </div>
  <div class="legend">
    基数：<code>1</code> 必填一次，<code>0..1</code> 可选，<code>0..*</code> 可重复，<code>1..*</code> 必填可重复 &nbsp;|&nbsp;
    <span style="color:#9333ea">@属性</span> &nbsp;|&nbsp; <span style="color:#d97706">choice / sequence</span> 为内容模型分组 &nbsp;|&nbsp; ↻ 递归引用不再展开
  </div>
</header>
<main id="main"></main>
<footer id="foot"></footer>
<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  var D = JSON.parse(document.getElementById('data').textContent);
  var main = document.getElementById('main');
  var qEl = document.getElementById('q');
  var rootSel = document.getElementById('root');
  var onlyReq = document.getElementById('onlyReq');
  var showAttr = document.getElementById('showAttr');
  var all = [];       // every built row: {el, hasKids, expanded, parent}
  var memo = {};

  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function localName(k) {
    if (!k) return '';
    if (k.charAt(0) === '#') return '(内联类型)';
    var i = k.indexOf('|');
    return i >= 0 ? k.slice(i + 1) : k;
  }
  function card(min, max) {
    if (max === -1) return min + '..*';
    return min === max ? String(min) : min + '..' + max;
  }

  // ---- model access -------------------------------------------------------
  function elOf(n) {
    if (!n.ref) return n;
    var g = D.elements[n.ref];
    if (!g) return { k: 'el', name: localName(n.ref), min: n.min, max: n.max, unresolved: 1 };
    var o = {};
    for (var p in g) o[p] = g[p];
    o.min = n.min; o.max = n.max;
    return o;
  }
  function content(k, seen) {
    seen = seen || [];
    var t = D.types[k];
    var out = { attrs: [], items: [], text: null };
    if (!t || t.kind !== 'complex' || seen.indexOf(k) >= 0) return out;
    seen = seen.concat(k);
    var b = t.base && D.types[t.base] ? content(t.base, seen) : (t.text && D.types[t.text] && D.types[t.text].kind === 'complex' ? content(t.text, seen) : null);
    if (b) { out.attrs = b.attrs.slice(); out.items = b.items.slice(); out.text = b.text; }
    if (t.text && !(D.types[t.text] && D.types[t.text].kind === 'complex')) out.text = t.text;
    if (t.attrs) out.attrs = out.attrs.concat(t.attrs);
    if (t.items) out.items.push(t.items);
    return out;
  }
  function simpleInfo(k) {
    var facets = {}, seen = {}, builtin = '', extra = '';
    var cur = k;
    while (cur && !seen[cur]) {
      seen[cur] = 1;
      var t = D.types[cur];
      if (!t) { builtin = cur.replace(/^xs:/, ''); break; }
      var f = t.facets || {};
      for (var p in f) if (!(p in facets)) facets[p] = f[p];
      if (t.list) { extra = '列表：' + localName(t.list).replace(/^xs:/, ''); break; }
      if (t.union) { extra = '联合：' + t.union.map(function (u) { return localName(u).replace(/^xs:/, ''); }).join(' | '); break; }
      cur = t.kind === 'complex' ? t.text : t.base;
    }
    return { builtin: builtin, facets: facets, extra: extra };
  }
  function typeLabel(k) {
    if (!k) return '';
    if (k.indexOf('xs:') === 0) return k;
    var t = D.types[k];
    if (k.charAt(0) === '#') {
      if (t && t.kind === 'simple') return '(内联简单类型)';
      return '(内联类型)';
    }
    return localName(k) + (t ? '' : ' ⚠');
  }

  // ---- row descriptors ------------------------------------------------------
  function particleRows(p, out) {
    if (!p) return out;
    if (p.k === 'el') { out.push({ kind: 'el', node: elOf(p) }); return out; }
    if (p.k === 'any') { out.push({ kind: 'any', node: p }); return out; }
    var inner = p, label = p.k, min = p.min, max = p.max;
    if (p.k === 'group') {
      inner = D.groups[p.ref];
      if (!inner) { out.push({ kind: 'any', node: { min: p.min, max: p.max, ns: '未解析的 group ' + localName(p.ref) } }); return out; }
      label = inner.k;
    }
    if (label !== 'choice' && min === 1 && max === 1) {
      (inner.items || []).forEach(function (c) { particleRows(c, out); });
      return out;
    }
    out.push({ kind: 'group', label: label, min: min, max: max, items: inner.items || [] });
    return out;
  }
  function rowsOfType(k) {
    var c = content(k), out = [];
    c.items.forEach(function (p) { particleRows(p, out); });
    if (showAttr.checked) c.attrs.forEach(function (a) { out.push({ kind: 'attr', node: a }); });
    return out;
  }
  function rowsOfGroup(r) {
    var out = [];
    r.items.forEach(function (p) { particleRows(p, out); });
    return out;
  }
  function childRows(r, ancestors) {
    if (r.kind === 'group') return rowsOfGroup(r);
    if (r.kind === 'el' && r.node.type && ancestors.indexOf(r.node.type) < 0) return rowsOfType(r.node.type);
    return [];
  }
  function isRecursive(r, ancestors) {
    return r.kind === 'el' && r.node.type && ancestors.indexOf(r.node.type) >= 0 && D.types[r.node.type] && D.types[r.node.type].kind === 'complex';
  }
  function required(r) {
    if (r.kind === 'attr') return r.node.u === 'required';
    if (r.kind === 'any') return false;
    var mn = r.kind === 'el' ? r.node.min : r.min;
    return mn >= 1;
  }

  // ---- text / search ---------------------------------------------------------
  function selfText(r) {
    var parts = [];
    if (r.kind === 'el' || r.kind === 'attr') {
      var n = r.node;
      parts.push(n.name || n.n || '', n.doc || '', typeLabel(n.type || n.t));
      var info = simpleInfo(n.type || n.t);
      if (info.facets.enum) info.facets.enum.forEach(function (e) { parts.push(e[0], e[1]); });
      var t = D.types[n.type || n.t];
      if (t && t.doc) parts.push(t.doc);
    } else if (r.kind === 'group') parts.push(r.label);
    return parts.join('\n').toLowerCase();
  }
  function selfMatch(r, q) { return !!q && selfText(r).indexOf(q) >= 0; }
  function typeHas(k, q, stack) {
    if (k in memo) return memo[k];
    if (stack.indexOf(k) >= 0) return false;
    var res = rowsOfType(k).some(function (r) { return rowHas(r, q, stack.concat(k)); });
    memo[k] = res;
    return res;
  }
  function rowHas(r, q, stack) {
    if (selfMatch(r, q)) return true;
    if (r.kind === 'group') return rowsOfGroup(r).some(function (c) { return rowHas(c, q, stack); });
    if (r.kind === 'el' && r.node.type) {
      var t = D.types[r.node.type];
      if (t && t.kind === 'complex') return typeHas(r.node.type, q, stack);
    }
    return false;
  }

  // ---- rendering -------------------------------------------------------------
  function metaFor(r) {
    if (!r.node) return null;
    var n = r.node, k = n.type || n.t;
    if (!k) return null;
    var info = simpleInfo(k), t = D.types[k], bits = [];
    var isSimple = k.indexOf('xs:') === 0 || (t && (t.kind === 'simple' || t.text));
    if (!isSimple) return null;
    if (info.builtin && k.indexOf('xs:') !== 0) bits.push('基础类型 ' + info.builtin);
    if (info.extra) bits.push(info.extra);
    var f = info.facets;
    ['length', 'minLength', 'maxLength', 'minInclusive', 'maxInclusive', 'minExclusive', 'maxExclusive', 'totalDigits', 'fractionDigits', 'pattern'].forEach(function (p) {
      if (f[p] != null) bits.push(p + ' = ' + f[p]);
    });
    if (n.default != null) bits.push('default = ' + n.default);
    if (n.fixed != null) bits.push('fixed = ' + n.fixed);
    if (!bits.length && !f.enum) return null;
    var box = h('div', 'meta');
    if (bits.length) box.appendChild(h('div', null, bits.join(' · ')));
    if (f.enum) {
      var det = h('details');
      det.appendChild(h('summary', null, '枚举值（' + f.enum.length + '）'));
      var tb = h('table');
      f.enum.forEach(function (e) {
        var tr = h('tr');
        tr.appendChild(h('td', null, e[0])); tr.appendChild(h('td', null, e[1]));
        tb.appendChild(tr);
      });
      det.appendChild(tb);
      box.appendChild(det);
    }
    return box;
  }

  function addRows(parentEl, rows, ancestors, parentRec, q, path) {
    var shown = 0;
    rows.forEach(function (r) {
      var visible = true;
      if (onlyReq.checked && !required(r)) visible = false;
      if (visible && q && !rowHas(r, q, ancestors.slice())) visible = false;
      if (!visible) return;
      shown += addRow(parentEl, r, ancestors, parentRec, q, path);
    });
    return shown;
  }

  function addRow(parentEl, r, ancestors, parentRec, q, path) {
    var n = r.node || {}, row = h('div', 'row ' + (r.kind === 'attr' ? 'attr' : r.kind === 'group' ? 'grp' : ''));
    var name, p = path;
    if (r.kind === 'group') name = r.label;
    else if (r.kind === 'any') name = '(any)';
    else if (r.kind === 'attr') name = '@' + n.n;
    else name = n.name;
    if (r.kind === 'el') p = path + '/' + name; else if (r.kind === 'attr') p = path + '/@' + n.n;
    if (required(r)) row.classList.add('req');
    var hit = selfMatch(r, q);
    if (hit) row.classList.add('hit');

    var kids = childRows(r, ancestors);
    var rec = isRecursive(r, ancestors);
    var tw = h('span', 'tw', kids.length ? '▸' : '');
    row.appendChild(tw);
    row.appendChild(h('span', 'nm', name));
    if (r.kind === 'attr') row.appendChild(h('span', 'card' + (n.u === 'required' ? ' req' : ''), n.u === 'required' ? '必填' : n.u === 'prohibited' ? '禁用' : '可选'));
    else if (r.kind === 'group') row.appendChild(h('span', 'card' + (r.min >= 1 ? ' req' : ''), card(r.min, r.max)));
    else if (r.kind === 'any') row.appendChild(h('span', 'card', card(n.min, n.max)));
    else row.appendChild(h('span', 'card' + (n.min >= 1 ? ' req' : ''), card(n.min, n.max)));
    var tk = n.type || n.t;
    if (tk) row.appendChild(h('span', 'ty', typeLabel(tk)));
    if (r.kind === 'any') row.appendChild(h('span', 'ds', 'namespace: ' + n.ns));
    if (r.kind === 'group' && r.label === 'choice') row.appendChild(h('span', 'ds', '以下选其一'));
    if (n.nil) row.appendChild(h('span', 'tag', 'nillable'));
    if (n.abstract) row.appendChild(h('span', 'tag', 'abstract'));
    if (n.unresolved) row.appendChild(h('span', 'tag warn', '未解析'));
    if (rec) row.appendChild(h('span', 'tag warn', '↻ 递归引用'));
    var t = tk && D.types[tk];
    var doc = n.doc || (t && t.doc) || '';
    if (doc) row.appendChild(h('span', 'ds', doc));
    row.title = p;
    parentEl.appendChild(row);
    var meta = metaFor(r);
    if (meta) parentEl.appendChild(meta);

    var rec_ = { el: row, hasKids: kids.length > 0, expanded: false, parent: parentRec };
    all.push(rec_);
    if (!kids.length) { row.classList.add('leaf'); return 1; }

    var box = h('div', 'kids hidden');
    parentEl.appendChild(box);
    var built = false, anc = r.kind === 'el' && n.type ? ancestors.concat(n.type) : ancestors;
    function build() {
      if (built) return;
      built = true;
      addRows(box, kids, anc, rec_, q, p);
    }
    function set(open) {
      if (open) build();
      rec_.expanded = open;
      box.classList.toggle('hidden', !open);
      tw.textContent = open ? '▾' : '▸';
    }
    rec_.set = set;
    tw.addEventListener('click', function () { set(!rec_.expanded); });
    if (q && kids.some(function (c) { return rowHas(c, q, anc.slice()); })) set(true);
    return 1;
  }

  function rootRows(key) {
    if (D.elements[key]) return [{ kind: 'el', node: elOf({ k: 'el', ref: key, min: 1, max: 1 }) }];
    var t = D.types[key];
    var r = { kind: 'el', node: { k: 'el', name: localName(key), type: key, min: 1, max: 1, doc: t ? t.doc : '' } };
    return [r];
  }

  function render() {
    var q = qEl.value.trim().toLowerCase();
    if (q.length < 2) q = '';
    memo = {};
    all = [];
    main.textContent = '';
    var key = rootSel.value;
    var rows = rootRows(key);
    var shown = addRows(main, rows, [], null, q, '');
    if (!shown) main.appendChild(h('div', 'empty', q ? '没有匹配的内容' : '没有可显示的内容'));
    if (!q && all.length && all[0].set) all[0].set(true);
    document.getElementById('rootname').textContent = localName(key);
    document.getElementById('count').textContent = '';
  }

  D.roots.forEach(function (r) {
    var o = h('option', null, (r.kind === 'element' ? '元素 ' : '类型 ') + r.name);
    o.value = r.key;
    rootSel.appendChild(o);
  });
  rootSel.value = D.default;
  document.getElementById('foot').textContent = '来源文件：' + D.files.join('、') + '。由 tool-box 离线生成，无外部依赖。';

  rootSel.addEventListener('change', render);
  qEl.addEventListener('input', render);
  onlyReq.addEventListener('change', render);
  showAttr.addEventListener('change', render);
  document.getElementById('expandNext').addEventListener('click', function () {
    all.slice().forEach(function (r) {
      if (r.hasKids && !r.expanded && (!r.parent || r.parent.expanded)) r.set(true);
    });
  });
  document.getElementById('collapseAll').addEventListener('click', function () {
    all.forEach(function (r) { if (r.hasKids && r.set) r.set(false); });
    if (all.length && all[0].set) all[0].set(true);
  });
  render();
})();
</script>
</body>
</html>
"""


def render_html(model: dict, title: str = "XSD 结构") -> str:
    """Build the standalone HTML page for a model returned by ``parse_xsd``."""
    payload = json.dumps(
        {k: model[k] for k in ("types", "elements", "groups", "roots", "default", "files")},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Keep the JSON inert inside <script>: no premature </script> or comment openers.
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return _TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", payload)
