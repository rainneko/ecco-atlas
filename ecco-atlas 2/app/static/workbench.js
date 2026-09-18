// Annotation workbench: move images between sections by drag-and-drop, by each
// card's menu, or in bulk; nothing is sent until "Save changes".
(function () {
  const root = document.getElementById('wb');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const secs = [...root.querySelectorAll('.wb-sec')];
  const pendingEl = $('wb-pending'), saveBtn = $('wb-save'), moveBtn = $('wb-move');
  const bulk = $('wb-bulk'), selinfo = $('wb-selinfo'), msg = $('wb-msg');
  const cards = () => [...root.querySelectorAll('.wb-card')];
  const keyOf = c => c.closest('.wb-sec').dataset.key;
  let saving = false, last = null, dragged = [];

  const title = s => s.classList.contains('new')
    ? 'New: ' + (s.querySelector('[data-name]').value.trim() || '(no name)')
    : s.dataset.title;
  const fill = (sel, cur) => {
    sel.innerHTML = '';
    secs.forEach(s => {
      const o = document.createElement('option');
      o.value = s.dataset.key; o.textContent = title(s);
      if (s.dataset.key === cur) o.selected = true;
      sel.appendChild(o);
    });
  };
  const refreshMenus = () => {
    cards().forEach(c => { const s = c.querySelector('select'); fill(s, keyOf(c)); });
    const b = bulk.value; fill(bulk, b);
  };
  const update = () => {
    const n = cards().filter(c => keyOf(c) !== c.dataset.orig).length;
    pendingEl.textContent = n;
    $('wb-pword').textContent = n === 1 ? 'change' : 'changes';
    saveBtn.disabled = n === 0 || saving;
    secs.forEach(s => {
      const g = s.querySelector('.wb-grid');
      const k = g.querySelectorAll('.wb-card').length;
      s.querySelector('[data-count]').textContent = k + ' image' + (k === 1 ? '' : 's');
      const e = g.querySelector('.wb-empty');
      if (e) e.hidden = k > 0;
    });
    const nsel = root.querySelectorAll('.wb-card.sel').length;
    selinfo.textContent = nsel ? nsel + ' selected' : '';
    moveBtn.disabled = nsel === 0;
  };
  const place = (card, key) => {
    const s = secs.find(x => x.dataset.key === key);
    if (!s) return;
    s.querySelector('.wb-grid').appendChild(card);
    card.classList.toggle('moved', key !== card.dataset.orig);
    card.querySelector('select').value = key;
  };
  const show = (text, ok) => {
    msg.textContent = text; msg.className = 'flash ' + (ok ? 'ok' : 'err'); msg.hidden = false;
  };

  refreshMenus();
  root.querySelectorAll('[data-name]').forEach(i => i.addEventListener('input', refreshMenus));
  root.addEventListener('change', e => {
    if (e.target.matches('.wb-card select')) { place(e.target.closest('.wb-card'), e.target.value); update(); }
  });
  root.addEventListener('click', e => {
    const c = e.target.closest('.wb-card');
    if (!c || e.target.closest('a, select')) return;
    if (e.shiftKey && last) {
      const all = cards(), [i, j] = [all.indexOf(last), all.indexOf(c)].sort((a, b) => a - b);
      for (let k = i; k <= j; k++) all[k].classList.add('sel');
    } else c.classList.toggle('sel');
    last = c; update();
  });
  root.addEventListener('dragstart', e => {
    const c = e.target.closest('.wb-card');
    if (!c) return;
    dragged = c.classList.contains('sel') ? [...root.querySelectorAll('.wb-card.sel')] : [c];
    dragged.forEach(d => d.classList.add('dragging'));
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', c.dataset.oid);
  });
  root.addEventListener('dragend', () => {
    dragged.forEach(d => d.classList.remove('dragging'));
    secs.forEach(s => s.classList.remove('over'));
  });
  secs.forEach(s => {
    s.addEventListener('dragover', e => { e.preventDefault(); s.classList.add('over'); });
    s.addEventListener('dragleave', e => { if (!s.contains(e.relatedTarget)) s.classList.remove('over'); });
    s.addEventListener('drop', e => {
      e.preventDefault(); s.classList.remove('over');
      dragged.forEach(c => { place(c, s.dataset.key); c.classList.remove('sel', 'dragging'); });
      dragged = []; update();
    });
  });
  moveBtn.addEventListener('click', () => {
    root.querySelectorAll('.wb-card.sel').forEach(c => { place(c, bulk.value); c.classList.remove('sel'); });
    update();
  });
  $('wb-clear').addEventListener('click', () => {
    root.querySelectorAll('.wb-card.sel').forEach(c => c.classList.remove('sel')); update();
  });
  saveBtn.addEventListener('click', async () => {
    const moves = {}, names = {};
    cards().forEach(c => { const k = keyOf(c); if (k !== c.dataset.orig) moves[c.dataset.oid] = k; });
    Object.values(moves).filter(k => /^n\d$/.test(k)).forEach(k => {
      names[k] = secs.find(s => s.dataset.key === k).querySelector('[data-name]').value;
    });
    saving = true; saveBtn.disabled = true; saveBtn.textContent = 'Saving…';
    try {
      const r = await fetch('/admin/workbench/save', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ src: root.dataset.src, level: root.dataset.level,
                               value: root.dataset.value, moves, names }) });
      const d = await r.json();
      if (d.ok) { show('Saved ' + d.saved + ' changes. Reloading…', true); location.reload(); return; }
      show((d.errors || ['Save failed']).join(' '), false);
    } catch (err) { show('Could not save: ' + err, false); }
    saving = false; saveBtn.textContent = 'Save changes'; update();
  });
  addEventListener('beforeunload', e => {
    if (!saving && +pendingEl.textContent > 0) { e.preventDefault(); e.returnValue = ''; }
  });
  update();
})();
