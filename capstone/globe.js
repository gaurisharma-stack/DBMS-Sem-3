// 3D knowledge globe: the Kaggle knowledge base drawn live from MySQL.
// Outer shell = 131 symptoms (grouped by body system), inner core = 41 diseases,
// threads = disease_symptoms links. Selected symptoms and matched diseases light up.
import * as THREE from './vendor/three.module.min.js';

const AQUA = new THREE.Color('#6ee7d8'), DEEP = new THREE.Color('#2b6f86'),
      CORAL = new THREE.Color('#ff8e7f'), GOLD = new THREE.Color('#f5c46b');
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

let renderer, scene, camera, root, slot, tip, ro, data = null, built = false;
let symPts, disPts, hiSym, hiDis, baseLines, hiLines, shell;
let symPos = {}, disPos = {}, symName = {}, disName = {};
let rotY = 0.6, rotX = 0.25, velY = reduce ? 0 : 0.0016, drag = null, raf = 0;
const pointer = new THREE.Vector2(-9, -9), ray = new THREE.Raycaster();

function dot() {           // soft round sprite for points
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d'), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(.35, 'rgba(255,255,255,.85)');
  r.addColorStop(1, 'rgba(255,255,255,0)'); g.fillStyle = r; g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}
const SPRITE = dot();
const mat = (size, opacity = 1) => new THREE.PointsMaterial({ size, map: SPRITE, vertexColors: true, transparent: true,
  opacity, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true });

function fib(n, r, i) {    // even spread of n points on a sphere
  const y = 1 - (i + .5) / n * 2, rad = Math.sqrt(1 - y * y), th = i * Math.PI * (3 - Math.sqrt(5));
  return new THREE.Vector3(Math.cos(th) * rad * r, y * r, Math.sin(th) * rad * r);
}

function init() {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(38, 1, .1, 50); camera.position.set(0, 0, 4.2);
  root = new THREE.Group(); scene.add(root);
  shell = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(1.42, 2)),
    new THREE.LineBasicMaterial({ color: DEEP, transparent: true, opacity: .16 }));
  root.add(shell);
  const cv = renderer.domElement; cv.className = 'globe-canvas';
  cv.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY }; cv.setPointerCapture(e.pointerId); });
  cv.addEventListener('pointermove', e => {
    const b = cv.getBoundingClientRect();
    pointer.set((e.clientX - b.left) / b.width * 2 - 1, -(e.clientY - b.top) / b.height * 2 + 1);
    pointer.cx = e.clientX - b.left; pointer.cy = e.clientY - b.top;
    if (!drag) return;
    velY = (e.clientX - drag.x) * .0009; rotX += (e.clientY - drag.y) * .004;
    rotX = Math.max(-1.1, Math.min(1.1, rotX)); drag = { x: e.clientX, y: e.clientY };
  });
  const up = () => { drag = null; if (!reduce) velY = Math.sign(velY || 1) * Math.max(.0016, Math.min(.02, Math.abs(velY))); else velY = 0; };
  cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
  cv.addEventListener('pointerleave', () => { pointer.set(-9, -9); if (tip) tip.hidden = true; });
  tip = document.createElement('div'); tip.className = 'globe-tip'; tip.hidden = true;
  ray.params.Points.threshold = .05;
}

function build() {
  if (!root || !data || built || !data.links || !data.links.length) return;
  const syms = [...data.symptoms].sort((a, b) => (a.sys || '').localeCompare(b.sys || '') || a.n.localeCompare(b.n));
  const sp = [], sc = [];
  syms.forEach((s, i) => {
    const v = fib(syms.length, 1.42, i); symPos[s.id] = v; symName[s.id] = s.n; sp.push(v.x, v.y, v.z);
    const c = DEEP.clone().lerp(AQUA, .35 + (s.w || 3) / 10); sc.push(c.r, c.g, c.b);
  });
  const g1 = new THREE.BufferGeometry(); g1.setAttribute('position', new THREE.Float32BufferAttribute(sp, 3));
  g1.setAttribute('color', new THREE.Float32BufferAttribute(sc, 3)); g1.userData.ids = syms.map(s => s.id);
  symPts = new THREE.Points(g1, mat(.075)); root.add(symPts);

  const dp = [], dc = [];
  data.diseases.forEach((d, i) => {
    const v = fib(data.diseases.length, .7, i); disPos[d.id] = v; disName[d.id] = d.n; dp.push(v.x, v.y, v.z);
    const c = GOLD.clone().lerp(new THREE.Color('#ffffff'), .15).multiplyScalar(.55 + d.sev * .09); dc.push(c.r, c.g, c.b);
  });
  const g2 = new THREE.BufferGeometry(); g2.setAttribute('position', new THREE.Float32BufferAttribute(dp, 3));
  g2.setAttribute('color', new THREE.Float32BufferAttribute(dc, 3)); g2.userData.ids = data.diseases.map(d => d.id);
  disPts = new THREE.Points(g2, mat(.11, .9)); root.add(disPts);

  const lp = [], lc = [];
  data.links.forEach(([d, s, w]) => {
    const a = disPos[d], b = symPos[s]; if (!a || !b) return;
    lp.push(a.x, a.y, a.z, b.x, b.y, b.z);
    const c = DEEP.clone().lerp(AQUA, w * .6); lc.push(c.r * .5, c.g * .5, c.b * .5, c.r, c.g, c.b);
  });
  const g3 = new THREE.BufferGeometry(); g3.setAttribute('position', new THREE.Float32BufferAttribute(lp, 3));
  g3.setAttribute('color', new THREE.Float32BufferAttribute(lc, 3));
  baseLines = new THREE.LineSegments(g3, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: .32,
    depthWrite: false, blending: THREE.AdditiveBlending }));
  root.add(baseLines);
  built = true;
  highlight(window.Globe._hl || {});
}

function pts(ids, pos, color, size) {
  const p = [], c = [];
  ids.forEach(id => { const v = pos[id]; if (v) { p.push(v.x, v.y, v.z); c.push(color.r, color.g, color.b); } });
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(p, 3));
  g.setAttribute('color', new THREE.Float32BufferAttribute(c, 3));
  return new THREE.Points(g, mat(size));
}

function highlight({ symptoms = [], diseases = [] }) {
  window.Globe._hl = { symptoms, diseases };
  if (!built) return;
  [hiSym, hiDis, hiLines].forEach(o => { if (o) { root.remove(o); o.geometry.dispose(); } });
  hiSym = hiDis = hiLines = null;
  const S = new Set(symptoms.map(Number)), D = new Set(diseases.map(Number));
  baseLines.material.opacity = S.size || D.size ? .1 : .32;
  if (S.size) { hiSym = pts([...S], symPos, CORAL, .16); root.add(hiSym); }
  if (D.size) { hiDis = pts([...D], disPos, GOLD, .22); root.add(hiDis); }
  const lp = [], lc = [];
  data.links.forEach(([d, s]) => {
    const on = D.size ? D.has(d) && (S.has(s) || !S.size) : S.has(s);
    if (!on) return; const a = disPos[d], b = symPos[s];
    lp.push(a.x, a.y, a.z, b.x, b.y, b.z); lc.push(GOLD.r, GOLD.g, GOLD.b, CORAL.r, CORAL.g, CORAL.b);
  });
  if (lp.length) {
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(lp, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(lc, 3));
    hiLines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: .85,
      depthWrite: false, blending: THREE.AdditiveBlending }));
    root.add(hiLines);
  }
}

function size() {
  if (!slot) return; const w = slot.clientWidth, h = slot.clientHeight; if (!w || !h) return;
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
}

function hover() {
  if (!built || drag || pointer.x < -2) { tip.hidden = true; return; }
  ray.setFromCamera(pointer, camera);
  const hit = ray.intersectObjects([disPts, symPts])[0];
  if (!hit) { tip.hidden = true; slot.style.cursor = 'grab'; return; }
  const isD = hit.object === disPts, id = hit.object.geometry.userData.ids[hit.index];
  tip.textContent = (isD ? 'Disease: ' : 'Symptom: ') + (isD ? disName[id] : symName[id]);
  tip.style.transform = `translate(${pointer.cx + 14}px, ${pointer.cy - 10}px)`; tip.hidden = false; slot.style.cursor = 'pointer';
}

function loop() {
  raf = requestAnimationFrame(loop);
  if (!slot || !slot.isConnected || document.hidden) return;
  rotY += velY; if (!drag && !reduce) velY += (Math.sign(velY || 1) * .0016 - velY) * .02;
  root.rotation.set(rotX, rotY, 0);
  if (hiDis) hiDis.material.size = .22 + Math.sin(performance.now() / 380) * .04 * (reduce ? 0 : 1);
  hover(); renderer.render(scene, camera);
}

window.Globe = Object.assign(window.Globe || {}, {
  ready: true,
  mount(el) {
    try { if (!renderer) init(); } catch (e) { el.classList.add('no-webgl'); return; }
    slot = el; el.appendChild(renderer.domElement); el.appendChild(tip);
    if (ro) ro.disconnect(); ro = new ResizeObserver(size); ro.observe(el); size(); build();
    if (!raf) loop();
  },
  setData(meta) { data = meta; build(); },
  highlight,
});
// the app may have asked to mount before this module finished loading
if (window.Globe._pending) { const p = window.Globe._pending; delete window.Globe._pending;
  if (p.data) window.Globe.setData(p.data); if (p.el) window.Globe.mount(p.el); }
