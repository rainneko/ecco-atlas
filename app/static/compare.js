// Compare page (DESIGN §12.7): class picker with suggestions, facets that
// follow the picked class, and a swap button.
(function () {
  const form = document.getElementById('cmpform');
  if (!form) return;
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  form.querySelectorAll('input[data-picker]').forEach(inp => {
    const p = inp.dataset.picker;
    const hidden = form.querySelector('input[type=hidden][name="' + p + '"]');
    const box = document.getElementById('sugg-' + p);
    let timer = null, items = [], active = -1;
    const close = () => { box.hidden = true; active = -1; };
    const render = () => {
      box.innerHTML = items.map((it, i) =>
        '<div role="option" class="opt' + (i === active ? ' on' : '') + '" data-i="' + i + '"><b>' + esc(it.label) + '</b> ' +
        (it.name ? '<span class="name">' + esc(it.name) + '</span> ' : '') +
        '<span class="muted">' + esc(it.source) + ', ' + esc(it.level) + ', ' + it.n + ' images</span></div>').join('') ||
        '<div class="opt muted">No class matches.</div>';
      box.hidden = false;
    };
    const pick = it => {
      inp.value = it.label; hidden.value = it.key; close();
      loadFacets(p, it.key);
    };
    inp.addEventListener('input', () => {
      hidden.value = '';
      clearTimeout(timer);
      const q = inp.value.trim();
      if (!q) return close();
      timer = setTimeout(async () => {
        try { items = await (await fetch('/api/classes/suggest?q=' + encodeURIComponent(q))).json(); } catch (e) { items = []; }
        active = items.length ? 0 : -1; render();
      }, 180);
    });
    inp.addEventListener('keydown', e => {
      if (box.hidden) return;
      if (e.key === 'ArrowDown') { active = Math.min(items.length - 1, active + 1); render(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { active = Math.max(0, active - 1); render(); e.preventDefault(); }
      else if (e.key === 'Enter' && active >= 0) { pick(items[active]); e.preventDefault(); }
      else if (e.key === 'Escape') close();
    });
    box.addEventListener('mousedown', e => {
      const o = e.target.closest('.opt[data-i]');
      if (o) { e.preventDefault(); pick(items[+o.dataset.i]); }
    });
    inp.addEventListener('blur', () => setTimeout(close, 150));
  });

  async function loadFacets(p, key) {
    let f;
    try { f = await (await fetch('/api/class/facets?c=' + encodeURIComponent(key))).json(); } catch (e) { return; }
    const place = form.querySelector('select[name="' + p + '_place"]');
    const agent = form.querySelector('select[name="' + p + '_agent"]');
    place.innerHTML = '<option value="">Any place</option>' +
      f.places.map(x => '<option value="' + esc(x.key) + '">' + esc(x.name) + ' (' + x.n + ')</option>').join('');
    agent.innerHTML = '<option value="">Any house</option>' +
      f.agents.map(x => '<option value="' + esc(x.name) + '">' + esc(x.display || x.name) + ' (' + x.n + ')</option>').join('');
    form.querySelectorAll('input[name="' + p + '_place_not"], input[name="' + p + '_agent_not"]').forEach(c => { c.checked = false; });
  }

  document.getElementById('swap').addEventListener('click', () => {
    const d = new FormData(form), out = new URLSearchParams();
    for (const [k, v] of d.entries()) {
      if (!v) continue;
      const m = k.match(/^([ab])(.*)$/);
      out.append(m ? (m[1] === 'a' ? 'b' : 'a') + m[2] : k, v);
    }
    location.href = '/compare?' + out.toString();
  });
})();
