// Ambient network background for WORLDSCOPE.
// Adapted from ihelfrich.github.io/network.js pattern (drifting nodes with
// connecting lines, accent pulses, pointer attraction) and made
// data-driven: node count and accent positions are seeded from today's
// cross-section recurrences when they're available, falling back to a
// time-of-day color cycle when they're not.
//
// Palette uses heritage colors: Carolina Blue (UNC), Indiana Crimson,
// Georgia Tech Old Gold. Background is parchment so the canvas sits
// quietly under content without dominating the page.

(function () {
  const canvas = document.getElementById('ws-network');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let W = 0, H = 0;
  let nodes = [];
  let pointer = { x: -9999, y: -9999, active: false };

  // Heritage palette
  const palette = {
    ink: '#0B1220',
    paper: '#FAF8F3',
    mist: '#E8E2D5',
    slate: '#4E5667',
    navy: '#13294B',
    carolina: '#4B9CD3',  // UNC Blue
    crimson: '#990000',   // Indiana Red
    gold: '#D4A017',      // Georgia Tech Gold
    teal: '#1A8A87',
  };

  // Pull seed data from a JSON blob the page can inline. Falls back to a
  // time-based seed if not present.
  const seedData = (function () {
    const el = document.getElementById('ws-network-seed');
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || '{}');
    } catch (e) {
      return null;
    }
  })();

  function resize() {
    const rect = canvas.getBoundingClientRect();
    W = rect.width;
    H = rect.height;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  // Time-of-day color cycle: morning leans gold, afternoon teal, evening
  // crimson, night carolina. Subtle, never garish.
  function dayPhaseColor() {
    const h = new Date().getHours();
    if (h < 6) return palette.carolina;
    if (h < 12) return palette.gold;
    if (h < 18) return palette.teal;
    return palette.crimson;
  }

  function seed() {
    // Node count scales with viewport but stays restrained: this is
    // background, not a feature.
    const baseCount = Math.min(110, Math.max(60, Math.floor((W * H) / 9000)));

    // If we have cross-section data, accent nodes per high-confidence
    // entity (capped). Otherwise 8% of total go accent.
    let accentCount;
    let accentColors;
    if (seedData && Array.isArray(seedData.recurrences)) {
      const recs = seedData.recurrences;
      accentCount = Math.min(recs.length, Math.floor(baseCount * 0.18));
      // Rotate through three heritage accents by recurrence rank.
      accentColors = [palette.carolina, palette.crimson, palette.gold];
    } else {
      accentCount = Math.floor(baseCount * 0.08);
      accentColors = [dayPhaseColor()];
    }

    nodes = Array.from({ length: baseCount }, (_, i) => {
      const isAccent = i < accentCount;
      const color = isAccent ? accentColors[i % accentColors.length] : palette.slate;
      return {
        x: Math.random() * W,
        y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.18,
        vy: (Math.random() - 0.5) * 0.18,
        r: isAccent ? 2.6 + Math.random() * 1.6 : 1.0 + Math.random() * 1.2,
        accent: isAccent,
        color: color,
        phase: Math.random() * Math.PI * 2,
        baseR: 0
      };
    });
    nodes.forEach(n => (n.baseR = n.r));
  }

  function step(t) {
    ctx.clearRect(0, 0, W, H);

    // First pass: positions + pointer attraction
    for (const n of nodes) {
      n.x += n.vx;
      n.y += n.vy;
      if (n.x < -10) n.x = W + 10;
      if (n.x > W + 10) n.x = -10;
      if (n.y < -10) n.y = H + 10;
      if (n.y > H + 10) n.y = -10;

      if (pointer.active) {
        const dx = pointer.x - n.x;
        const dy = pointer.y - n.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 24000) {
          const f = 0.0008 * (1 - d2 / 24000);
          n.vx += dx * f;
          n.vy += dy * f;
        }
      }
      n.vx *= 0.985;
      n.vy *= 0.985;
      n.vx += (Math.random() - 0.5) * 0.006;
      n.vy += (Math.random() - 0.5) * 0.006;
    }

    // Second pass: connecting lines (only within threshold)
    ctx.lineWidth = 0.7;
    const threshold2 = 12500;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < threshold2) {
          const alpha = (1 - d2 / threshold2) * 0.25;
          // Mist tint, fades with distance
          ctx.strokeStyle = `rgba(78, 86, 103, ${alpha})`;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    // Third pass: nodes themselves. Accents pulse subtly.
    for (const n of nodes) {
      if (n.accent) {
        n.phase += 0.012;
        n.r = n.baseR + Math.sin(n.phase) * 0.4;
        // Soft halo
        const grad = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 4);
        grad.addColorStop(0, n.color + '55');
        grad.addColorStop(1, n.color + '00');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r * 4, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.fillStyle = n.accent ? n.color : 'rgba(78, 86, 103, 0.55)';
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fill();
    }

    requestAnimationFrame(step);
  }

  function onPointer(e) {
    const rect = canvas.getBoundingClientRect();
    pointer.x = e.clientX - rect.left;
    pointer.y = e.clientY - rect.top;
    pointer.active = true;
  }
  function onPointerLeave() {
    pointer.active = false;
    pointer.x = -9999; pointer.y = -9999;
  }

  resize();
  seed();
  window.addEventListener('resize', () => { resize(); seed(); });
  canvas.parentElement.addEventListener('pointermove', onPointer);
  canvas.parentElement.addEventListener('pointerleave', onPointerLeave);
  requestAnimationFrame(step);
})();
