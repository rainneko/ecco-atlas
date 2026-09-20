// Map through time (DESIGN §12.9). Loaded on class pages; does nothing until
// the reader opens the map, then loads d3, the coastlines and the class's books.
(function () {
  const sec = document.getElementById('map');
  if (!sec) return;
  const KEY = sec.dataset.key, V = sec.dataset.v || '1';
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let booted = false;

  function open(scroll) {
    sec.hidden = false;
    if (!booted) { booted = true; boot(); }
    if (scroll) sec.scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'start' });
  }
  document.querySelectorAll('[data-open-map]').forEach(b => b.addEventListener('click', e => {
    e.preventDefault(); history.replaceState(null, '', '#map'); open(true);
  }));
  const loadScript = src => new Promise((res, rej) => {
    const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = rej; document.head.appendChild(s);
  });
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  async function boot() {
    const stage = sec.querySelector('.geo-stage');
    stage.innerHTML = '<p class="muted geo-loading">Loading the map…</p>';
    try {
      if (!window.d3) await loadScript('/static/vendor/d3-7.9.0.min.js');
      if (!window.topojson) await loadScript('/static/vendor/topojson-client-3.1.0.min.js');
      const [land, data] = await Promise.all([
        fetch('/static/geo/land-50m-simplified.json?v=' + V).then(r => r.json()),
        fetch('/api/class/geo?c=' + encodeURIComponent(KEY)).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      ]);
      build(land, data);
    } catch (e) {
      stage.innerHTML = '<p class="flash err">The map could not be loaded (' + esc(e.message || e) + ').</p>';
    }
  }

  function build(land, D) {
    const d3 = window.d3;
    const P = D.places, B = D.books;                  // B rows: [year, placeIdx, [houseIdx], falseImprint]
    const stage = sec.querySelector('.geo-stage');
    if (!B.length) {
      stage.innerHTML = '<p class="note">None of these books has both a year and a place with coordinates.</p>';
      footnote(D); return;
    }
    const y0 = B[0][0], y1 = B[B.length - 1][0];
    const firstYear = P.map(() => Infinity), total = P.map(() => 0);
    B.forEach(r => { total[r[1]]++; if (r[0] < firstYear[r[1]]) firstYear[r[1]] = r[0]; });
    // the first place: earliest year, then most books
    const order = P.map((_, i) => i).sort((a, b) => firstYear[a] - firstYear[b] || total[b] - total[a]);
    const first = order[0];
    // arrivals: each later place, drawn from the place with most books before it appeared
    const arrivals = order.slice(1).map(p => {
      const before = P.map(() => 0);
      B.forEach(r => { if (r[0] < firstYear[p]) before[r[1]]++; });
      let from = first, best = -1;
      before.forEach((n, i) => { if (i !== p && (n > best || (n === best && firstYear[i] < firstYear[from]))) { best = n; from = i; } });
      return { to: p, from, year: firstYear[p] };
    });

    // ---------- layout
    stage.innerHTML = '';
    const W = Math.max(320, stage.clientWidth), H = Math.round(Math.min(520, Math.max(320, W * 0.56)));
    const svg = d3.select(stage).append('svg').attr('viewBox', [0, 0, W, H]).attr('class', 'geo-svg')
      .attr('role', 'img').attr('aria-label', 'Map of the places where these books were printed');
    const defs = svg.append('defs');
    defs.append('marker').attr('id', 'geo-arrow').attr('viewBox', '0 0 10 10').attr('refX', 9).attr('refY', 5)
      .attr('markerWidth', 7).attr('markerHeight', 7).attr('orient', 'auto-start-reverse')
      .append('path').attr('d', 'M0,0 L10,5 L0,10 z').attr('class', 'geo-arrowhead');

    // projection fitted to the places, at least 24° x 14°
    let lons = P.map(p => p[3]), lats = P.map(p => p[2]);
    let lo0 = Math.min(...lons), lo1 = Math.max(...lons), la0 = Math.min(...lats), la1 = Math.max(...lats);
    const padLon = Math.max(0, (24 - (lo1 - lo0)) / 2) + 3, padLat = Math.max(0, (14 - (la1 - la0)) / 2) + 2;
    lo0 -= padLon; lo1 += padLon; la0 = Math.max(-80, la0 - padLat); la1 = Math.min(80, la1 + padLat);
    const proj = d3.geoNaturalEarth1().fitExtent([[16, 16], [W - 16, H - 16]],
      { type: 'MultiPoint', coordinates: [[lo0, la0], [lo1, la0], [lo0, la1], [lo1, la1], [(lo0 + lo1) / 2, la1]] });
    const path = d3.geoPath(proj);
    const g = svg.append('g');
    g.append('path').datum(d3.geoGraticule().step([10, 10])()).attr('class', 'geo-grat').attr('d', path);
    g.append('path').datum(topojson.feature(land, land.objects.land)).attr('class', 'geo-land').attr('d', path);
    const gArcs = g.append('g'), gDots = g.append('g');
    const xy = P.map(p => proj([p[3], p[2]]));

    // arcs as quadratic curves bulging upwards
    // trimmed so the arc leaves the edge of one circle and its arrow stops at
    // the edge of the other (radii in map units, i.e. screen px / zoom)
    const arcD = (a, r0 = 0, r1 = 0) => {
      const [x0, yA] = xy[a.from], [x1, yB] = xy[a.to];
      const mx = (x0 + x1) / 2, my = (yA + yB) / 2, dx = x1 - x0, dy = yB - yA, len = Math.hypot(dx, dy) || 1;
      let nx = -dy / len, ny = dx / len; if (ny > 0) { nx = -nx; ny = -ny; }
      const k = Math.min(0.35 * len, 120), cx = mx + nx * k, cy = my + ny * k;
      const u0 = Math.hypot(cx - x0, cy - yA) || 1, u1 = Math.hypot(x1 - cx, yB - cy) || 1;
      const sx = x0 + (cx - x0) / u0 * r0, sy = yA + (cy - yA) / u0 * r0;
      const ex = x1 - (x1 - cx) / u1 * r1, ey = yB - (yB - cy) / u1 * r1;
      return `M${sx},${sy} Q${cx},${cy} ${ex},${ey}`;
    };
    const arcs = gArcs.selectAll('path').data(arrivals).join('path').attr('class', 'geo-arc')
      .attr('d', arcD).attr('marker-end', 'url(#geo-arrow)').style('display', 'none');

    // circles, wrapped in click-menu links (app.js opens the menu)
    const menus = D.menus || {};
    const dots = gDots.selectAll('a').data(P.map((p, i) => i)).join('a')
      .attr('class', 'menulink').attr('href', i => (menus[P[i][0]] || {}).primary ? menus[P[i][0]].primary[1] : '#')
      .each(function (i) {
        const m = menus[P[i][0]]; if (!m) return;
        this.setAttribute('data-title', m.title); this.setAttribute('data-primary', m.primary[0]);
        this.setAttribute('data-secondary', m.secondary[0]); this.setAttribute('data-secondary-href', m.secondary[1]);
        this.setAttribute('data-more', m.more[0]); this.setAttribute('data-more-href', m.more[1]);
      });
    const circ = dots.append('circle').attr('class', i => 'geo-dot p' + D.colours[i])
      .attr('cx', i => xy[i][0]).attr('cy', i => xy[i][1]).attr('r', 0);

    // zoom: circles and lines keep their screen size
    let zk = 1;
    const zoom = d3.zoom().scaleExtent([1, 12]).on('zoom', ev => {
      zk = ev.transform.k; g.attr('transform', ev.transform); render(false);
    });
    svg.call(zoom).on('dblclick.zoom', null);

    // ---------- controls
    const ctl = sec.querySelector('.geo-controls');
    ctl.innerHTML = `
      <button type="button" class="geo-play" aria-label="Play">▶ Play</button>
      <span class="geo-end">${y0}</span>
      <div class="geo-track"><svg class="geo-hist" preserveAspectRatio="none" aria-hidden="true"></svg>
        <input type="range" class="geo-year" min="${y0}" max="${y1}" step="1" value="${y1}" aria-label="Year"></div>
      <span class="geo-end">${y1}</span>
      <label class="geo-opt">Speed <select class="geo-speed"><option value="1">×1</option><option value="2">×2</option><option value="4">×4</option></select></label>
      <div class="geo-mode" role="group" aria-label="Which books"><button type="button" data-mode="cum" class="on">Up to this year</button><button type="button" data-mode="win">Around this year (±5)</button></div>
      <button type="button" class="quiet geo-reset">Reset view</button>`;
    const slider = ctl.querySelector('.geo-year');
    const playBtn = ctl.querySelector('.geo-play'), speedSel = ctl.querySelector('.geo-speed');
    // histogram of books per year behind the slider
    const perYear = new Map(); B.forEach(r => perYear.set(r[0], (perYear.get(r[0]) || 0) + 1));
    const hmax = Math.max(...perYear.values()), span = Math.max(1, y1 - y0 + 1);
    const hist = d3.select(ctl.querySelector('.geo-hist')).attr('viewBox', `0 0 ${span} 10`);
    hist.selectAll('rect').data([...perYear]).join('rect')
      .attr('x', d => d[0] - y0 + 0.1).attr('width', 0.8).attr('y', d => 10 - 10 * d[1] / hmax).attr('height', d => 10 * d[1] / hmax);

    let mode = 'cum', year = y1, lastYear = y1, timer = null;
    ctl.querySelectorAll('.geo-mode button').forEach(b => b.addEventListener('click', () => {
      mode = b.dataset.mode; ctl.querySelectorAll('.geo-mode button').forEach(x => x.classList.toggle('on', x === b)); render(false);
    }));
    ctl.querySelector('.geo-reset').addEventListener('click', () => svg.transition().duration(REDUCED ? 0 : 400).call(zoom.transform, d3.zoomIdentity));
    slider.addEventListener('input', () => { stop(); setYear(+slider.value); });
    playBtn.addEventListener('click', () => timer ? stop() : play());
    function play() {
      if (year >= y1) setYear(y0, true);
      playBtn.textContent = '❚❚ Pause'; playBtn.setAttribute('aria-label', 'Pause');
      const tick = () => { if (year >= y1) return stop(); setYear(year + 1); timer = setTimeout(tick, 350 / +speedSel.value); };
      timer = setTimeout(tick, 350 / +speedSel.value);
    }
    function stop() {
      clearTimeout(timer); timer = null; playBtn.textContent = '▶ Play'; playBtn.setAttribute('aria-label', 'Play');
    }
    function setYear(y, jump) { lastYear = jump ? y : year; year = y; slider.value = y; slider.setAttribute('aria-valuetext', String(y)); render(!jump); }

    // radius scale: area ∝ books; the largest circle of the class is 28 px
    const winMax = (() => {
      if (mode === 'cum') return Math.max(...total);
      let m = 1; for (let y = y0; y <= y1; y++) { const c = counts(y, 'win'); m = Math.max(m, ...c); } return m;
    });
    function counts(t, md) {
      const c = P.map(() => 0);
      B.forEach(r => { if (md === 'cum' ? r[0] <= t : Math.abs(r[0] - t) <= 5) c[r[1]]++; });
      return c;
    }
    const maxCum = Math.max(...total);
    let maxWin = null;
    const tip = sec.querySelector('.geo-tip');

    function topHouses(rows, k) {
      const c = new Map(); rows.forEach(r => r[2].forEach(h => c.set(h, (c.get(h) || 0) + 1)));
      const s = [...c].sort((a, b) => b[1] - a[1] || D.houses[a[0]][1].localeCompare(D.houses[b[0]][1]));
      return { top: s.slice(0, k), more: Math.max(0, s.length - k) };
    }
    const houseLine = h => h.top.length ? h.top.map(([i, n]) => `<a href="/agent/${encodeURIComponent(D.houses[i][0])}">${esc(D.houses[i][1])}</a> <span class="muted">(${n})</span>`).join(' · ') + (h.more ? ` <span class="muted">and ${h.more} more</span>` : '') : '<span class="muted">no imprint names</span>';

    function render(animate) {
      const inView = B.filter(r => mode === 'cum' ? r[0] <= year : Math.abs(r[0] - year) <= 5);
      const c = P.map(() => 0); inView.forEach(r => c[r[1]]++);
      if (mode === 'win' && maxWin === null) maxWin = winMax();
      const rs = d3.scaleSqrt().domain([0, mode === 'cum' ? maxCum : maxWin]).range([0, 28]);
      const rad = P.map((_, i) => (c[i] ? Math.max(3, rs(c[i])) : 0) / zk);
      circ.attr('r', i => rad[i]).attr('stroke-width', 1.2 / zk)
        .classed('pulse', i => !REDUCED && animate && firstYear[i] === year && year !== lastYear);
      // arcs up to this year; the newest drawn, older faded
      arcs.style('display', a => a.year <= year ? null : 'none')
        .attr('d', a => arcD(a, rad[a.from] + 1 / zk, rad[a.to] + 2 / zk))
        .attr('stroke-width', 1.6 / zk)
        .classed('old', a => a.year < year - 2);
      if (animate && !REDUCED) arcs.filter(a => a.year === year && lastYear < year).each(function () {
        const L = this.getTotalLength();
        d3.select(this).attr('stroke-dasharray', L).attr('stroke-dashoffset', L)
          .transition().duration(600).attr('stroke-dashoffset', 0).on('end', function () { d3.select(this).attr('stroke-dasharray', null); });
      });
      // info under the map
      const nPlaces = c.filter(Boolean).length, span = mode === 'cum' ? 'so far' : `${year - 5}–${year + 5}`;
      const newHere = arrivals.filter(a => a.year === year);
      sec.querySelector('.geo-info').innerHTML = `
        <div class="geo-bigyear">${year}</div>
        <div><p><span class="muted">First printed:</span> <b>${esc(P[first][1])}</b>, ${firstYear[first]}</p>
          <p><b>${inView.length}</b> book${inView.length === 1 ? '' : 's'} in <b>${nPlaces}</b> place${nPlaces === 1 ? '' : 's'} <span class="muted">${span}</span>
          ${newHere.length ? ' · <span class="geo-new">New in ' + year + ': ' + newHere.map(a => esc(P[a.to][1]) + ' <span class="muted">(after ' + esc(P[a.from][1]) + ')</span>').join(', ') + '</span>' : ''}</p>
          <p><span class="muted">Main houses:</span> ${houseLine(topHouses(inView, 3))}</p></div>`;
      circ.each(function (i) { this._n = c[i]; this._rows = inView.filter(r => r[1] === i); });
    }

    // tooltip
    circ.on('mouseenter', function (ev, i) {
      const h = topHouses(this._rows || [], 3);
      tip.innerHTML = `<b>${esc(P[i][1])}</b><br>${this._n || 0} book${this._n === 1 ? '' : 's'} in view · first ${firstYear[i]}<br>` +
        (h.top.length ? h.top.map(([k, n]) => esc(D.houses[k][1]) + ' (' + n + ')').join(', ') + (h.more ? ', …' : '') : '<span class="muted">no imprint names</span>') +
        '<br><span class="muted">Click to compare</span>';
      tip.hidden = false;
    }).on('mousemove', ev => {
      const r = sec.querySelector('.geo-stage').getBoundingClientRect();
      tip.style.left = (ev.clientX - r.left + 14) + 'px'; tip.style.top = (ev.clientY - r.top + 10) + 'px';
    }).on('mouseleave', () => { tip.hidden = true; }).on('click', () => { tip.hidden = true; });

    // table and footnote
    const tbl = sec.querySelector('.geo-table');
    tbl.innerHTML = '<summary>Show as table</summary><table><thead><tr><th>Place</th><th class="num">First year</th><th class="num">Books</th><th>Main houses</th></tr></thead><tbody>' +
      order.map(i => `<tr><td><i class="sw" data-i="${D.colours[i]}"></i>${esc(P[i][1])}</td><td class="num">${firstYear[i]}</td><td class="num">${total[i]}</td><td>${houseLine(topHouses(B.filter(r => r[1] === i), 3))}</td></tr>`).join('') +
      '</tbody></table>';
    footnote(D);
    render(false);
  }

  function footnote(D) {
    const off = D.n_noyear + D.n_noplace;
    sec.querySelector('.geo-foot').innerHTML =
      (off ? `${off} of ${D.n_books} books are not on the map: ${D.n_noyear} have no year, ${D.n_noplace} no place with coordinates. ` : `All ${D.n_books} books are on the map. `) +
      'An arrow means the design is next found in that city, drawn from the city with the most books until then; it does not show that a block travelled. ' +
      'Circle area is proportional to the number of books. Coastlines: Natural Earth; no modern borders are drawn.';
  }
  // after every declaration above: opening may start loading at once
  if (location.hash === '#map') open(true);
})();
