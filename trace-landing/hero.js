(() => {
  const hero = document.querySelector('.hero');
  const start = () => hero.classList.add('ready');
  (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(start);
  setTimeout(start, 1500);

  // Early access: validates, then confirms inline (preview only, nothing is sent)
  const ok = v => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v);
  const input = hero.querySelector('.signup input');
  document.querySelectorAll('.signup').forEach(form => {
    const inp = form.querySelector('input'), note = form.parentElement.querySelector('.note');
    const idle = note.textContent;
    form.addEventListener('submit', e => {
      e.preventDefault();
      if (!ok(inp.value.trim())) {
        form.classList.add('bad'); note.classList.add('err'); note.textContent = 'Please enter a valid email address.';
        inp.setAttribute('aria-invalid', 'true'); inp.focus(); return;
      }
      form.hidden = true;
      note.classList.remove('err');
      note.textContent = 'Thank you. Design preview only, nothing was sent.';
    });
    inp.addEventListener('input', () => {
      if (form.classList.contains('bad')) { form.classList.remove('bad'); note.classList.remove('err'); note.textContent = idle; inp.removeAttribute('aria-invalid'); }
    });
  });
  // nav CTA scrolls to the form and focuses it
  hero.querySelector('.nav-cta').addEventListener('click', e => {
    e.preventDefault();
    document.getElementById('early-access').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
    setTimeout(() => input.focus({ preventScroll: true }), 350);
  });

  // Effortlessness: statements appear one after another when the section is reached
  const eff = document.querySelector('.effortless');
  if (eff) {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) eff.classList.add('in');
    else new IntersectionObserver((en, ob) => { if (en.some(x => x.isIntersecting)) { eff.classList.add('in'); ob.disconnect(); } }, { threshold: .45 }).observe(eff);
  }

  // Launch film: click to play with sound, native controls once started
  const film = document.getElementById('film-video');
  if (film) {
    const frame = film.parentElement, btn = frame.querySelector('.film-play');
    btn.addEventListener('click', () => { film.controls = true; frame.classList.add('playing'); film.play(); });
    film.addEventListener('ended', () => { film.controls = false; frame.classList.remove('playing'); film.currentTime = 0; });
  }
})();

// Use cases tabs: click or arrow keys
(() => {
  const tabs = [...document.querySelectorAll('.uc-tabs [role="tab"]')];
  if (!tabs.length) return;
  const pill = document.querySelector('.uc-pill');
  const placePill = () => {
    const on = tabs.find(x => x.getAttribute('aria-selected') === 'true');
    if (!pill || !on || !on.offsetWidth) return;
    pill.style.width = on.offsetWidth + 'px'; pill.style.transform = `translateX(${on.offsetLeft}px)`;
  };
  placePill(); addEventListener('resize', placePill); addEventListener('load', placePill);
  (document.fonts && document.fonts.ready || Promise.resolve()).then(placePill);
  const show = (t, focus) => {
    tabs.forEach(x => {
      const on = x === t;
      x.setAttribute('aria-selected', on);
      x.tabIndex = on ? 0 : -1;
      document.getElementById(x.getAttribute('aria-controls')).hidden = !on;
    });
    document.getElementById('use-cases').dataset.tab = t.id.replace('uc-tab-', '');
    placePill();
    if (focus) t.focus();
    const root = document.documentElement;
    root.classList.add('no-snap'); clearTimeout(window.__snapT); window.__snapT = setTimeout(() => root.classList.remove('no-snap'), 1000);
    if (window.__traceDraw) requestAnimationFrame(() => window.__traceDraw(true));
  };
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => show(t));
    t.addEventListener('keydown', e => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (d) { e.preventDefault(); show(tabs[(i + d + tabs.length) % tabs.length], true); }
      else if (e.key === 'Home') { e.preventDefault(); show(tabs[0], true); }
      else if (e.key === 'End') { e.preventDefault(); show(tabs[tabs.length - 1], true); }
    });
  });
})();

// Privacy composition: a small, honest demo of the controls
(() => {
  const mac = document.getElementById('mac');
  if (!mac) return;
  const state = mac.querySelector('.state span'), pause = mac.querySelector('.c-pause'), del = mac.querySelector('.c-del'), empty = mac.querySelector('.mac-empty');
  mac.querySelectorAll('[role=switch]').forEach(b => b.addEventListener('click', () => b.setAttribute('aria-checked', b.getAttribute('aria-checked') !== 'true')));
  pause.addEventListener('click', () => {
    const p = mac.classList.toggle('paused');
    state.textContent = p ? 'Paused' : 'Remembering'; pause.textContent = p ? 'Resume' : 'Pause';
  });
  del.addEventListener('click', () => {
    const c = mac.classList.toggle('cleared');
    empty.hidden = !c; del.textContent = c ? 'Restore' : 'Delete';
  });
})();

// Trace reconnection: objects arrive, then lines draw, then the pill and a single pulse per line
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const EASE = 'cubic-bezier(.2,.7,.1,1)';
  const pos = (el, vis) => {
    let x = 0, y = 0, n = el;
    while (n && n !== vis) { x += n.offsetLeft; y += n.offsetTop; n = n.offsetParent; }
    return { x, y, w: el.offsetWidth, h: el.offsetHeight };
  };
  const draw = (vis, replay) => {
    const svg = vis.querySelector('.cx'); if (!svg || !vis.offsetWidth) return;
    const nowBox = vis.querySelector('.blk.now .box'); if (!nowBox) return;
    const sig = vis.offsetWidth + 'x' + vis.offsetHeight + ':' + nowBox.offsetLeft;
    if (replay) vis._sig = null;
    if (vis._sig === sig) return;
    const animate = !reduce && !vis._sig && !vis._static;
    vis._sig = sig;
    (vis._timers || []).forEach(clearTimeout); vis._timers = [];
    const k = 100 / vis.offsetWidth, f2 = v => v.toFixed(2);
    svg.innerHTML = '';
    vis.querySelectorAll('.tr-pill').forEach(p => p.remove());
    const anchors = {};
    vis.querySelectorAll('[data-anchor]').forEach(a => { anchors[a.dataset.anchor] = a; });
    const maxD = Math.max(...[...vis.querySelectorAll('.blk')].map(b => parseFloat(b.style.getPropertyValue('--d')) || 0));
    const tObj = .1 + maxD * .12 + .6;               // when every object has arrived
    const N = pos(nowBox, vis), r = 1.2, ends = {};
    const links = [...vis.querySelectorAll('[data-link]')].filter(f => anchors[f.dataset.link]);
    let tLast = tObj;
    links.forEach((f, i) => {
      const A = pos(anchors[f.dataset.link], vis), F = pos(f.querySelector('.box') || f, vis);
      const sx = (F.x + F.w) * k, sy = (F.y + F.h / 2) * k;
      const bx = N.x * k - 2.2, ex = N.x * k, ey = (A.y + A.h / 2) * k;
      const dy = ey - sy, sgn = dy >= 0 ? 1 : -1, rr = Math.min(r, Math.abs(dy) / 2);
      const d = Math.abs(dy) < .3
        ? `M${f2(sx)} ${f2(sy)} L${f2(ex)} ${f2(ey)}`
        : `M${f2(sx)} ${f2(sy)} L${f2(bx - rr)} ${f2(sy)} Q${f2(bx)} ${f2(sy)} ${f2(bx)} ${f2(sy + sgn * rr)} L${f2(bx)} ${f2(ey - sgn * rr)} Q${f2(bx)} ${f2(ey)} ${f2(bx + rr)} ${f2(ey)} L${f2(ex)} ${f2(ey)}`;
      const p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); svg.appendChild(p);
      const dv = parseFloat(f.style.getPropertyValue('--d')) || 0;
      const t0 = .1 + dv * .12 + .4, t1 = t0 + .7;
      tLast = Math.max(tLast, t1);
      if (animate) p.animate([{ clipPath: 'inset(-10% 100% -10% 0)' }, { clipPath: 'inset(-10% -10% -10% 0)' }], { duration: 700, delay: t0 * 1000, easing: EASE, fill: 'both' });
      ends[f.dataset.link] = { ey, ex, t: Math.max(ends[f.dataset.link]?.t || 0, t1) };
      if (animate) {
        const c = document.createElementNS(NS, 'circle'); c.setAttribute('class', 'pulse'); c.setAttribute('r', '.55'); c.style.opacity = '0';
        const m = document.createElementNS(NS, 'animateMotion');
        m.setAttribute('dur', '1.4s'); m.setAttribute('begin', 'indefinite'); m.setAttribute('fill', 'freeze'); m.setAttribute('path', d);
        c.appendChild(m); svg.appendChild(c);
        vis._timers.push(setTimeout(() => { c.style.opacity = '1'; try { m.beginElement(); } catch (e) {} }, (t1 + .05) * 1000));
        vis._timers.push(setTimeout(() => { c.style.opacity = '0'; }, (t1 + .05 + 1.4) * 1000));
      }
    });
    Object.values(ends).forEach(o => {
      const e = document.createElementNS(NS, 'circle'); e.setAttribute('class', 'end'); e.setAttribute('r', '.6'); e.setAttribute('cx', f2(o.ex - .5)); e.setAttribute('cy', f2(o.ey)); svg.appendChild(e);
      if (animate) e.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 400, delay: o.t * 1000, fill: 'both' });
    });
    if (links.length) {
      const pill = document.createElement('span');
      pill.className = 'tr-pill'; pill.textContent = `Trace reconnected ${links.length} ${links.length === 1 ? 'moment' : 'moments'}`;
      pill.style.left = ((N.x + N.w) * k) + 'cqw'; pill.style.top = (N.y * k - 3.3) + 'cqw'; vis.appendChild(pill);
      if (animate) pill.animate([{ opacity: 0, transform: 'translate(-100%,.5cqw)' }, { opacity: 1, transform: 'translate(-100%,0)' }], { duration: 600, delay: (tLast + .05) * 1000, easing: EASE, fill: 'both' });
    }
    vis._static = false;
  };
  const run = replay => document.querySelectorAll('.uc-panel:not([hidden]) .vis').forEach(v => draw(v, replay === true));
  window.__traceDraw = run;
  new ResizeObserver(() => run()).observe(document.querySelector('.uc-stage'));
  (document.fonts && document.fonts.ready || Promise.resolve()).then(() => run());
  addEventListener('load', () => run());
})();
