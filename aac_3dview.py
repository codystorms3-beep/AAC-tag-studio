"""Standalone Three.js tag viewer — part one of the 3D view.

The app writes a self-contained HTML page (Three.js inlined, STL parts
embedded as base64) and opens it in the user's default browser. Nothing is
embedded in tkinter yet, and nothing is fetched from the internet: the page
works from a file:// URL with no server.

Three.js is the r147 UMD build (build/three.min.js + examples/js
STLLoader/OrbitControls), the last version that ships plain script tags —
modern three.js is ES modules only, and module scripts are blocked from
file:// URLs. The three files live in viewer3d/ next to this module and are
inlined into the page, so the generated file keeps working even if the app
folder moves later. Three.js is MIT licensed; its license header stays
intact in the inlined source.
"""

import base64
import html
import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_JS_FILES = ("three.min.js", "STLLoader.js", "OrbitControls.js")

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _read_js():
    """The inlined Three.js source, or None when viewer3d/ is incomplete."""
    parts = []
    for name in _JS_FILES:
        p = _HERE / "viewer3d" / name
        if not p.exists():
            return None
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _color(value, fallback):
    v = str(value or "").strip()
    return v if _HEX.match(v) else fallback


def build_viewer_html(word, parts):
    """Build the self-contained viewer page.

    word: caption shown on the page. parts: [(label, stl_bytes, color), ...]
    with one entry per STL part (base in body color, ink parts in ink colors).
    Returns the HTML string, or None when the Three.js files are missing or
    no STL part was supplied.
    """
    js = _read_js()
    if js is None or not parts:
        return None
    meshes = [{"label": str(label),
               "color": _color(color, "#CCCCCC"),
               "stl": base64.b64encode(bytes(data)).decode("ascii")}
              for label, data, color in parts]
    page = _PAGE.replace("__THREE_JS__", js)
    page = page.replace("__MESHES_JSON__", json.dumps(meshes))
    page = page.replace("__WORD__", html.escape(str(word)))
    return page


def build_multi_viewer_html(tags):
    """Build a multi-tag viewer page.

    tags: [(word, parts)] with parts as in build_viewer_html. The tags
    are laid out in a grid on one table, each with a floating word label
    so a tag stays identifiable while orbiting. Returns the HTML string,
    or None when the Three.js files are missing or no tag produced parts.
    Browser page only (the pre-conversion test) — no embed path.
    """
    js = _read_js()
    if js is None:
        return None
    tag_list = []
    for word, parts in tags:
        meshes = [{"label": str(label),
                   "color": _color(color, "#CCCCCC"),
                   "stl": base64.b64encode(bytes(data)).decode("ascii")}
                  for label, data, color in parts]
        if meshes:
            tag_list.append({"word": str(word), "meshes": meshes})
    if not tag_list:
        return None
    page = _PAGE_MULTI.replace("__THREE_JS__", js)
    page = page.replace("__TAGS_JSON__", json.dumps(tag_list))
    page = page.replace("__NTAGS__", str(len(tag_list)))
    page = page.replace("__WORDS__", html.escape(", ".join(x["word"] for x in tag_list)))
    return page


def build_live_viewer_html():
    """Build the live viewer page (v15.35).

    The page polls state.json (same origin, served by the loopback
    server) every 600ms and swaps the tag meshes in place when the
    version changes — camera and orbit are untouched, so editing never
    yanks the view. Returns None when the Three.js files are missing.
    """
    js = _read_js()
    if js is None:
        return None
    return _PAGE_LIVE.replace("__THREE_JS__", js)


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AAC Tag Studio &mdash; 3D view: __WORD__</title>
<style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: #F1EDF5;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  #bar { padding: 10px 14px; background: #fff; border-bottom: 1px solid #ddd;
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
  #bar h1 { font-size: 16px; margin: 0; color: #2E2440; }
  #bar p { font-size: 12px; margin: 0; color: #666; }
  #bar button { margin-left: auto; font-size: 13px; padding: 5px 12px;
    border: 1px solid #A98BD6; border-radius: 8px; background: #E9E4EE;
    color: #2E2440; cursor: pointer; }
  #view { position: absolute; top: 62px; bottom: 0; left: 0; right: 0; }
</style>
</head>
<body>
<div id="bar">
  <h1>3D view: __WORD__</h1>
  <p>Drag to rotate &middot; Scroll to zoom &middot; Right-drag to pan</p>
  <p>Geometry check &mdash; this shows the printed shape. The picture and word show as raised geometry on the tag.</p>
  <button id="reset">Reset view</button>
</div>
<div id="view"></div>
<script>
__THREE_JS__
</script>
<script>
var meshes = __MESHES_JSON__;
function b64ToBuffer(b64) {
  var bin = atob(b64), buf = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}
var scene = new THREE.Scene();
scene.background = new THREE.Color(0xF1EDF5);
var renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight - 62);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.getElementById('view').appendChild(renderer.domElement);
var camera = new THREE.PerspectiveCamera(45, window.innerWidth / (window.innerHeight - 62), 0.1, 10000);
var controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = false;  // v15.28: no glide — damped controls felt floaty
scene.add(new THREE.AmbientLight(0xffffff, 0.55));
var sun = new THREE.DirectionalLight(0xffffff, 1.0);
sun.castShadow = true;
sun.shadow.mapSize.width = 2048;
sun.shadow.mapSize.height = 2048;
scene.add(sun);
var group = new THREE.Group();
// STL files are built with thickness on +Z; lay the tag flat, face up.
group.rotation.x = -Math.PI / 2;
var loader = new THREE.STLLoader();
meshes.forEach(function (m) {
  var geo = loader.parse(b64ToBuffer(m.stl));
  var mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(m.color), roughness: 0.65, metalness: 0.0 });
  var mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  group.add(mesh);
});
scene.add(group);
var box = new THREE.Box3().setFromObject(group);
var size = box.getSize(new THREE.Vector3());
var center = box.getCenter(new THREE.Vector3());
group.position.sub(center);
var maxDim = Math.max(size.x, size.y, size.z);
// v15.28: a visible table the tag rests on — the shadow-only plane left
// the tag floating in space. It sits a hair below the tag's underside so
// the two faces never z-fight, and it still receives the shadow.
var ground = new THREE.Mesh(
  new THREE.PlaneGeometry(maxDim * 4, maxDim * 4),
  new THREE.MeshStandardMaterial({ color: 0xDCD7E6, roughness: 0.95, metalness: 0.0 }));
ground.rotation.x = -Math.PI / 2;
ground.position.y = -size.y / 2 - maxDim * 0.001;
ground.receiveShadow = true;
scene.add(ground);
var homePos = new THREE.Vector3(maxDim * 0.8, maxDim * 0.8, maxDim * 1.1);
camera.position.copy(homePos);
controls.target.set(0, 0, 0);
sun.position.set(maxDim * 0.8, maxDim * 1.5, maxDim * 0.8);
sun.shadow.camera.left = -maxDim;
sun.shadow.camera.right = maxDim;
sun.shadow.camera.top = maxDim;
sun.shadow.camera.bottom = -maxDim;
document.getElementById('reset').addEventListener('click', function () {
  camera.position.copy(homePos);
  controls.target.set(0, 0, 0);
});
window.addEventListener('resize', function () {
  camera.aspect = window.innerWidth / (window.innerHeight - 62);
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight - 62);
});
(function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
})();
</script>
</body>
</html>
"""

_PAGE_MULTI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AAC Tag Studio &mdash; 3D view: __NTAGS__ tags</title>
<style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: #F1EDF5;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  #bar { padding: 10px 14px; background: #fff; border-bottom: 1px solid #ddd;
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
  #bar h1 { font-size: 16px; margin: 0; color: #2E2440; }
  #bar p { font-size: 12px; margin: 0; color: #666; }
  #bar button { margin-left: auto; font-size: 13px; padding: 5px 12px;
    border: 1px solid #A98BD6; border-radius: 8px; background: #E9E4EE;
    color: #2E2440; cursor: pointer; }
  #view { position: absolute; top: 62px; bottom: 0; left: 0; right: 0; }
</style>
</head>
<body>
<div id="bar">
  <h1>3D view: __NTAGS__ tags</h1>
  <p>__WORDS__</p>
  <p>Drag to rotate &middot; Scroll to zoom &middot; Right-drag to pan</p>
  <p>Geometry check &mdash; this shows the printed shapes. Each tag shows its picture and word as raised geometry.</p>
  <button id="reset">Reset view</button>
</div>
<div id="view"></div>
<script>
__THREE_JS__
</script>
<script>
var tags = __TAGS_JSON__;
function b64ToBuffer(b64) {
  var bin = atob(b64), buf = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}
function wordSprite(word, scale) {
  var cv = document.createElement('canvas');
  cv.width = 512; cv.height = 128;
  var ctx = cv.getContext('2d');
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, 512, 128);
  ctx.fillStyle = '#2E2440'; ctx.font = 'bold 64px system-ui, sans-serif';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(word, 256, 68);
  var tex = new THREE.CanvasTexture(cv);
  var mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
  var sp = new THREE.Sprite(mat);
  sp.scale.set(scale, scale / 4, 1);
  sp.renderOrder = 999;
  return sp;
}
var scene = new THREE.Scene();
scene.background = new THREE.Color(0xF1EDF5);
var renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight - 62);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.getElementById('view').appendChild(renderer.domElement);
var camera = new THREE.PerspectiveCamera(45, window.innerWidth / (window.innerHeight - 62), 0.1, 10000);
var controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = false;
scene.add(new THREE.AmbientLight(0xffffff, 0.55));
var sun = new THREE.DirectionalLight(0xffffff, 1.0);
sun.castShadow = true;
sun.shadow.mapSize.width = 2048;
sun.shadow.mapSize.height = 2048;
scene.add(sun);
var loader = new THREE.STLLoader();
// Pass 1: build every tag flat (STL +Z rotated to +Y), measure footprints.
var built = [];
tags.forEach(function (tg) {
  var inner = new THREE.Group();
  inner.rotation.x = -Math.PI / 2;
  tg.meshes.forEach(function (m) {
    var geo = loader.parse(b64ToBuffer(m.stl));
    var mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(m.color), roughness: 0.65, metalness: 0.0 });
    var mesh = new THREE.Mesh(geo, mat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    inner.add(mesh);
  });
  var outer = new THREE.Group();
  outer.add(inner);
  scene.add(outer);
  scene.updateMatrixWorld(true);
  var box = new THREE.Box3().setFromObject(inner);
  var size = box.getSize(new THREE.Vector3());
  var center = box.getCenter(new THREE.Vector3());
  // Center the footprint on the cell and rest the underside on y = 0,
  // so tags of different thickness still share one table.
  inner.position.set(-center.x, -box.min.y, -center.z);
  built.push({ outer: outer, word: tg.word, w: size.x, d: size.z, h: size.y });
});
var cell = 0;
built.forEach(function (b) { cell = Math.max(cell, b.w, b.d); });
cell = cell * 1.25;
var cols = Math.ceil(Math.sqrt(built.length));
var rows = Math.ceil(built.length / cols);
var gridW = cols * cell, gridD = rows * cell;
var gridSize = Math.max(gridW, gridD);
// Pass 2: place cells, centered on the origin, with a floating word label.
built.forEach(function (b, i) {
  var col = i % cols, row = Math.floor(i / cols);
  var x = (col - (cols - 1) / 2) * cell;
  var z = (row - (rows - 1) / 2) * cell;
  b.outer.position.set(x, 0, z);
  var sp = wordSprite(b.word, cell * 0.6);
  sp.position.set(x, b.h + cell * 0.18, z);
  scene.add(sp);
});
var ground = new THREE.Mesh(
  new THREE.PlaneGeometry(gridSize * 1.6, gridSize * 1.6),
  new THREE.MeshStandardMaterial({ color: 0xDCD7E6, roughness: 0.95, metalness: 0.0 }));
ground.rotation.x = -Math.PI / 2;
ground.position.y = -gridSize * 0.001;
ground.receiveShadow = true;
scene.add(ground);
var homePos = new THREE.Vector3(gridSize * 0.8, gridSize * 0.8, gridSize * 1.1);
camera.position.copy(homePos);
controls.target.set(0, 0, 0);
sun.position.set(gridSize * 0.8, gridSize * 1.5, gridSize * 0.8);
sun.shadow.camera.left = -gridSize;
sun.shadow.camera.right = gridSize;
sun.shadow.camera.top = gridSize;
sun.shadow.camera.bottom = -gridSize;
document.getElementById('reset').addEventListener('click', function () {
  camera.position.copy(homePos);
  controls.target.set(0, 0, 0);
});
window.addEventListener('resize', function () {
  camera.aspect = window.innerWidth / (window.innerHeight - 62);
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight - 62);
});
(function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
})();
</script>
</body>
</html>
"""


_PAGE_LIVE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AAC Tag Studio &mdash; Live 3D view</title>
<style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: #F1EDF5;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  #bar { padding: 10px 14px; background: #fff; border-bottom: 1px solid #ddd;
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
  #bar h1 { font-size: 16px; margin: 0; color: #2E2440; }
  #bar p { font-size: 12px; margin: 0; color: #666; }
  #bar button { margin-left: auto; font-size: 13px; padding: 5px 12px;
    border: 1px solid #A98BD6; border-radius: 8px; background: #E9E4EE;
    color: #2E2440; cursor: pointer; }
  #view { position: absolute; top: 62px; bottom: 0; left: 0; right: 0; }
</style>
</head>
<body>
<div id="bar">
  <h1>Live 3D view: <span id="liveword">&hellip;</span></h1>
  <p>Live &mdash; updates as you edit &middot; Drag to rotate &middot; Scroll to zoom &middot; Right-drag to pan</p>
  <p id="liveupdated">Waiting for the first tag&hellip;</p>
  <button id="reset">Reset view</button>
</div>
<div id="view"></div>
<script>
__THREE_JS__
</script>
<script>
var scene = new THREE.Scene();
scene.background = new THREE.Color(0xF1EDF5);
var renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight - 62);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.getElementById('view').appendChild(renderer.domElement);
var camera = new THREE.PerspectiveCamera(45, window.innerWidth / (window.innerHeight - 62), 0.1, 10000);
var controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = false;
scene.add(new THREE.AmbientLight(0xffffff, 0.55));
var sun = new THREE.DirectionalLight(0xffffff, 1.0);
sun.castShadow = true;
sun.shadow.mapSize.width = 2048;
sun.shadow.mapSize.height = 2048;
scene.add(sun);
var loader = new THREE.STLLoader();
var currentVersion = -1, currentGroup = null, currentGround = null, lastMaxDim = 1, firstLoad = true;
var homePos = new THREE.Vector3(1, 1, 1);
function setHome(maxDim) {
  lastMaxDim = maxDim;
  homePos = new THREE.Vector3(maxDim * 0.8, maxDim * 0.8, maxDim * 1.1);
  camera.position.copy(homePos);
  controls.target.set(0, 0, 0);
  sun.position.set(maxDim * 0.8, maxDim * 1.5, maxDim * 0.8);
  sun.shadow.camera.left = -maxDim;
  sun.shadow.camera.right = maxDim;
  sun.shadow.camera.top = maxDim;
  sun.shadow.camera.bottom = -maxDim;
}
function loadTag(state) {
  // Fetch every part first; only swap the scene when all parse OK,
  // so a failed update never blanks the view.
  var jobs = state.meshes.map(function (m) {
    return fetch(m.url, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('mesh fetch failed');
      return r.arrayBuffer();
    }).then(function (buf) {
      return { geo: loader.parse(buf), color: m.color };
    });
  });
  Promise.all(jobs).then(function (parts) {
    var group = new THREE.Group();
    group.rotation.x = -Math.PI / 2;
    parts.forEach(function (pt) {
      var mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(pt.color), roughness: 0.65, metalness: 0.0 });
      var mesh = new THREE.Mesh(pt.geo, mat);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      group.add(mesh);
    });
    scene.add(group);
    scene.updateMatrixWorld(true);
    var box = new THREE.Box3().setFromObject(group);
    var size = box.getSize(new THREE.Vector3());
    var center = box.getCenter(new THREE.Vector3());
    group.position.sub(center);
    var maxDim = Math.max(size.x, size.y, size.z);
    var ground = new THREE.Mesh(
      new THREE.PlaneGeometry(maxDim * 4, maxDim * 4),
      new THREE.MeshStandardMaterial({ color: 0xDCD7E6, roughness: 0.95, metalness: 0.0 }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -size.y / 2 - maxDim * 0.001;
    ground.receiveShadow = true;
    scene.add(ground);
    if (currentGroup) { scene.remove(currentGroup); }
    if (currentGround) { scene.remove(currentGround); }
    currentGroup = group;
    currentGround = ground;
    if (firstLoad) { setHome(maxDim); firstLoad = false; }
    document.getElementById('liveword').textContent = state.word;
    document.getElementById('liveupdated').textContent =
      'Updated ' + new Date().toLocaleTimeString() + ' \u00b7 v' + state.version;
  }).catch(function () { /* keep the previous tag; next poll retries */ });
}
var failCount = 0;
function poll() {
  fetch('state.json', { cache: 'no-store' }).then(function (r) {
    if (!r.ok) return null;
    return r.json();
  }).then(function (state) {
    failCount = 0;
    if (state && state.version !== currentVersion) {
      currentVersion = state.version;
      loadTag(state);
    }
  }).catch(function () {
    // Network failure (not a 404): the server is gone — e.g. an old tab
    // from a previous app run. Say so instead of freezing silently.
    failCount++;
    if (failCount === 10) {
      document.getElementById('liveupdated').textContent =
        'Live server not responding — reopen Live 3D viewer in the app';
    }
  });
}
document.getElementById('reset').addEventListener('click', function () {
  setHome(lastMaxDim);
});
window.addEventListener('resize', function () {
  camera.aspect = window.innerWidth / (window.innerHeight - 62);
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight - 62);
});
setHome(1);
poll();
setInterval(poll, 600);
(function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
})();
</script>
</body>
</html>
"""
