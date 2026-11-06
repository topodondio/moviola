"""Visore: mostra i fotogrammi con la correzione colore.

GlViewer usa la scheda video (OpenGL): l'immagine e la LUT 3D vengono
caricate come texture e uno shader applica il colore con interpolazione
trilineare (stessa resa dell'export). Se OpenGL non è disponibile si usa
CpuViewer, che riceve fotogrammi già corretti dal motore.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QSurfaceFormat
from PySide6.QtWidgets import QWidget

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
except Exception:  # pragma: no cover
    QOpenGLWidget = None

BG = QColor(12, 12, 13)

VERT = """
#version 330 core
layout(location = 0) in vec2 pos;
uniform vec4 rect;
out vec2 v_uv;
void main() {
    v_uv = vec2(pos.x, 1.0 - pos.y);
    vec2 p = rect.xy + pos * rect.zw;
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

FRAG = """
#version 330 core
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D img;
uniform sampler3D lut;
uniform float lutSize;
uniform int useLut;
void main() {
    vec3 c = texture(img, v_uv).rgb;
    if (useLut == 1) {
        vec3 s = (c * (lutSize - 1.0) + 0.5) / lutSize;
        c = texture(lut, s).rgb;
    }
    outColor = vec4(c, 1.0);
}
"""


def setup_default_format():
    """Da chiamare prima di creare QApplication (necessario su macOS)."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(0)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)


def fit_rect(fw, fh, ww, wh, aspect):
    """Rettangolo (x, y, w, h) che contiene l'immagine con l'aspetto del progetto."""
    if ww <= 0 or wh <= 0:
        return 0, 0, 0, 0
    a = aspect or (fw / max(fh, 1))
    if ww / wh > a:
        h = wh
        w = h * a
    else:
        w = ww
        h = w / a
    return (ww - w) / 2, (wh - h) / 2, w, h


class _Base:
    def _frame_rect(self, ww, wh):
        # l'immagine dentro al formato del progetto (bande nere se diverso)
        x, y, w, h = fit_rect(1, 1, ww, wh, self.aspect)
        if self._img is None:
            return x, y, w, h
        fh, fw = self._img.shape[:2]
        fa = fw / max(fh, 1)
        ix, iy, iw, ih = fit_rect(fw, fh, w, h, fa)
        return x + ix, y + iy, iw, ih


if QOpenGLWidget is not None:

    class GlViewer(QOpenGLWidget, _Base):
        failed = Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.aspect = 16 / 9
            self._img = None
            self._img_dirty = False
            self._lut = None
            self._lut_key = "identity"
            self._lut_dirty = False
            self._ok = False
            self._tex_size = None
            self.setMinimumSize(320, 180)

        def initializeGL(self):
            try:
                from OpenGL import GL

                self.GL = GL
                vs = GL.glCreateShader(GL.GL_VERTEX_SHADER)
                GL.glShaderSource(vs, VERT)
                GL.glCompileShader(vs)
                if not GL.glGetShaderiv(vs, GL.GL_COMPILE_STATUS):
                    raise RuntimeError(GL.glGetShaderInfoLog(vs))
                fs = GL.glCreateShader(GL.GL_FRAGMENT_SHADER)
                GL.glShaderSource(fs, FRAG)
                GL.glCompileShader(fs)
                if not GL.glGetShaderiv(fs, GL.GL_COMPILE_STATUS):
                    raise RuntimeError(GL.glGetShaderInfoLog(fs))
                prog = GL.glCreateProgram()
                GL.glAttachShader(prog, vs)
                GL.glAttachShader(prog, fs)
                GL.glLinkProgram(prog)
                if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
                    raise RuntimeError(GL.glGetProgramInfoLog(prog))
                self.prog = prog
                self.vao = GL.glGenVertexArrays(1)
                GL.glBindVertexArray(self.vao)
                quad = np.array([0, 0, 1, 0, 0, 1, 1, 1], dtype=np.float32)
                self.vbo = GL.glGenBuffers(1)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
                GL.glBufferData(GL.GL_ARRAY_BUFFER, quad.nbytes, quad, GL.GL_STATIC_DRAW)
                GL.glEnableVertexAttribArray(0)
                GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
                GL.glBindVertexArray(0)
                self.tex = GL.glGenTextures(1)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
                for p, v in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                             (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
                    GL.glTexParameteri(GL.GL_TEXTURE_2D, p, v)
                self.lut_tex = GL.glGenTextures(1)
                GL.glBindTexture(GL.GL_TEXTURE_3D, self.lut_tex)
                for p, v in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                             (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE),
                             (GL.GL_TEXTURE_WRAP_R, GL.GL_CLAMP_TO_EDGE)):
                    GL.glTexParameteri(GL.GL_TEXTURE_3D, p, v)
                self.u_rect = GL.glGetUniformLocation(prog, "rect")
                self.u_img = GL.glGetUniformLocation(prog, "img")
                self.u_lut = GL.glGetUniformLocation(prog, "lut")
                self.u_size = GL.glGetUniformLocation(prog, "lutSize")
                self.u_use = GL.glGetUniformLocation(prog, "useLut")
                self._ok = True
                self._img_dirty = self._img is not None
                self._lut_dirty = self._lut is not None
                self._tex_size = None
            except Exception as e:  # scheda video non adatta: si passa alla CPU
                self._ok = False
                self.failed.emit(str(e))

        def set_frame(self, frame):
            self._img = frame.img
            self._img_dirty = frame.img is not None
            if frame.lut_key != self._lut_key:
                self._lut = frame.lut
                self._lut_key = frame.lut_key
                self._lut_dirty = frame.lut is not None
            self.update()

        def clear(self):
            self._img = None
            self.update()

        def paintGL(self):
            if not self._ok:
                return
            GL = self.GL
            try:
                dpr = self.devicePixelRatioF()
                ww, wh = int(self.width() * dpr), int(self.height() * dpr)
                GL.glViewport(0, 0, ww, wh)
                GL.glClearColor(BG.redF(), BG.greenF(), BG.blueF(), 1.0)
                GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                # cornice del formato progetto (nera) anche senza immagine
                x, y, w, h = fit_rect(1, 1, ww, wh, self.aspect)
                GL.glEnable(GL.GL_SCISSOR_TEST)
                GL.glScissor(int(x), int(y), int(w), int(h))
                GL.glClearColor(0, 0, 0, 1)
                GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                GL.glDisable(GL.GL_SCISSOR_TEST)
                if self._img is None:
                    return
                GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
                if self._img_dirty:
                    img = np.ascontiguousarray(self._img)
                    ih, iw = img.shape[:2]
                    if self._tex_size != (iw, ih):
                        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGB8, iw, ih, 0, GL.GL_RGB,
                                        GL.GL_UNSIGNED_BYTE, img)
                        self._tex_size = (iw, ih)
                    else:
                        GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, iw, ih, GL.GL_RGB, GL.GL_UNSIGNED_BYTE, img)
                    self._img_dirty = False
                GL.glActiveTexture(GL.GL_TEXTURE1)
                GL.glBindTexture(GL.GL_TEXTURE_3D, self.lut_tex)
                use = 1 if (self._lut is not None and self._lut_key != "identity") else 0
                if use and self._lut_dirty:
                    lut = np.ascontiguousarray(self._lut, dtype=np.float32)
                    n = lut.shape[0]
                    GL.glTexImage3D(GL.GL_TEXTURE_3D, 0, GL.GL_RGB16F, n, n, n, 0, GL.GL_RGB, GL.GL_FLOAT, lut)
                    self._lut_dirty = False
                GL.glUseProgram(self.prog)
                GL.glUniform1i(self.u_img, 0)
                GL.glUniform1i(self.u_lut, 1)
                GL.glUniform1f(self.u_size, float(self._lut.shape[0]) if use else 33.0)
                GL.glUniform1i(self.u_use, use)
                fx, fy, fw, fh = self._frame_rect(ww, wh)
                GL.glUniform4f(self.u_rect, fx / ww * 2 - 1, 1 - (fy + fh) / wh * 2, fw / ww * 2, fh / wh * 2)
                GL.glBindVertexArray(self.vao)
                GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
                GL.glBindVertexArray(0)
                GL.glUseProgram(0)
                GL.glActiveTexture(GL.GL_TEXTURE0)
            except Exception as e:
                self._ok = False
                self.failed.emit(str(e))
else:
    GlViewer = None


class CpuViewer(QWidget, _Base):
    """Ripiego senza scheda video: disegna con QPainter."""

    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.aspect = 16 / 9
        self._img = None
        self._qimg = None
        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def set_frame(self, frame):
        self._img = frame.img
        if frame.img is None:
            self._qimg = None
        else:
            arr = np.ascontiguousarray(frame.img)
            h, w = arr.shape[:2]
            self._arr = arr  # QImage non copia i dati: li teniamo vivi
            self._qimg = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self.update()

    def clear(self):
        self._img = None
        self._qimg = None
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        x, y, w, h = fit_rect(1, 1, self.width(), self.height(), self.aspect)
        p.fillRect(int(x), int(y), int(w), int(h), Qt.black)
        if self._qimg is not None:
            fx, fy, fw, fh = self._frame_rect(self.width(), self.height())
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            from PySide6.QtCore import QRectF

            p.drawImage(QRectF(fx, fy, fw, fh), self._qimg)
        p.end()
