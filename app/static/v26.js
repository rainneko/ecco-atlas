// v2.6 (DESIGN §5.4, §5.6): image lightbox for every gallery of ornaments, and
// old section links (#lending, #map, #places …) that now live in class-page tabs.
(function () {
  // ---------------------------------------------------------------- tabs
  const nav = document.querySelector('.classtabs');
  if (nav && location.hash) {
    const where = { lending: 'houses', houses: 'houses', timeline: 'houses', map: 'places',
                    where: 'places', places: 'designs', designs: 'designs', related: 'related',
                    images: 'images' };
    const id = decodeURIComponent(location.hash.slice(1)), want = where[id];
    if (want && want !== nav.dataset.tab && !document.getElementById(id)) {
      const link = nav.querySelector('a[href$="tab=' + want + '"]');
      if (link) location.replace(link.href + location.hash);
    }
  }

  // ---------------------------------------------------------------- lightbox
  const SEL = '.gallery .thumb > a[href^="/ornament/"], [data-lightbox] .thumb > a[href^="/ornament/"]';
  const oidOf = a => a.dataset.oid || (a.getAttribute('href').match(/^\/ornament\/([^/?#]+)/) || [])[1];
  let box = null, items = [], idx = 0, lastFocus = null, touchX = null;

  function build() {
    box = document.createElement('div');
    box.className = 'lb'; box.hidden = true;
    box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-label', 'Image viewer');
    box.innerHTML =
      '<button type="button" class="lb-close" aria-label="Close (Esc)">×</button>' +
      '<button type="button" class="lb-prev" aria-label="Previous image (←)">‹</button>' +
      '<button type="button" class="lb-next" aria-label="Next image (→)">›</button>' +
      '<figure class="lb-fig"><div class="lb-stage"><span class="lb-wait">Loading…</span><img alt=""></div>' +
      '<figcaption class="lb-cap"><div class="lb-text"></div><div class="lb-links"></div></figcaption></figure>';
    document.body.appendChild(box);
    box.querySelector('.lb-close').addEventListener('click', close);
    box.querySelector('.lb-prev').addEventListener('click', () => go(-1));
    box.querySelector('.lb-next').addEventListener('click', () => go(1));
    box.addEventListener('click', e => { if (e.target === box) close(); });
    box.addEventListener('touchstart', e => { touchX = e.touches[0].clientX; }, { passive: true });
    box.addEventListener('touchend', e => {
      if (touchX === null) return;
      const dx = e.changedTouches[0].clientX - touchX; touchX = null;
      if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1);
    });
    const img = box.querySelector('img');
    img.addEventListener('load', () => { box.querySelector('.lb-wait').hidden = true; img.style.visibility = ''; preloadNext(); });
    img.addEventListener('error', () => { box.querySelector('.lb-wait').textContent = 'The image could not be loaded.'; });
  }

  const big = oid => '/img/crop/' + oid + '.jpg?w=1400';

  function show() {
    const a = items[idx], oid = oidOf(a);
    const img = box.querySelector('img'), wait = box.querySelector('.lb-wait');
    wait.hidden = false; wait.textContent = 'Loading…'; img.style.visibility = 'hidden';
    img.src = big(oid);
    const fig = a.closest('figure'), cap = fig && fig.querySelector('figcaption');
    img.alt = (a.querySelector('img') || {}).alt || '';
    box.querySelector('.lb-text').innerHTML = cap ? cap.innerHTML : '';
    const n = items.length;
    let links = (n > 1 ? '<span class="lb-count">' + (idx + 1) + ' / ' + n + '</span>' : '') +
      '<a href="/ornament/' + oid + '">Open the ornament page</a>' +
      '<a href="/img/page/' + oid + '" target="_blank" rel="noopener">Full page image</a>';
    const pg = document.querySelector('.pager');
    if (pg && idx === n - 1 && pg.querySelector('a[rel=next]'))
      links += '<a href="' + pg.querySelector('a[rel=next]').getAttribute('href') + '">Next page of images →</a>';
    if (pg && idx === 0 && n > 1 && pg.querySelector('a[rel=prev]'))
      links += '<a href="' + pg.querySelector('a[rel=prev]').getAttribute('href') + '">← Previous page</a>';
    box.querySelector('.lb-links').innerHTML = links;
    box.querySelector('.lb-prev').hidden = idx === 0;
    box.querySelector('.lb-next').hidden = idx === n - 1;
  }

  // one image ahead only: each uncached crop costs one download from the image server
  let preloaded = null;
  function preloadNext() {
    if (idx + 1 >= items.length) return;
    const oid = oidOf(items[idx + 1]);
    if (preloaded === oid) return;
    preloaded = oid;
    setTimeout(() => { if (box && !box.hidden && oidOf(items[Math.min(idx + 1, items.length - 1)]) === oid) new Image().src = big(oid); }, 700);
  }

  function open(list, i) {
    if (!box) build();
    items = list; idx = i; lastFocus = document.activeElement;
    box.hidden = false; document.body.classList.add('lb-open');
    show(); box.querySelector('.lb-close').focus();
  }
  function close() {
    if (!box || box.hidden) return;
    box.hidden = true; document.body.classList.remove('lb-open');
    box.querySelector('img').removeAttribute('src');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function go(d) {
    const j = idx + d;
    if (j < 0 || j >= items.length) return;
    idx = j; show();
  }

  document.addEventListener('click', e => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const single = e.target.closest('a[data-lightbox-single]');
    if (single) { e.preventDefault(); open([single], 0); return; }
    const a = e.target.closest(SEL);
    if (!a || !a.querySelector('img')) return;               // only the image opens the viewer
    const scope = a.closest('.gallery, [data-lightbox]');
    const list = [...scope.querySelectorAll('.thumb > a[href^="/ornament/"]')].filter(x => x.querySelector('img'));
    e.preventDefault();
    open(list, Math.max(0, list.indexOf(a)));
  });
  document.addEventListener('keydown', e => {
    if (!box || box.hidden) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
    else if (e.key === 'Tab') {                               // keep focus inside the viewer
      const f = [...box.querySelectorAll('button:not([hidden]), a[href]')];
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
})();
