"""Self-contained HTML export of a GEFEG guideline with the developer's questions.

The page works offline. The BA replies in the page and saves it; the data (guideline tree and
question threads) lives in a JSON block that is rewritten on save, so the saved file can be
imported back (see ``app.core.gefeg_questions.extract_export``)."""
import html
import json

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.45 "Segoe UI", Arial, sans-serif; color: #222; background: #f4f5f9; }
#app { display: flex; flex-direction: column; height: 100vh; }
.top { background: #fff; border-bottom: 1px solid #dcdfe8; padding: 8px 16px; }
.titlerow { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 16px; }
.top h1 { margin: 0; font-size: 16px; }
.meta { display: flex; flex-wrap: wrap; gap: 2px 16px; color: #555; font-size: 12.5px; }
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin-top: 6px; }
.bar .grow { flex: 1; }
button { font: inherit; cursor: pointer; border: 1px solid #c7cbe0; background: #fff; border-radius: 6px; padding: 4px 10px; }
button:hover { background: #eef0ff; }
button.primary { background: #4f5bd5; color: #fff; border-color: #4f5bd5; }
button.primary:hover { background: #3f4ac0; }
button.on { background: #e3e6ff; border-color: #4f5bd5; color: #2b348f; font-weight: 600; }
input[type=text] { font: inherit; padding: 5px 9px; border: 1px solid #c7cbe0; border-radius: 6px; min-width: 180px; max-width: 320px; }
.dirty { color: #c2410c; font-weight: 600; }
.saved { color: #15803d; }
.progress { font-size: 13px; }
.banner { background: #fff7e0; border: 1px solid #f0d58a; border-radius: 6px; padding: 6px 10px; margin: 8px 16px 0; font-size: 13px; }
.main { flex: 1; display: grid; grid-template-columns: min(var(--tree-w, 42%), 70%) 8px minmax(0, 1fr); min-height: 0; padding: 10px 16px 16px; }
.tree { overflow-y: auto; overflow-x: hidden; background: #fff; border: 1px solid #dcdfe8; border-radius: 8px; padding: 4px 0; min-width: 0; }
.divider { cursor: col-resize; position: relative; touch-action: none; }
.divider::after { content: ""; position: absolute; left: 3px; top: 0; bottom: 0; width: 2px; border-radius: 2px; }
.divider:hover::after { background: #4f5bd5; }
.detail { display: flex; flex-direction: column; overflow: hidden; min-width: 0; background: #fff; border: 1px solid #dcdfe8; border-radius: 8px; }
.dhead { flex: none; padding: 10px 18px 8px; border-bottom: 1px solid #eee; }
.dtabs { flex: none; display: flex; gap: 4px; padding: 0 12px; border-bottom: 2px solid #e5e7f0; }
.dtabs button { border: none; border-bottom: 3px solid transparent; margin-bottom: -2px; border-radius: 0; background: none; padding: 6px 12px; color: #555; }
.dtabs button.active { color: #3f4ac0; border-bottom-color: #4f5bd5; font-weight: 600; }
.dbody { flex: 1; min-height: 0; overflow: auto; padding: 10px 18px 16px; }
.hint { padding: 8px 12px; color: #666; font-size: 12.5px; border-bottom: 1px solid #eee; }
.row { display: flex; align-items: center; gap: 6px; padding: 3px 8px; cursor: pointer; white-space: nowrap; min-width: 0; }
.row > * { flex: none; }
.row > .name { flex: 0 1 auto; min-width: 2.5em; overflow: hidden; text-overflow: ellipsis; }
.row > .qb:last-child { margin-left: auto; }
@media (max-width: 800px) {
  .main { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr) 0 minmax(0, 1.4fr); gap: 8px; }
  .divider { display: none; }
}
.row:hover { background: #f1f3ff; }
.row.sel { background: #dfe3ff; }
.row.ctx .name { color: #8a8fa3; }
.tw { width: 14px; text-align: center; color: #666; flex: none; }
.name { font-weight: 600; }
.name.attribute { color: #7c3aed; }
.occ { color: #888; font-size: 12px; }
.qb { font-size: 12px; border-radius: 10px; padding: 0 7px; }
.qb.open { background: #ffedd5; color: #c2410c; }
.qb.answered { background: #dcfce7; color: #15803d; }
.qb.closed { background: #e5e7eb; color: #555; }
.crumbs { font-size: 12.5px; margin-bottom: 8px; color: #666; word-break: break-all; }
.crumbs a { color: #3f4ac0; cursor: pointer; text-decoration: none; }
.crumbs a:hover { text-decoration: underline; }
.crumbs .sep { margin: 0 4px; color: #aaa; }
h2 { margin: 0 0 4px; font-size: 17px; }
h3 { margin: 18px 0 6px; font-size: 14px; border-bottom: 1px solid #e5e7f0; padding-bottom: 3px; }
dl { margin: 8px 0; }
dt { font-size: 12px; color: #777; margin-top: 6px; }
dd { margin: 0; white-space: pre-wrap; word-break: break-word; }
.card { border: 1px solid #e1e4f0; border-radius: 8px; padding: 6px 12px 8px; margin: 8px 0; background: #fafbff; }
.card.hl { border-color: #f59e0b; background: #fffbeb; }
.card .no { font-weight: 700; color: #4f5bd5; margin-right: 8px; }
.flag { color: #c0392b; font-weight: 600; font-size: 12.5px; }
.thread { border: 1px solid #d8dcf0; border-radius: 8px; margin: 10px 0; background: #fff; }
.thread-head { display: flex; gap: 8px; align-items: center; padding: 6px 12px; background: #f1f3ff; border-radius: 8px 8px 0 0; font-size: 13px; }
.msg { padding: 8px 12px; border-top: 1px solid #eceef8; }
.msg .who { font-size: 12px; font-weight: 700; margin-bottom: 2px; }
.msg.dev .who { color: #4f5bd5; }
.msg.ba .who { color: #15803d; }
.msg .when { font-weight: 400; color: #888; margin-left: 8px; }
.msg .text { white-space: pre-wrap; word-break: break-word; }
a.ref { display: inline-block; padding: 0 7px; border-radius: 10px; background: #ede9fe; color: #5b21b6; font-size: 12.5px; cursor: pointer; text-decoration: none; }
a.ref:hover { background: #ddd6fe; }
.imgs { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.imgs .im { position: relative; }
.imgs img { max-width: 260px; max-height: 180px; border: 1px solid #ccd; border-radius: 4px; cursor: zoom-in; display: block; }
.imgs .x { position: absolute; top: 2px; right: 2px; padding: 0 6px; line-height: 18px; background: rgba(255,255,255,.9); }
.reply { padding: 8px 12px; border-top: 1px solid #eceef8; }
.reply textarea { width: 100%; font: inherit; padding: 6px 8px; border: 1px solid #c7cbe0; border-radius: 6px; resize: vertical; }
.reply .acts { display: flex; gap: 8px; align-items: center; margin-top: 6px; color: #777; font-size: 12px; }
.empty { color: #888; padding: 16px; }
.lightbox { position: fixed; inset: 0; background: rgba(0,0,0,.75); display: flex; align-items: center; justify-content: center; z-index: 10; cursor: zoom-out; }
.lightbox img { max-width: 94vw; max-height: 94vh; background: #fff; }
</style>
</head>
<body>
<div id="app"></div>
<script id="gfg-data" type="application/json">__DATA__</script>
<script>
(function () {
  'use strict';
  var data = JSON.parse(document.getElementById('gfg-data').textContent);
  var nodes = data.nodes;
  var questions = data.questions;
  var bySeq = {}, qBySeq = {}, hasChild = {};
  nodes.forEach(function (n) {
    bySeq[n.seq] = n;
    if (n.parent_seq != null) hasChild[n.parent_seq] = true;
  });
  var DRAFT_KEY = 'gfg-draft:' + data.export_id;
  var mode = 'questions';
  var selected = null;
  var collapsed = {};
  var query = '';
  var onlyOpen = false;
  var dirty = false;
  var fileHandle = null;
  var added = {};      // ids of BA messages written in this session (may be deleted again)
  var drafts = {};     // reply boxes: question id -> { text, images }
  var banner = '';
  var MAX_SIDE = 1600;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  // Escapes message text and turns field references ("@[Name](#seq)") into links to that field.
  function richText(s) {
    return esc(s).replace(/@\[([^\]]+)\]\(#(\d+)\)/g, function (all, name, seq) {
      var n = bySeq[seq];
      if (!n) return all;
      var parent = n.parent_seq != null ? bySeq[n.parent_seq] : null;
      return '<a class="ref" data-act="ref" data-seq="' + seq + '" title="' + esc(n.path) + '">' +
        (parent ? esc(parent.name) + ' &rsaquo; ' : '') + (n.kind === 'attribute' ? '@' : '') + esc(n.name) + '</a>';
    });
  }
  function uid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, '');
    var s = '';
    for (var i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16);
    return s;
  }
  function fmtTime(s) {
    var d = new Date(s);
    return isNaN(d) ? '' : d.toLocaleString();
  }
  function safeImg(src) {
    return /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+\/=]+$/.test(src) ? src : '';
  }

  function indexQuestions() {
    qBySeq = {};
    questions.forEach(function (q) { (qBySeq[q.node_seq] = qBySeq[q.node_seq] || []).push(q); });
  }
  indexQuestions();

  // ---- browser draft (protects against closing the tab before saving)
  function persistDraft() {
    var out = {};
    questions.forEach(function (q) {
      var mine = q.messages.filter(function (m) { return m.by === 'ba' && added[m.id]; });
      if (mine.length) out[q.id] = mine;
    });
    try {
      if (Object.keys(out).length) localStorage.setItem(DRAFT_KEY, JSON.stringify(out));
      else localStorage.removeItem(DRAFT_KEY);
    } catch (e) { /* storage unavailable */ }
  }
  function restoreDraft() {
    var saved;
    try { saved = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch (e) { saved = null; }
    if (!saved) return;
    var n = 0;
    questions.forEach(function (q) {
      (saved[q.id] || []).forEach(function (m) {
        if (!q.messages.some(function (x) { return x.id === m.id; })) {
          q.messages.push(m); added[m.id] = true; q.status = 'answered'; n++;
        }
      });
      q.messages.sort(function (a, b) { return a.at < b.at ? -1 : a.at > b.at ? 1 : 0; });
    });
    if (n) {
      dirty = true;
      banner = 'Restored ' + n + ' reply(s) that were not saved to the file yet. Click "Save" to keep them in the file.';
    }
  }
  restoreDraft();

  // ---- helpers over the tree
  function ancestors(seq) {
    var out = [];
    for (var n = bySeq[seq]; n && n.parent_seq != null; n = bySeq[n.parent_seq]) out.unshift(n.parent_seq);
    return out;
  }
  function openCount(seq) {
    return (qBySeq[seq] || []).filter(function (q) { return q.status === 'open'; }).length;
  }
  function occText(n) {
    if (n.kind === 'attribute') return n.use || '';
    if (n.min_occurs == null) return '';
    return n.min_occurs + '..' + (n.max_occurs === 'unbounded' ? '*' : n.max_occurs);
  }
  function matches(n, q) {
    var hay = [n.path, n.type, n.notes];
    n.mappings.forEach(function (m) { hay.push(m.condition, m.instruction, m.additional_info, m.flag); });
    (qBySeq[n.seq] || []).forEach(function (x) { x.messages.forEach(function (m) { hay.push(m.text); }); });
    return hay.some(function (t) { return t && String(t).toLowerCase().indexOf(q) >= 0; });
  }

  function visibleSeqs() {
    var q = query.trim().toLowerCase();
    var keep = null;
    if (mode === 'questions' || q || onlyOpen) {
      keep = {};
      nodes.forEach(function (n) {
        var list = qBySeq[n.seq] || [];
        if (mode === 'questions' || onlyOpen) {
          if (!list.length) return;
          if (onlyOpen && !openCount(n.seq)) return;
        }
        if (q && !matches(n, q)) return;
        keep[n.seq] = true;
        ancestors(n.seq).forEach(function (a) { keep[a] = true; });
      });
    }
    var hidden = {}, out = [];
    nodes.forEach(function (n) {
      if (keep && !keep[n.seq]) return;
      if (n.parent_seq != null && (hidden[n.parent_seq] || (!q && collapsed[n.parent_seq]))) {
        hidden[n.seq] = true;
        return;
      }
      out.push(n.seq);
    });
    return out;
  }

  // ---- rendering
  var app = document.getElementById('app');

  function skeleton() {
    var g = data.guideline;
    app.innerHTML =
      '<div class="top"><div class="titlerow"><h1>' + esc(g.title || g.filename) + '</h1>' +
      '<div class="meta"><span>' + esc([g.model, g.version, g.variant, g.issue_date].filter(Boolean).join(' · ')) + '</span>' +
      '<span>Exported: ' + esc(fmtTime(data.exported_at)) + '</span></div></div>' +
      '<div class="bar">' +
      '<button class="primary" data-act="save">Save</button><span id="state"></span>' +
      '<span class="progress" id="progress"></span>' +
      '<button data-act="next">Next unanswered &rarr;</button>' +
      '<span class="grow"></span>' +
      '<input type="text" id="search" placeholder="Search fields, instructions, questions">' +
      '<button data-act="mode-questions" id="m-q">Fields with questions</button>' +
      '<button data-act="mode-all" id="m-a">Full structure</button>' +
      '<label><input type="checkbox" id="only-open"> Unanswered only</label>' +
      '<button data-act="expand">Expand</button><button data-act="collapse">Collapse</button>' +
      '</div></div>' +
      '<div id="banner"></div>' +
      '<div class="main"><div class="tree" id="tree"></div><div class="divider" id="divider" title="Drag to resize"></div>' +
      '<div class="detail" id="detail"></div></div>' +
      '<input type="file" id="file" accept="image/*" multiple style="display:none">';
    var saved = Number(localStorage.getItem('gfg-tree-width'));
    if (saved) document.querySelector('.main').style.setProperty('--tree-w', saved + 'px');
  }

  function renderHeader() {
    var total = questions.length;
    var open = questions.filter(function (q) { return q.status === 'open'; }).length;
    document.getElementById('progress').textContent =
      (total - open) + ' of ' + total + ' questions answered';
    document.getElementById('state').innerHTML = dirty
      ? '<span class="dirty">&#9679; Unsaved changes</span>'
      : '<span class="saved">&#10003; Saved</span>';
    document.getElementById('m-q').className = mode === 'questions' ? 'on' : '';
    document.getElementById('m-a').className = mode === 'all' ? 'on' : '';
    document.getElementById('banner').innerHTML = banner ? '<div class="banner">' + esc(banner) + '</div>' : '';
  }

  function renderTree() {
    var seqs = visibleSeqs();
    var el = document.getElementById('tree');
    var html = '';
    if (mode === 'questions') {
      html += '<div class="hint">Only fields with questions are listed. Greyed fields are their parent groups, ' +
        'shown so you can see where each field sits in the structure. Use "Full structure" to see everything.</div>';
    }
    seqs.forEach(function (seq) {
      var n = bySeq[seq];
      var list = qBySeq[seq] || [];
      var open = openCount(seq);
      var badge = '';
      if (list.length) {
        badge = open
          ? '<span class="qb open">&#10067; ' + open + '</span>'
          : '<span class="qb answered">&#10003; ' + list.length + '</span>';
      }
      var expandable = !!hasChild[seq];
      html += '<div class="row' + (seq === selected ? ' sel' : '') + (list.length ? '' : ' ctx') +
        '" data-act="sel" data-seq="' + seq + '" style="padding-left:' + (8 + n.depth * 16) + 'px">' +
        '<span class="tw"' + (expandable ? ' data-act="toggle" data-seq="' + seq + '"' : '') + '>' +
        (expandable ? (collapsed[seq] ? '&#9656;' : '&#9662;') : '&middot;') + '</span>' +
        '<span class="name ' + n.kind + '">' + (n.kind === 'attribute' ? '@' : '') + esc(n.name) + '</span>' +
        '<span class="occ">' + esc(occText(n)) + '</span>' + badge + '</div>';
    });
    if (!seqs.length) html += '<div class="empty">Nothing to show.</div>';
    el.innerHTML = html;
    var cur = el.querySelector('.row.sel');
    if (cur && cur.scrollIntoView && el.dataset.follow) {
      cur.scrollIntoView({ block: 'nearest' });
      el.dataset.follow = '';
    }
  }

  function fieldsHtml(pairs) {
    return '<dl>' + pairs.filter(function (p) { return p[1]; }).map(function (p) {
      return '<dt>' + esc(p[0]) + '</dt><dd>' + esc(p[1]) + '</dd>';
    }).join('') + '</dl>';
  }

  function imagesHtml(list, removable, qid) {
    if (!list || !list.length) return '';
    return '<div class="imgs">' + list.map(function (src, i) {
      src = safeImg(src);
      if (!src) return '';
      return '<span class="im"><img src="' + src + '" data-act="zoom" alt="">' +
        (removable ? '<button class="x" data-act="rm-img" data-qid="' + qid + '" data-i="' + i + '">&times;</button>' : '') +
        '</span>';
    }).join('') + '</div>';
  }

  function threadHtml(q) {
    var d = drafts[q.id] || { text: '', images: [] };
    var label = q.status === 'open' ? 'Waiting for reply' : q.status === 'answered' ? 'Answered' : 'Closed';
    var html = '<div class="thread"><div class="thread-head"><span class="qb ' + q.status + '">' + label + '</span>' +
      (q.mapping_nr ? '<span>About Map Instruction #' + esc(q.mapping_nr) + '</span>' : '<span>About this field</span>') +
      '</div>';
    q.messages.forEach(function (m) {
      html += '<div class="msg ' + m.by + '"><div class="who">' + (m.by === 'ba' ? 'BA' : 'Developer') +
        '<span class="when">' + esc(fmtTime(m.at)) + '</span>' +
        (added[m.id] ? ' <button data-act="del-msg" data-qid="' + q.id + '" data-mid="' + m.id + '">Delete</button>' : '') +
        '</div><div class="text">' + richText(m.text) + '</div>' + imagesHtml(m.images, false) + '</div>';
    });
    html += '<div class="reply"><textarea rows="3" data-qid="' + q.id + '" placeholder="Write your reply... (you can paste screenshots here)">' +
      esc(d.text) + '</textarea>' + imagesHtml(d.images, true, q.id) +
      '<div class="acts"><button class="primary" data-act="reply" data-qid="' + q.id + '">Reply</button>' +
      '<button data-act="attach" data-qid="' + q.id + '">Attach image</button>' +
      '<span>Remember to click "Save" at the top when you are done.</span></div></div></div>';
    return html;
  }

  var dtab = 'questions';
  var lastDetailKey = '';

  function renderDetail() {
    var el = document.getElementById('detail');
    var n = selected != null ? bySeq[selected] : null;
    if (!n) { el.innerHTML = '<div class="empty">Select a field on the left.</div>'; return; }
    var oldBody = el.querySelector('.dbody');
    var keepScroll = oldBody && lastDetailKey === n.seq + ':' + dtab ? oldBody.scrollTop : 0;
    lastDetailKey = n.seq + ':' + dtab;
    var crumbs = ancestors(n.seq).map(function (a) {
      return '<a data-act="goto" data-seq="' + a + '">' + esc(bySeq[a].name) + '</a>';
    }).concat(['<strong>' + esc(n.name) + '</strong>']).join('<span class="sep">&rsaquo;</span>');
    var list = qBySeq[n.seq] || [];
    var hl = {};
    list.forEach(function (q) { if (q.status !== 'closed' && q.mapping_nr) hl[q.mapping_nr] = true; });
    var waiting = openCount(n.seq);
    var html = '<div class="dhead"><div class="crumbs">' + crumbs + '</div>' +
      '<h2>' + (n.kind === 'attribute' ? '@' : '') + esc(n.name) + '</h2>' +
      '<div class="crumbs">' + esc(n.path) + ' &nbsp; <button data-act="show-full">Show in full structure</button></div></div>' +
      '<div class="dtabs">' +
      '<button data-act="dtab" data-tab="questions" class="' + (dtab === 'questions' ? 'active' : '') + '">Questions (' + list.length + ')' +
      (waiting ? ' &#10067;' + waiting : '') + '</button>' +
      '<button data-act="dtab" data-tab="maps" class="' + (dtab === 'maps' ? 'active' : '') + '">Map Instructions (' + n.mappings.length + ')</button>' +
      '<button data-act="dtab" data-tab="details" class="' + (dtab === 'details' ? 'active' : '') + '">Details</button>' +
      '</div><div class="dbody">';
    if (dtab === 'details') {
      html += fieldsHtml([
        ['Kind', n.kind === 'attribute' ? 'Attribute' : 'Element'],
        ['Occurrence', n.kind === 'element' ? occText(n) : ''],
        ['Use', n.use], ['Type', n.type],
        ['PDF page', n.page ? String(n.page) : ''], ['General Notes', n.notes]
      ]);
    } else if (dtab === 'maps') {
      if (!n.mappings.length) html += '<div class="empty">No Map Instructions</div>';
      n.mappings.forEach(function (m, i) {
        var nr = m.nr || String(i + 1);
        html += '<div class="card' + (hl[nr] ? ' hl' : '') + '"><div><span class="no">#' + esc(nr) + '</span>' +
          (m.flag ? '<span class="flag">' + esc(m.flag) + '</span>' : '') +
          (hl[nr] ? ' <span class="qb open">&#10067; has a question</span>' : '') + '</div>' +
          fieldsHtml([['Condition', m.condition], ['Instruction', m.instruction], ['Additional Info', m.additional_info]]) + '</div>';
      });
    } else {
      list.forEach(function (q) { html += threadHtml(q); });
      if (!list.length) html += '<div class="empty">No questions for this field.</div>';
    }
    el.innerHTML = html + '</div>';
    el.querySelector('.dbody').scrollTop = keepScroll;
  }

  function renderAll() { renderHeader(); renderTree(); renderDetail(); }

  function select(seq, follow) {
    selected = seq;
    document.getElementById('tree').dataset.follow = follow ? '1' : '';
    renderTree();
    renderDetail();
  }
  function reveal(seq) {
    ancestors(seq).forEach(function (a) { delete collapsed[a]; });
  }

  // ---- changes
  function markChanged() {
    dirty = true;
    banner = '';
    persistDraft();
    indexQuestions();
    renderAll();
  }
  function questionById(id) {
    return questions.filter(function (q) { return q.id === id; })[0];
  }
  function sendReply(qid) {
    var q = questionById(qid);
    var d = drafts[qid] || { text: '', images: [] };
    var text = d.text.trim();
    if (!text && !d.images.length) return;
    var msg = { id: uid(), by: 'ba', text: text, images: d.images.slice(), at: new Date().toISOString() };
    q.messages.push(msg);
    q.status = 'answered';
    added[msg.id] = true;
    delete drafts[qid];
    markChanged();
  }
  function deleteMessage(qid, mid) {
    var q = questionById(qid);
    q.messages = q.messages.filter(function (m) { return m.id !== mid; });
    delete added[mid];
    var last = q.messages[q.messages.length - 1];
    q.status = last && last.by === 'ba' ? 'answered' : 'open';
    markChanged();
  }

  // ---- images
  function toDataUrl(file) {
    return new Promise(function (resolve, reject) {
      var fr = new FileReader();
      fr.onerror = reject;
      fr.onload = function () {
        var img = new Image();
        img.onerror = reject;
        img.onload = function () {
          var s = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
          var c = document.createElement('canvas');
          c.width = Math.max(1, Math.round(img.width * s));
          c.height = Math.max(1, Math.round(img.height * s));
          var ctx = c.getContext('2d');
          ctx.fillStyle = '#fff';
          ctx.fillRect(0, 0, c.width, c.height);
          ctx.drawImage(img, 0, 0, c.width, c.height);
          var out = c.toDataURL('image/png');
          if (out.length > 700000) out = c.toDataURL('image/jpeg', 0.85);
          resolve(out);
        };
        img.src = fr.result;
      };
      fr.readAsDataURL(file);
    });
  }
  function addImages(qid, files) {
    var imgs = Array.prototype.filter.call(files, function (f) { return /^image\//.test(f.type); });
    if (!imgs.length) return;
    var d = drafts[qid] = drafts[qid] || { text: '', images: [] };
    var room = 6 - d.images.length;
    Promise.all(imgs.slice(0, room).map(toDataUrl)).then(function (urls) {
      d.images = d.images.concat(urls);
      renderDetail();
    }).catch(function () { alert('This image could not be read.'); });
  }

  // ---- save
  function safeJson(obj) {
    return JSON.stringify(obj).replace(/[<>&\u2028\u2029]/g, function (c) {
      return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  }
  function buildFile() {
    data.saved_at = new Date().toISOString();
    var clone = document.documentElement.cloneNode(true);
    clone.querySelector('#app').innerHTML = '';
    clone.querySelector('#gfg-data').textContent = safeJson(data);
    return '<!DOCTYPE html>\n' + clone.outerHTML;
  }
  function download(content, name) {
    var url = URL.createObjectURL(new Blob([content], { type: 'text/html' }));
    var a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  function savedOk() {
    dirty = false;
    try { localStorage.removeItem(DRAFT_KEY); } catch (e) { /* ignore */ }
    renderHeader();
  }
  async function save() {
    var content = buildFile();
    var name = (data.file_name || 'questions.html').replace(/(_reply)?\.html$/i, '_reply.html');
    if (window.showSaveFilePicker) {
      try {
        if (!fileHandle) {
          fileHandle = await window.showSaveFilePicker({
            suggestedName: name,
            types: [{ description: 'HTML file', accept: { 'text/html': ['.html'] } }]
          });
        }
        var w = await fileHandle.createWritable();
        await w.write(content);
        await w.close();
        savedOk();
        return;
      } catch (e) {
        if (e && e.name === 'AbortError') return;
        fileHandle = null;
      }
    }
    download(content, name);
    savedOk();
  }

  // ---- events
  app.addEventListener('click', function (e) {
    var t = e.target.closest('[data-act]');
    if (!t) return;
    var act = t.dataset.act;
    var seq = t.dataset.seq != null ? Number(t.dataset.seq) : null;
    if (act === 'sel') select(seq, false);
    else if (act === 'toggle') {
      e.stopPropagation();
      if (collapsed[seq]) delete collapsed[seq]; else collapsed[seq] = true;
      renderTree();
    }
    else if (act === 'goto') select(seq, true);
    else if (act === 'mode-questions') { mode = 'questions'; renderAll(); }
    else if (act === 'mode-all') { mode = 'all'; if (selected != null) reveal(selected); document.getElementById('tree').dataset.follow = '1'; renderAll(); }
    else if (act === 'show-full') {
      mode = 'all'; query = ''; document.getElementById('search').value = '';
      onlyOpen = false; document.getElementById('only-open').checked = false;
      reveal(selected); document.getElementById('tree').dataset.follow = '1'; renderAll();
    }
    else if (act === 'ref') {
      mode = 'all'; query = ''; document.getElementById('search').value = '';
      onlyOpen = false; document.getElementById('only-open').checked = false;
      reveal(seq); selected = seq; document.getElementById('tree').dataset.follow = '1'; renderAll();
    }
    else if (act === 'expand') { collapsed = {}; renderTree(); }
    else if (act === 'collapse') { nodes.forEach(function (n) { if (hasChild[n.seq]) collapsed[n.seq] = true; }); renderTree(); }
    else if (act === 'next') {
      var start = selected == null ? -1 : nodes.findIndex(function (n) { return n.seq === selected; });
      var order = nodes.slice(start + 1).concat(nodes.slice(0, start + 1));
      var hit = order.filter(function (n) { return openCount(n.seq) > 0; })[0];
      if (!hit) { alert('All questions have been answered.'); return; }
      reveal(hit.seq); selected = hit.seq;
      document.getElementById('tree').dataset.follow = '1';
      renderTree(); renderDetail();
    }
    else if (act === 'reply') sendReply(t.dataset.qid);
    else if (act === 'del-msg') deleteMessage(t.dataset.qid, t.dataset.mid);
    else if (act === 'attach') { var f = document.getElementById('file'); f.dataset.qid = t.dataset.qid; f.click(); }
    else if (act === 'rm-img') { drafts[t.dataset.qid].images.splice(Number(t.dataset.i), 1); renderDetail(); }
    else if (act === 'zoom') {
      var box = document.createElement('div');
      box.className = 'lightbox';
      box.innerHTML = '<img src="' + t.src + '" alt="">';
      box.onclick = function () { box.remove(); };
      app.appendChild(box);
    }
    else if (act === 'dtab') { dtab = t.dataset.tab; renderDetail(); }
    else if (act === 'save') save();
  });
  app.addEventListener('pointerdown', function (e) {
    if (!e.target.closest || !e.target.closest('#divider')) return;
    e.preventDefault();
    var main = document.querySelector('.main');
    var tree = document.getElementById('tree');
    var startX = e.clientX, startW = tree.getBoundingClientRect().width;
    function move(ev) {
      var w = Math.min(window.innerWidth - 360, Math.max(240, startW + ev.clientX - startX));
      main.style.setProperty('--tree-w', w + 'px');
      try { localStorage.setItem('gfg-tree-width', String(Math.round(w))); } catch (err) { /* ignore */ }
    }
    function stop() {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
  });
  app.addEventListener('input', function (e) {
    var t = e.target;
    if (t.id === 'search') { query = t.value; renderTree(); }
    else if (t.tagName === 'TEXTAREA' && t.dataset.qid) {
      var d = drafts[t.dataset.qid] = drafts[t.dataset.qid] || { text: '', images: [] };
      d.text = t.value;
    }
  });
  app.addEventListener('change', function (e) {
    var t = e.target;
    if (t.id === 'only-open') { onlyOpen = t.checked; renderTree(); }
    else if (t.id === 'file') { addImages(t.dataset.qid, t.files); t.value = ''; }
  });
  app.addEventListener('paste', function (e) {
    var t = e.target;
    if (t.tagName !== 'TEXTAREA' || !t.dataset.qid || !e.clipboardData) return;
    var files = Array.prototype.filter.call(e.clipboardData.files || [], function (f) { return /^image\//.test(f.type); });
    if (files.length) { e.preventDefault(); addImages(t.dataset.qid, files); }
  });
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); }
  });
  window.addEventListener('beforeunload', function (e) {
    if (dirty) { e.preventDefault(); e.returnValue = ''; }
  });

  skeleton();
  var first = nodes.filter(function (n) { return (qBySeq[n.seq] || []).length; })[0] || nodes[0];
  if (first) selected = first.seq;
  renderAll();
})();
</script>
</body>
</html>
"""


def render_export_html(payload: dict) -> str:
    title = payload["guideline"].get("title") or payload["guideline"].get("filename") or "GEFEG questions"
    data = json.dumps(payload, ensure_ascii=False)
    # keep the JSON block inert inside <script>: no tag/entity can start inside a string
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                    ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        data = data.replace(ch, esc)
    return _TEMPLATE.replace("__TITLE__", html.escape(f"{title} - questions")).replace("__DATA__", data)
