// Hover preview for the page-strip dots. One shared card, images fetched lazily
// and cached by the browser; nothing is prefetched, so a books page with 30 rows
// and a thousand dots costs zero extra requests until the user actually hovers.
(function () {
  const card = document.getElementById('hovercard');
  if (!card) return;
  const img = card.querySelector('img');
  const meta = card.querySelector('.hc-meta');
  let hideTimer = null;

  function place(e) {
    const pad = 14, w = card.offsetWidth, h = card.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > innerWidth - 8) x = e.clientX - w - pad;
    if (y + h > innerHeight - 8) y = e.clientY - h - pad;
    card.style.left = Math.max(8, x) + 'px';
    card.style.top = Math.max(8, y) + 'px';
  }

  document.addEventListener('mouseover', function (e) {
    const pt = e.target.closest('.pt');
    if (!pt) return;
    clearTimeout(hideTimer);
    const oid = pt.dataset.oid;
    if (img.dataset.oid !== oid) {
      img.dataset.oid = oid;
      img.removeAttribute('src');          // avoid showing the previous ornament
      img.src = '/img/crop/' + oid + '.jpg?w=270';
    }
    meta.textContent = 'page ' + pt.dataset.page + ' · ' + pt.dataset.kind +
      (pt.dataset.n > 1 ? ' · ' + pt.dataset.n + ' on this page' : '');
    card.hidden = false;
    place(e);
  });

  document.addEventListener('mousemove', function (e) {
    if (!card.hidden && e.target.closest('.pt')) place(e);
  });

  document.addEventListener('mouseout', function (e) {
    if (!e.target.closest('.pt')) return;
    hideTimer = setTimeout(() => { card.hidden = true; }, 80);
  });

  // keyboard accessibility: dots are links, so focus should preview too
  document.addEventListener('focusin', function (e) {
    const pt = e.target.closest('.pt');
    if (!pt) return;
    const r = pt.getBoundingClientRect();
    place({ clientX: r.left, clientY: r.bottom });
    img.src = '/img/crop/' + pt.dataset.oid + '.jpg?w=270';
    card.hidden = false;
  });
  document.addEventListener('focusout', () => { card.hidden = true; });
})();
