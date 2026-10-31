"""OpenGL in-program 3D preview (v15.46) — replaces the PIL renderer.

The Maker/Tag Design preview cell now holds a real GPU view: a
pyopengltk OpenGLFrame (a tkinter Frame that owns an OpenGL context)
drawing the exported STL triangles with shaders — ambient + directional
light, a ground plane, soft contact shadows, and the butter selection
glow. It renders on demand (new scene, camera move, resize) on the main
thread; the export + parse work still runs off-thread, and only the
upload + draw happen on the main thread.

v15.58 look: a lavender sky gradient behind the scene, a faint world
grid on the floor, a specular + rim term on the lit shader for a
glossier tag edge, and selection you can actually see — a violet ring
on the floor around the selected tag (v15.66: the inflated-mesh
outline is gone — from the tilted view it painted violet halos over
the raised ink, the "stringing" across the selected tag's face).

Supersampling: the scene renders to a 2x framebuffer and is blitted
down, so edges are antialiased even when the context has no
multisample buffers. If that path fails it falls back to direct
rendering. If OpenGL or pyopengltk is missing entirely, create_gl_view()
returns None and the caller shows a plain message instead of a blank
preview — importing this module never crashes the app, because all
OpenGL imports are lazy (inside create_gl_view).

Geometry helpers (parse_stl, fit_distance, project_bounds, gc_hold)
moved here verbatim from the retired aac_3drender.py — they are pure
math/parsing, not rendering, and the picking code still uses them.
"""

import gc
import math
import struct
import threading

import numpy as np

# ---------------------------------------------------------------- helpers

_gc_lock = threading.Lock()
_gc_depth = [0]


def gc_hold():
    """Refcounted GC hold for worker threads (see v15.39 note in git
    history: a worker render loop allocating millions of small tuples
    triggered cyclic GC that finalized a dead main-thread tkinter Font
    and deadlocked). Kept for the export+parse worker."""
    with _gc_lock:
        if _gc_depth[0] == 0:
            gc.disable()
        _gc_depth[0] += 1


def gc_release():
    with _gc_lock:
        _gc_depth[0] -= 1
        if _gc_depth[0] == 0:
            gc.enable()


def parse_stl(data):
    """Parse STL bytes (binary or ascii) -> list of ((x,y,z) * 3) triangles."""
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if count > 0 and len(data) == 84 + 50 * count:
            tris = []
            off = 84
            for _ in range(count):
                vals = struct.unpack_from("<12fH", data, off)
                off += 50
                tris.append(((vals[3], vals[4], vals[5]),
                             (vals[6], vals[7], vals[8]),
                             (vals[9], vals[10], vals[11])))
            return tris
    try:
        text = data.decode("utf-8", "ignore")
    except Exception:
        return []
    verts = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("vertex "):
            try:
                _, xs, ys, zs = line.split()
                verts.append((float(xs), float(ys), float(zs)))
            except Exception:
                pass
    return [(verts[i], verts[i + 1], verts[i + 2])
            for i in range(0, len(verts) - 2, 3)]


def hex_to_rgb(s):
    """Hex color -> (r, g, b). Tolerates #RGB, #RRGGBB, and a few names."""
    if not isinstance(s, str):
        return (200, 200, 200)
    s = s.strip()
    if s.startswith("#"):
        h = s[1:]
        try:
            if len(h) == 3:
                return tuple(int(c * 2, 16) for c in h)
            if len(h) >= 6:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            pass
    return {"white": (255, 255, 255), "black": (0, 0, 0),
            "red": (220, 60, 60), "blue": (60, 120, 220),
            "green": (60, 180, 120)}.get(s.lower(), (200, 200, 200))


def _norm(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / m, v[1] / m, v[2] / m) if m else (0.0, 0.0, 1.0)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


_LIGHT = _norm((-0.45, -0.35, 0.82))


def fit_distance(radius, fov_deg=38.0):
    """Camera distance that fits a bounding sphere of `radius` in view.

    v15.66: never frame tighter than a 120 mm scene — a single small
    tag used to fill the whole view (its own bounds are tiny, so the
    fit zoomed right onto it). Wheel zoom still sets dist directly,
    so zooming in close by hand is unaffected."""
    radius = max(radius, 60.0)
    return radius / math.tan(math.radians(fov_deg) / 2.0) * 1.08


def _basis(azim_deg, elev_deg, dist, target, w, h, fov_deg):
    """Camera basis: (eye, right, up, fwd, scale, cx, cy).

    v15.49: the preview is orthographic (slicer-style), so `scale` is
    pixels per world unit — (h / 2) / (dist * tan(fov / 2)) — the same
    framing the old perspective view had at the target plane, which
    keeps fit_distance valid and wheel zoom (dist) working as before.
    """
    elev = max(8.0, min(82.0, elev_deg))
    az = math.radians(azim_deg)
    el = math.radians(elev)
    ce, se = math.cos(el), math.sin(el)
    eye = (target[0] + dist * ce * math.sin(az),
           target[1] + dist * ce * math.cos(az),
           target[2] + dist * se)
    fwd = _norm((target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]))
    right = _norm(_cross(fwd, (0.0, 0.0, 1.0)))
    up = _cross(right, fwd)
    scale = (h / 2.0) / (dist * math.tan(math.radians(fov_deg) / 2.0))
    return (eye, right, up, fwd, scale, w / 2.0, h / 2.0)


def _project(basis, v):
    eye, right, up, fwd, scale, cx, cy = basis
    rx = v[0] - eye[0]; ry = v[1] - eye[1]; rz = v[2] - eye[2]
    x = rx * right[0] + ry * right[1] + rz * right[2]
    y = rx * up[0] + ry * up[1] + rz * up[2]
    z = rx * fwd[0] + ry * fwd[1] + rz * fwd[2]
    if z <= 0.1:
        return None
    # Orthographic: no perspective divide — a flat tag projects the
    # same at any distance, which is what kills the cone stretching.
    return (cx + x * scale, cy - y * scale, z)


def project_bounds(tris, azim_deg, elev_deg, dist, target, width, height,
                   fov_deg=38.0):
    """Screen-space (cx, cy, rx, ry) of a triangle set, for picking/glow.
    Orthographic, matching the slicer-style preview (v15.49)."""
    basis = _basis(azim_deg, elev_deg, dist, target, width, height, fov_deg)
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for tri in tris:
        for v in tri:
            p = _project(basis, v)
            if p is None:
                continue
            minx = min(minx, p[0]); maxx = max(maxx, p[0])
            miny = min(miny, p[1]); maxy = max(maxy, p[1])
    if minx == float("inf"):
        return (0, 0, 0, 0)
    return ((minx + maxx) / 2.0, (miny + maxy) / 2.0,
            (maxx - minx) / 2.0, (maxy - miny) / 2.0)


# ---------------------------------------------------------------- GL view

_SS = 2  # supersample factor for the offscreen framebuffer

_VS_LIT = """
#version 330 core
layout(location = 0) in vec3 pos;
layout(location = 1) in vec3 normal;
uniform mat4 mvp;
uniform mat3 normalMat;
uniform vec2 inflateCenter;
uniform float inflate;
out vec3 vN;
void main() {
    vec3 p = pos;
    p.xy = inflateCenter + (pos.xy - inflateCenter) * inflate;
    gl_Position = mvp * vec4(p, 1.0);
    vN = normalMat * normal;
}
"""

_FS_LIT = """
#version 330 core
in vec3 vN;
uniform vec3 color;
uniform vec3 sunDir;
out vec4 frag;
void main() {
    // vN and sunDir are both in VIEW space (normalMat rotates the
    // object-space normals), so the camera looks down +Z and the
    // surface-to-camera direction is (0, 0, 1). Diffuse is unchanged
    // from the old object-space dot (rotations preserve dots); the
    // rim + specular are new in v15.58 and give the tag's edge the
    // glossy look: side walls (normals away from the camera) pick up
    // a soft light rim, flat faces a gentle specular sheen.
    vec3 N = normalize(vN);
    vec3 V = vec3(0.0, 0.0, 1.0);
    float d = max(dot(N, normalize(sunDir)), 0.0);
    float shade = clamp(0.55 + 0.90 * d, 0.0, 1.0);
    float spec = pow(max(dot(N, normalize(normalize(sunDir) + V)), 0.0), 64.0);
    float rim = pow(1.0 - max(dot(N, V), 0.0), 3.0);
    vec3 col = color * shade + vec3(1.0) * spec * 0.12
               + vec3(0.95, 0.93, 1.0) * rim * 0.30;
    frag = vec4(col, 1.0);
}
"""

_VS_GRAD = """
#version 330 core
layout(location = 0) in vec2 pos;
out vec2 vUv;
void main() {
    vUv = pos * 0.5 + 0.5;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

_FS_GRAD = """
#version 330 core
in vec2 vUv;
uniform vec3 topColor;
uniform vec3 bottomColor;
out vec4 frag;
void main() {
    frag = vec4(mix(bottomColor, topColor, vUv.y), 1.0);
}
"""

_VS_FLAT = """
#version 330 core
layout(location = 0) in vec2 pos;
uniform mat4 mvp;
uniform vec2 translate;
uniform vec2 scale;
out vec2 vLocal;
void main() {
    vLocal = pos;
    vec2 world = translate + pos * scale;
    gl_Position = mvp * vec4(world.x, world.y, 0.0, 1.0);
}
"""

_FS_FLAT = """
#version 330 core
in vec2 vLocal;
uniform vec4 color;
uniform float radial;
uniform float ringMode;
out vec4 frag;
void main() {
    float a = color.a;
    if (radial > 0.5) {
        float r = length(vLocal);
        a *= smoothstep(1.0, 0.55, r);
        if (a < 0.01) discard;
    }
    if (ringMode > 0.5) {
        // v15.58 selection ring: a band just outside the footprint.
        float r = length(vLocal);
        a *= smoothstep(0.74, 0.86, r) * (1.0 - smoothstep(0.93, 1.0, r));
        if (a < 0.01) discard;
    }
    frag = vec4(color.rgb, a);
}
"""


def _ortho(left, right, bottom, top, near, far):
    """Orthographic matrix (textbook layout, uploaded with GL_TRUE)."""
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0] = 2.0 / (right - left)
    m[0, 3] = -(right + left) / (right - left)
    m[1, 1] = 2.0 / (top - bottom)
    m[1, 3] = -(top + bottom) / (top - bottom)
    m[2, 2] = -2.0 / (far - near)
    m[2, 3] = -(far + near) / (far - near)
    m[3, 3] = 1.0
    return m


def _look_at(eye, target):
    fwd = np.array(target, dtype=np.float64) - np.array(eye, dtype=np.float64)
    fwd = fwd / np.linalg.norm(fwd)
    up0 = np.array([0.0, 0.0, 1.0])
    right = np.cross(fwd, up0)
    right = right / np.linalg.norm(right)
    up = np.cross(right, fwd)
    m = np.eye(4, dtype=np.float32)
    m[0, 0:3] = right
    m[1, 0:3] = up
    m[2, 0:3] = -fwd
    m[0, 3] = -float(np.dot(right, eye))
    m[1, 3] = -float(np.dot(up, eye))
    m[2, 3] = float(np.dot(fwd, eye))
    return m


def create_gl_view(master):
    """Build the OpenGL preview widget, or None if OpenGL is unavailable.

    All OpenGL imports happen here so that importing this module (and
    therefore the whole GUI) never fails when the packages are missing.
    """
    try:
        from pyopengltk import OpenGLFrame
        from OpenGL import GL
        from OpenGL.GL import shaders
    except Exception:
        return None

    class GLView(OpenGLFrame):
        def __init__(self, master):
            OpenGLFrame.__init__(self, master)
            self.animate = 0
            self._scene = None   # (groups, footprints) or None
            self._cam = None
            self._bg = (241, 237, 245)
            self._ground = (220, 215, 230)
            self._sel = None
            self._gl = None      # compiled programs + buffers
            self._dirty = True
            self._fbo = None
            self._fbo_size = (0, 0)
            self._fbo_ok = True

        # ---- public API used by the GUI ----
        def set_scene(self, layout, ranges, cam, bg, ground, sel):
            """Replace the scene (laid-out (tris, color) groups + word
            ranges) and redraw. Safe to call before the widget is mapped:
            the upload + draw happen on the first redraw after mapping."""
            groups = []
            footprints = {}
            for (w, s, e) in ranges:
                xs = []; ys = []
                for tris, _c in layout[s:e]:
                    for tri in tris:
                        for v in tri:
                            xs.append(v[0]); ys.append(v[1])
                if xs:
                    footprints[w] = ((min(xs) + max(xs)) / 2.0,
                                     (min(ys) + max(ys)) / 2.0,
                                     (max(xs) - min(xs)) / 2.0,
                                     (max(ys) - min(ys)) / 2.0)
            for tris, color in layout:
                # Opts colors arrive as hex strings ("#E88CA0"); the GL
                # shader needs (r, g, b) tuples. Tuples pass through.
                if isinstance(color, str):
                    color = hex_to_rgb(color)
                groups.append((tris, color))
            self._scene = (groups, footprints)
            self._cam = dict(cam)
            self._bg = bg
            self._ground = ground
            self._sel = sel
            self._layout_ranges = list(ranges)
            self._dirty = True
            if self.context_created:
                self._display()

        def set_camera(self, cam, sel=None):
            self._cam = dict(cam)
            if sel is not None:
                self._sel = sel
            if self.context_created:
                self._display()

        def refresh(self):
            if self.context_created:
                self._display()

        # ---- pyopengltk lifecycle ----
        def initgl(self):
            # pyopengltk calls initgl() on every resize too, so this must
            # be idempotent: compile once, then just return (the base
            # class already set the viewport before calling us).
            if self._gl is not None:
                return
            prog_lit = shaders.compileProgram(
                shaders.compileShader(_VS_LIT, GL.GL_VERTEX_SHADER),
                shaders.compileShader(_FS_LIT, GL.GL_FRAGMENT_SHADER))
            prog_flat = shaders.compileProgram(
                shaders.compileShader(_VS_FLAT, GL.GL_VERTEX_SHADER),
                shaders.compileShader(_FS_FLAT, GL.GL_FRAGMENT_SHADER))
            prog_grad = shaders.compileProgram(
                shaders.compileShader(_VS_GRAD, GL.GL_VERTEX_SHADER),
                shaders.compileShader(_FS_GRAD, GL.GL_FRAGMENT_SHADER))
            vao = GL.glGenVertexArrays(1)
            vbo = GL.glGenBuffers(1)
            grid_vao = GL.glGenVertexArrays(1)
            grid_vbo = GL.glGenBuffers(1)
            GL.glBindVertexArray(grid_vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, grid_vbo)
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE,
                                     8, None)
            quad_vao = GL.glGenVertexArrays(1)
            quad_vbo = GL.glGenBuffers(1)
            quad = np.array([-1.0, -1.0, 1.0, -1.0, 1.0, 1.0,
                             -1.0, -1.0, 1.0, 1.0, -1.0, 1.0],
                            dtype=np.float32)
            GL.glBindVertexArray(quad_vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, quad_vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, quad.nbytes, quad,
                            GL.GL_STATIC_DRAW)
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE,
                                     8, None)
            self._gl = {
                "lit": prog_lit, "flat": prog_flat, "grad": prog_grad,
                "vao": vao, "vbo": vbo,
                "grid_vao": grid_vao, "grid_vbo": grid_vbo,
                "quad_vao": quad_vao,
                "lit_mvp": GL.glGetUniformLocation(prog_lit, "mvp"),
                "lit_color": GL.glGetUniformLocation(prog_lit, "color"),
                "lit_sun": GL.glGetUniformLocation(prog_lit, "sunDir"),
                "lit_nmat": GL.glGetUniformLocation(prog_lit, "normalMat"),
                "lit_ctr": GL.glGetUniformLocation(prog_lit, "inflateCenter"),
                "lit_infl": GL.glGetUniformLocation(prog_lit, "inflate"),
                "flat_mvp": GL.glGetUniformLocation(prog_flat, "mvp"),
                "flat_tr": GL.glGetUniformLocation(prog_flat, "translate"),
                "flat_sc": GL.glGetUniformLocation(prog_flat, "scale"),
                "flat_color": GL.glGetUniformLocation(prog_flat, "color"),
                "flat_radial": GL.glGetUniformLocation(prog_flat, "radial"),
                "flat_ring": GL.glGetUniformLocation(prog_flat, "ringMode"),
                "grad_top": GL.glGetUniformLocation(prog_grad, "topColor"),
                "grad_bot": GL.glGetUniformLocation(prog_grad, "bottomColor"),
            }

        def _upload(self):
            groups, _fp = self._scene
            # v15.58: which word each layout group belongs to (ranges
            # are slices over the layout), so the selection outline
            # pass can draw only the selected word's spans.
            word_of = [None] * len(groups)
            for (w, s, e) in getattr(self, "_layout_ranges", []) or []:
                for gi in range(max(0, s), min(len(groups), e)):
                    word_of[gi] = w
            verts = []
            spans = []
            span_words = []
            off = 0
            for gi, (tris, color) in enumerate(groups):
                for tri in tris:
                    n = _norm(_cross((tri[1][0] - tri[0][0],
                                      tri[1][1] - tri[0][1],
                                      tri[1][2] - tri[0][2]),
                                     (tri[2][0] - tri[0][0],
                                      tri[2][1] - tri[0][1],
                                      tri[2][2] - tri[0][2])))
                    for v in tri:
                        verts.extend((v[0], v[1], v[2], n[0], n[1], n[2]))
                cnt = sum(len(t) for t in tris)
                spans.append((off, cnt, color))
                span_words.append(word_of[gi])
                off += cnt
            data = np.array(verts, dtype=np.float32)
            g = self._gl
            GL.glBindVertexArray(g["vao"])
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, g["vbo"])
            GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data,
                            GL.GL_STATIC_DRAW)
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE,
                                     24, GL.GLvoidp(0))
            GL.glEnableVertexAttribArray(1)
            GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE,
                                     24, GL.GLvoidp(12))
            self._spans = spans
            self._span_words = span_words
            self._dirty = False

        def _ensure_fbo(self, w, h):
            if not self._fbo_ok:
                return None
            if self._fbo is not None and self._fbo_size == (w, h):
                return self._fbo
            try:
                if self._fbo is not None:
                    GL.glDeleteFramebuffers(1, [self._fbo[0]])
                    GL.glDeleteRenderbuffers(2, self._fbo[1:])
                fbo = GL.glGenFramebuffers(1)
                color_rb = GL.glGenRenderbuffers(1)
                depth_rb = GL.glGenRenderbuffers(1)
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
                GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, color_rb)
                GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8,
                                         w, h)
                GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER,
                                             GL.GL_COLOR_ATTACHMENT0,
                                             GL.GL_RENDERBUFFER, color_rb)
                GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, depth_rb)
                GL.glRenderbufferStorage(GL.GL_RENDERBUFFER,
                                         GL.GL_DEPTH_COMPONENT24, w, h)
                GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER,
                                             GL.GL_DEPTH_ATTACHMENT,
                                             GL.GL_RENDERBUFFER, depth_rb)
                ok = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) == \
                    GL.GL_FRAMEBUFFER_COMPLETE
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
                if not ok:
                    raise RuntimeError("incomplete framebuffer")
                self._fbo = (fbo, color_rb, depth_rb)
                self._fbo_size = (w, h)
                return self._fbo
            except Exception:
                self._fbo_ok = False
                self._fbo = None
                return None

        def redraw(self):
            g = self._gl
            if g is None:
                return
            w = max(self.winfo_width(), 1)
            h = max(self.winfo_height(), 1)
            bg = tuple(c / 255.0 for c in self._bg)
            if self._scene is None or self._cam is None:
                GL.glClearColor(bg[0], bg[1], bg[2], 1.0)
                GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                return
            if self._dirty:
                self._upload()
            cam = self._cam
            dist = cam["dist"] or 100.0
            target = cam["target"]
            elev = max(8.0, min(82.0, cam["elev"]))
            az = math.radians(cam["azim"])
            el = math.radians(elev)
            ce, se = math.cos(el), math.sin(el)
            eye = (target[0] + dist * ce * math.sin(az),
                   target[1] + dist * ce * math.cos(az),
                   target[2] + dist * se)
            # v15.49: orthographic, like a 3D slicer's view. Perspective
            # turned close-up flat tags into stretched cones aiming at
            # the center (near edge huge, far edge small); ortho keeps
            # every disc a disc and every word undistorted at any zoom.
            # The box matches the old perspective framing at the target
            # plane, so fit_distance is unchanged and wheel zoom (dist)
            # still scales the view.
            ph_h = dist * math.tan(math.radians(19.0))
            ph_w = ph_h * (w / float(h))
            proj = _ortho(-ph_w, ph_w, -ph_h, ph_h,
                            max(1.0, dist * 0.05), dist * 5.0 + 400.0)
            view = _look_at(eye, target)
            mvp = (proj @ view).astype(np.float32)
            fbo = self._ensure_fbo(w * _SS, h * _SS)
            if fbo is not None:
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo[0])
                GL.glViewport(0, 0, w * _SS, h * _SS)
            else:
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
                GL.glViewport(0, 0, w, h)
            GL.glClearColor(bg[0], bg[1], bg[2], 1.0)
            # v15.58: the depth mask MUST be on for this clear. The
            # selection passes leave it off (outline/ring draw without
            # writing depth), and glClear skips the depth buffer when
            # the mask is off — so every redraw after the first one
            # with a selection kept the previous frame's depths, and
            # the tags then failed the depth test against themselves
            # (equal depths lose under LESS) and vanished, leaving
            # only the ground, the shadows, and the selection outline.
            GL.glDepthMask(GL.GL_TRUE)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
            groups, footprints = self._scene
            # v15.58 look: a soft lavender sky gradient behind the
            # scene (light mode: pale violet top fading to the bg;
            # dark mode: deep violet top fading to the dark bg), then
            # the ground plane, a faint world grid, shadows, and the
            # selection ring. The gradient is a fullscreen quad, drawn
            # first with depth testing off.
            lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
            if lum > 0.5:
                sky_top = (203 / 255.0, 198 / 255.0, 240 / 255.0)
                grid_col = (170 / 255.0, 160 / 255.0, 195 / 255.0)
                grid_a = 0.55
            else:
                sky_top = (52 / 255.0, 46 / 255.0, 80 / 255.0)
                grid_col = (130 / 255.0, 120 / 255.0, 160 / 255.0)
                grid_a = 0.5
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDepthMask(GL.GL_FALSE)
            GL.glUseProgram(g["grad"])
            GL.glUniform3f(g["grad_top"], sky_top[0], sky_top[1],
                           sky_top[2])
            GL.glUniform3f(g["grad_bot"], bg[0], bg[1], bg[2])
            GL.glBindVertexArray(g["quad_vao"])
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
            # Ground plane sized from the camera, not the footprints: it
            # must fill the view like the web page's ground plane. (The
            # first version sized it from the footprints, which for a
            # single tag made a 20-unit quad the tag hid almost all of.)
            half_h = dist * math.tan(math.radians(19.0)) * 1.5
            half_w = half_h * (w / float(h))
            gx0, gx1 = target[0] - half_w, target[0] + half_w
            gy0, gy1 = target[1] - half_h, target[1] + half_h
            # Ground + grid + shadows + glow are coplanar at world z=0,
            # so they draw painter-style with depth testing AND depth
            # writes off (otherwise the ground's depth would hide the
            # shadows/glow at equal depth); the tags then depth-test
            # against a buffer only they wrote.
            GL.glUseProgram(g["flat"])
            # v15.49: GL_TRUE, not GL_FALSE. The mvp array is NumPy
            # row-major; GL_FALSE made GL read it column-major, i.e.
            # TRANSPOSED — the view matrix's translation leaked into the
            # perspective divide, which stretched close-up flat tags
            # into cones aiming at the center (the 15.47/15.48 cone
            # screenshots). GL_TRUE uploads the matrix as intended, so
            # the rendered pixels now match project_bounds exactly.
            GL.glUniformMatrix4fv(g["flat_mvp"], 1, GL.GL_TRUE, mvp)
            GL.glUniform1f(g["flat_radial"], 0.0)
            GL.glUniform1f(g["flat_ring"], 0.0)
            GL.glBindVertexArray(g["quad_vao"])
            ground = tuple(c / 255.0 for c in self._ground)
            GL.glUniform4f(g["flat_color"], ground[0], ground[1],
                           ground[2], 1.0)
            # Ground quad: local square [-1,1]^2 mapped to world rect.
            GL.glUniform2f(g["flat_tr"], (gx0 + gx1) / 2.0,
                           (gy0 + gy1) / 2.0)
            GL.glUniform2f(g["flat_sc"], (gx1 - gx0) / 2.0,
                           (gy1 - gy0) / 2.0)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            # Faint world grid on the floor (15 mm spacing — about a
            # third of a tag wide), in the flat shader's world-coords
            # mode (translate 0, scale 1).
            _step = 15.0
            _pts = []
            _x = math.ceil(gx0 / _step) * _step
            while _x <= gx1:
                _pts.extend((_x, gy0, _x, gy1))
                _x += _step
            _y = math.ceil(gy0 / _step) * _step
            while _y <= gy1:
                _pts.extend((gx0, _y, gx1, _y))
                _y += _step
            GL.glUniform4f(g["flat_color"], grid_col[0], grid_col[1],
                           grid_col[2], grid_a)
            GL.glUniform2f(g["flat_tr"], 0.0, 0.0)
            GL.glUniform2f(g["flat_sc"], 1.0, 1.0)
            GL.glBindVertexArray(g["grid_vao"])
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, g["grid_vbo"])
            _arr = np.array(_pts, dtype=np.float32)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, _arr.nbytes, _arr,
                            GL.GL_DYNAMIC_DRAW)
            GL.glDrawArrays(GL.GL_LINES, 0, len(_pts) // 2)
            GL.glBindVertexArray(g["quad_vao"])
            GL.glUniform1f(g["flat_radial"], 1.0)
            for word, (cx, cy, rx, ry) in footprints.items():
                if word == self._sel:
                    continue
                GL.glUniform4f(g["flat_color"], 0.0, 0.0, 0.0, 0.28)
                GL.glUniform2f(g["flat_tr"], cx, cy)
                GL.glUniform2f(g["flat_sc"], rx * 1.15, ry * 1.15)
                GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
            if self._sel and self._sel in footprints:
                cx, cy, rx, ry = footprints[self._sel]
                GL.glUniform4f(g["flat_color"], 249 / 255.0, 233 / 255.0,
                               168 / 255.0, 0.95)
                GL.glUniform2f(g["flat_tr"], cx, cy)
                GL.glUniform2f(g["flat_sc"], rx * 1.25, ry * 1.25)
                GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
                # NOTE: the v15.58 selection ring is NOT drawn here.
                # In multi-tag mode the bed plate (a lit span) paints
                # over anything on the ground plane, so a ground-level
                # ring would be invisible; it is drawn after the tags
                # instead (see below).
            GL.glDisable(GL.GL_BLEND)
            # Tags (lit shader): depth test + writes on, backfaces
            # culled, so raised ink over base resolves by real depth.
            GL.glDepthMask(GL.GL_TRUE)
            GL.glEnable(GL.GL_DEPTH_TEST)
            GL.glEnable(GL.GL_CULL_FACE)
            GL.glCullFace(GL.GL_BACK)
            GL.glUseProgram(g["lit"])
            GL.glUniformMatrix4fv(g["lit_mvp"], 1, GL.GL_TRUE, mvp)
            _nmat = np.ascontiguousarray(view[0:3, 0:3], dtype=np.float32)
            GL.glUniformMatrix3fv(g["lit_nmat"], 1, GL.GL_TRUE, _nmat)
            _sun = _nmat @ np.array(_LIGHT, dtype=np.float32)
            GL.glUniform3f(g["lit_sun"], float(_sun[0]),
                           float(_sun[1]), float(_sun[2]))
            GL.glBindVertexArray(g["vao"])
            GL.glUniform2f(g["lit_ctr"], 0.0, 0.0)
            GL.glUniform1f(g["lit_infl"], 1.0)
            for (off, cnt, color) in self._spans:
                GL.glUniform3f(g["lit_color"], color[0] / 255.0,
                               color[1] / 255.0, color[2] / 255.0)
                GL.glDrawArrays(GL.GL_TRIANGLES, off, cnt)
            # v15.58 selection, drawn AFTER the tags (a ground-level
            # ring loses to the opaque bed plate, which paints over
            # it):
            # - ring: a thick violet band just outside the footprint,
            #   drawn as an overlay (depth testing off) — safe because
            #   the band never overlaps the tag itself.
            # v15.66: the old second half of this block — the selected
            # word's whole mesh re-drawn inflated 6% in violet — is
            # GONE. Its comment claimed equal-depth fragments inside
            # the silhouette would fail, leaving only a rim; that only
            # holds for a perfectly top-down view. From the tilted
            # orbit view, inflating in XY changes each fragment's
            # depth, so the inflated ink (picture + word, raised above
            # the base) passed the depth test over the base face and
            # painted violet halos — the "crazy stringing" across the
            # selected tag. The ring alone marks selection clearly.
            if self._sel and self._sel in footprints:
                cx, cy, rx, ry = footprints[self._sel]
                GL.glUseProgram(g["flat"])
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
                # v15.58: the ring is an overlay — depth testing OFF.
                # With the test left on, the bed plate (z=0.05, above
                # the ring's z=0 plane) would hide it in multi-tag
                # mode; the band sits outside the footprint, so it
                # never paints over the tag itself.
                GL.glDisable(GL.GL_DEPTH_TEST)
                GL.glUniform1f(g["flat_radial"], 0.0)
                GL.glUniform1f(g["flat_ring"], 1.0)
                GL.glUniform4f(g["flat_color"], 138 / 255.0, 107 / 255.0,
                               192 / 255.0, 0.95)
                GL.glUniform2f(g["flat_tr"], cx, cy)
                GL.glUniform2f(g["flat_sc"], rx * 1.35, ry * 1.35)
                GL.glBindVertexArray(g["quad_vao"])
                GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
                GL.glUniform1f(g["flat_ring"], 0.0)
                GL.glDisable(GL.GL_BLEND)
                GL.glEnable(GL.GL_DEPTH_TEST)
            if fbo is not None:
                GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, fbo[0])
                GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, 0)
                GL.glBlitFramebuffer(0, 0, w * _SS, h * _SS,
                                     0, 0, w, h,
                                     GL.GL_COLOR_BUFFER_BIT, GL.GL_LINEAR)
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)

    try:
        return GLView(master)
    except Exception:
        return None
