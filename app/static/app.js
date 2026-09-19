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
