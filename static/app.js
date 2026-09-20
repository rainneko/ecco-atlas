// Page-strip hover preview: one shared card; images are fetched only on hover,
// so a results page with a thousand dots costs nothing until someone looks.
(function () {
  const card = document.getElementById('hovercard');
  if (card) {
    const img = card.querySelector('img');
    const meta = card.querySelector('.hc-meta');
    let hideTimer = null;
    const place = (x0, y0) => {
      const pad = 14, w = card.offsetWidth, h = card.offsetHeight;
      let x = x0 + pad, y = y0 + pad;
      if (x + w > innerWidth - 8) x = x0 - w - pad;
      if (y + h > innerHeight - 8) y = y0 - h - pad;
      card.style.left = Math.max(8, x) + 'px';
      card.style.top = Math.max(8, y) + 'px';
    };
    const show = (pt, x, y) => {
      clearTimeout(hideTimer);
      if (img.dataset.oid !== pt.dataset.oid) {
        img.dataset.oid = pt.dataset.oid;
        img.removeAttribute('src');
        img.src = '/img/crop/' + pt.dataset.oid + '.jpg?w=300';
      }
      const n = +pt.dataset.n;
      meta.textContent = (pt.dataset.label ? pt.dataset.label + ', ' : '') + 'page ' + pt.dataset.page +
        (n > 1 ? ' (' + n + ' ' + pt.dataset.kind + ' on this page)' : '');
      card.hidden = false;
      place(x, y);
    };
    document.addEventListener('mouseover', e => {
      const pt = e.target.closest('.pt');
      if (pt) show(pt, e.clientX, e.clientY);
    });
    document.addEventListener('mousemove', e => {
      if (!card.hidden && e.target.closest('.pt')) place(e.clientX, e.clientY);
    });
    document.addEventListener('mouseout', e => {
      if (e.target.closest('.pt')) hideTimer = setTimeout(() => { card.hidden = true; }, 80);
    });
    document.addEventListener('focusin', e => {
      const pt = e.target.closest('.pt');
      if (pt) { const r = pt.getBoundingClientRect(); show(pt, r.left, r.bottom); }
    });
    document.addEventListener('focusout', e => { if (e.target.closest('.pt')) card.hidden = true; });
  }

  // "Cite" buttons copy the BibTeX of a publication (front page, /about)
  document.addEventListener('click', e => {
    const b = e.target.closest('button[data-cite]');
    if (!b) return;
    const ta = document.getElementById('bib-' + b.dataset.cite);
    const msg = document.querySelector('[data-cite-msg="' + b.dataset.cite + '"]');
    const done = ok => { if (msg) { msg.textContent = ok ? 'BibTeX copied' : 'Select and copy:'; if (!ok) { ta.hidden = false; ta.select(); } setTimeout(() => { msg.textContent = ''; }, 2500); } };
    if (navigator.clipboard && ta) navigator.clipboard.writeText(ta.value).then(() => done(true), () => done(false));
    else done(false);
  });

  // Click menu for places and houses on class pages (DESIGN §12.7.4).
  // The link itself is the primary action, so it works without JavaScript.
  let menu = null;
  const closeMenu = () => { if (menu) { menu.remove(); menu = null; } };
  document.addEventListener('click', e => {
    const a = e.target.closest('a.menulink');
    if (!a) { if (menu && !e.target.closest('.clickmenu')) closeMenu(); return; }
    if (e.metaKey || e.ctrlKey || e.shiftKey) return;           // open-in-new-tab stays a plain link
    e.preventDefault(); closeMenu();
    const esc = s => String(s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    menu = document.createElement('div');
    menu.className = 'clickmenu'; menu.setAttribute('role', 'dialog'); menu.setAttribute('aria-label', a.dataset.title);
    menu.innerHTML = '<div class="cm-title">' + esc(a.dataset.title) + '</div>' +
      '<a class="btn cm-primary" href="' + esc(a.getAttribute('href')) + '">' + esc(a.dataset.primary) + '</a>' +
      '<a class="cm-secondary" href="' + esc(a.dataset.secondaryHref) + '">' + esc(a.dataset.secondary) + '</a>' +
      '<a class="cm-more" href="' + esc(a.dataset.moreHref) + '">' + esc(a.dataset.more) + '</a>';
    document.body.appendChild(menu);
    const r = a.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
    let x = (e.clientX || r.left) + 8, y = (e.clientY || r.bottom) + 8;
    if (x + w > innerWidth - 8) x = innerWidth - w - 8;
    if (y + h > innerHeight - 8) y = Math.max(8, (e.clientY || r.top) - h - 8);
    menu.style.left = x + 'px'; menu.style.top = y + 'px';
    menu.querySelector('.cm-primary').focus();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
  addEventListener('scroll', closeMenu, { passive: true });

  // About page: highlight the section the reader is in (DESIGN §13.2): the
  // last heading that has scrolled past the top, so arriving at a term from a
  // `?` marks the section that contains it, not the next heading on screen.
  const toc = document.querySelector('.toc');
  if (toc) {
    const det = toc.querySelector('details');
    if (det && matchMedia('(max-width: 980px)').matches) det.open = false;   // collapsed on phones
    toc.addEventListener('click', e => { if (e.target.closest('a') && det && matchMedia('(max-width: 980px)').matches) det.open = false; });
    const links = [...toc.querySelectorAll('a[href^="#"]')];
    const targets = links.map(l => document.getElementById(l.getAttribute('href').slice(1)));
    let ticking = false;
    const mark = () => {
      ticking = false;
      let cur = -1;
      targets.forEach((t, i) => { if (t && t.getBoundingClientRect().top <= 110) cur = i; });
      // at the end of the page the last sections can never reach the top:
      // prefer the section named in the address, else the last one on screen
      if (innerHeight + scrollY >= document.documentElement.scrollHeight - 4) {
        const h = location.hash.slice(1), hi = targets.findIndex(t => t && t.id === h);
        if (hi >= 0 && targets[hi].getBoundingClientRect().top < innerHeight) cur = hi;
        else targets.forEach((t, i) => { if (t && t.getBoundingClientRect().top < innerHeight * 0.8) cur = i; });
      }
      links.forEach(l => l.classList.remove('on'));
      if (cur < 0) return;
      links[cur].classList.add('on');
      const sub = links[cur].closest('ul ul');
      if (sub) sub.previousElementSibling.classList.add('on');      // its parent section too
    };
    addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(mark); } }, { passive: true });
    addEventListener('hashchange', () => setTimeout(mark, 50));
    mark(); setTimeout(mark, 300);
  }

  // Navigation menus (DESIGN §8): hover and focus open them in CSS; the ▾
  // button opens them on touch screens and from the keyboard; Escape closes.
  const groups = [...document.querySelectorAll('.navgroup')];
  const setOpen = (g, open) => {
    g.classList.toggle('open', open); g.classList.toggle('closed', !open && g.contains(document.activeElement));
    const b = g.querySelector('.navcaret'); if (b) b.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  groups.forEach(g => {
    const b = g.querySelector('.navcaret');
    b.addEventListener('click', e => { e.stopPropagation(); const o = !g.classList.contains('open'); groups.forEach(x => setOpen(x, false)); setOpen(g, o); if (o) g.querySelector('.navpanel a').focus(); });
    g.addEventListener('mouseleave', () => g.classList.remove('closed'));
    g.addEventListener('focusout', e => { if (!g.contains(e.relatedTarget)) { setOpen(g, false); g.classList.remove('closed'); } });
  });
  document.addEventListener('click', e => { if (!e.target.closest('.navgroup')) groups.forEach(g => setOpen(g, false)); });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    const g = groups.find(x => x.contains(document.activeElement) || x.classList.contains('open'));
    if (g) { setOpen(g, false); g.classList.add('closed'); g.querySelector('.navcaret').focus(); }
  });

  // Folded sections (DESIGN §12.10): open the one the address points into, and
  // let the ? inside a folded heading show its tip without toggling it.
  const openTarget = () => {
    const h = location.hash.slice(1); if (!h) return;
    const el = document.getElementById(h); if (!el) return;
    const d = el.tagName === 'DETAILS' ? el : el.closest('details.fold');
    if (d && !d.open) { d.open = true; el.scrollIntoView(); }
  };
  openTarget(); addEventListener('hashchange', openTarget);
  document.querySelectorAll('details.fold > summary .help').forEach(h => h.addEventListener('click', e => {
    if (!e.target.closest('a.more')) e.preventDefault();
  }));

  // live read-outs next to range sliders
  document.querySelectorAll('.range input[type=range]').forEach(inp => {
    const out = inp.parentElement.querySelector('output');
    const fmt = () => {
      const v = +inp.value;
      out.textContent = inp.name === 'gap' ? v + ' yrs' : (inp.max <= 1 ? Math.round(v * 100) + '%' : v);
    };
    inp.addEventListener('input', fmt);
  });
})();
