// Image search (DESIGN §9.5). Four stages with fixed spans on the bar:
// upload 0–15 (real XHR progress), embedding 15–60 and matching 60–95
// (time-eased against the server's expected duration), results 100.
// The type confirmation is a pause at 60 %.
(function () {
  const root = document.getElementById('upload');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const drop = $('drop'), file = $('file'), progress = $('progress'), bar = $('bar');
  const msg = $('progress-msg'), kindbox = $('kindbox'), kinds = $('kinds');
  const KINDS = { DI: 'Decorated initial', FT: 'Factotum', HP: 'Headpiece', TP: 'Tailpiece or device' };
  const SPAN = { upload: [0, 15], model: [15, 25], embed: [15, 60], match: [60, 95], done: [95, 100] };
  let token = root.dataset.token || '';
  let eased = null, uploading = false;
  let expected = { embed: +root.dataset.expectedEmbed || 2000, match: +root.dataset.expectedMatch || 500 };

  const setBar = pct => { bar.style.width = Math.max(0, Math.min(100, pct)) + '%'; };
  const setStep = (name, text) => {
    document.querySelectorAll('#steps li').forEach(li => {
      const s = li.dataset.step;
      li.classList.toggle('on', s === name);
      li.classList.toggle('done', order(s) < order(name));
      if (s === 'model') li.hidden = root.dataset.modelLoaded === 'yes' && name !== 'model';
    });
    msg.textContent = text || '';
  };
  const order = s => ['upload', 'model', 'embed', 'kind', 'match', 'done'].indexOf(s);

  // Advance linearly to 90 % of the span over `ms`, then crawl toward the end.
  function ease(step, ms, label) {
    clearInterval(eased);
    const [a, b] = SPAN[step], t0 = Date.now();
    const tick = () => {
      const el = Date.now() - t0, span = b - a;
      let p = el < ms ? 0.9 * el / ms : 0.9 + 0.09 * (1 - Math.exp(-(el - ms) / (2 * ms)));
      setBar(a + span * p);
      const s = Math.round(el / 1000);
      if (el > 8000) msg.textContent = label + ' — still working (' + s + ' s). The first search after a restart loads the model, 10–20 s.';
      else msg.textContent = label + ' — usually about ' + (ms / 1000).toFixed(ms < 1500 ? 1 : 0) + ' s';
    };
    tick(); eased = setInterval(tick, 120);
  }
  const finish = step => { clearInterval(eased); setBar(SPAN[step][1]); };
  const fail = text => { clearInterval(eased); bar.style.background = 'var(--bad)'; msg.textContent = text; uploading = false; };

  async function status() {
    try { const r = await fetch('/api/image-search/status'); const d = await r.json();
      root.dataset.modelLoaded = d.model_loaded ? 'yes' : 'no';
      if (d.expected_ms) expected = d.expected_ms; return d; } catch (e) { return {}; }
  }

  function upload(f) {
    if (!f) return;
    progress.hidden = false; kindbox.hidden = true; bar.style.background = '';
    uploading = true; setStep('upload'); setBar(1);
    const xhr = new XMLHttpRequest(), fd = new FormData(); fd.append('file', f);
    xhr.upload.onprogress = e => { if (e.lengthComputable) { setBar(15 * e.loaded / e.total); msg.textContent = 'Uploading ' + Math.round(100 * e.loaded / e.total) + ' %'; } };
    xhr.onload = () => {
      uploading = false;
      if (xhr.status !== 200) { let t = 'Upload failed'; try { t = JSON.parse(xhr.responseText).detail || t; } catch (e) {} return fail(t); }
      token = JSON.parse(xhr.responseText).token; finish('upload'); embed();
    };
    xhr.onerror = () => fail('Upload failed — check the connection and try again.');
    xhr.open('POST', '/api/image-search'); xhr.send(fd);
  }

  async function embed() {
    const st = await status();
    if (st.error && !st.model_loaded) return fail('Image search is not ready: ' + st.error);
    if (!st.model_loaded) {
      setStep('model'); ease('model', 12000, 'Loading the model for the first time');
      while (true) { await new Promise(r => setTimeout(r, 1500)); const s = await status(); if (s.model_loaded || s.error) break; }
      finish('model');
    }
    setStep('embed'); ease('embed', expected.embed || 2000, 'Computing the embedding');
    const r = await fetch('/api/image-search/' + token + '/embed', { method: 'POST' });
    if (!r.ok) { let t = 'Could not compute the embedding'; try { t = (await r.json()).detail || t; } catch (e) {} return fail(t); }
    const d = await r.json(); finish('embed'); showKinds(d);
  }

  function showKinds(d) {
    setStep('kind', 'Step 2 of 4 — confirm the type, then continue.');
    $('qimg').src = '/img/query/' + token + '.jpg';
    $('match-eta').textContent = ((d.expected_ms || expected.match || 500) / 1000).toFixed(1);
    const best = d.kind;
    kinds.innerHTML = Object.keys(KINDS).map(k => {
      const s = d.kind_scores[k]; const w = s == null ? 0 : Math.max(0, Math.min(100, s * 100));
      return '<label class="kindrow"><input type="radio" name="kind" value="' + k + '"' + (k === best ? ' checked' : '') +
        (s == null ? ' disabled' : '') + '><span class="kl"><b>' + k + '</b> ' + KINDS[k] + '</span>' +
        '<span class="kbar"><i style="width:' + w + '%"></i></span><span class="kv">' + (s == null ? 'no data' : s.toFixed(2)) + '</span></label>';
    }).join('');
    kindbox.hidden = false; kindbox.scrollIntoView({ block: 'nearest' });
  }

  async function match() {
    const chosen = kinds.querySelector('input[name=kind]:checked');
    if (!chosen) return;
    kindbox.hidden = true; setStep('match'); ease('match', expected.match || 500, 'Matching against the atlas');
    const r = await fetch('/api/image-search/' + token + '/match', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: chosen.value }) });
    if (!r.ok) { let t = 'Matching failed'; try { t = (await r.json()).detail || t; } catch (e) {} return fail(t); }
    const d = await r.json(); finish('match'); setStep('done', 'Opening the results…'); setBar(100);
    location.href = d.url;
  }

  $('pick').addEventListener('click', () => file.click());
  drop.addEventListener('click', e => { if (e.target === drop || e.target.tagName === 'P') file.click(); });
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.click(); } });
  file.addEventListener('change', () => upload(file.files[0]));
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => upload(e.dataTransfer.files[0]));
  $('continue').addEventListener('click', match);
  addEventListener('beforeunload', e => { if (uploading) { e.preventDefault(); e.returnValue = ''; } });

  // Arriving with ?token= (from an ornament page, or "match again with this type")
  if (token) {
    const kind = new URLSearchParams(location.search).get('kind');
    progress.hidden = false; setStep('upload'); finish('upload');
    if (kind) {
      fetch('/api/image-search/' + token + '/match', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind }) })
        .then(r => r.ok ? r.json() : Promise.reject()).then(d => { location.href = d.url; }).catch(() => embed());
    } else embed();
  }
})();
