"""Visore: mostra i fotogrammi con la correzione colore.

GlViewer usa la scheda video (OpenGL): l'immagine e la LUT 3D vengono
caricate come texture e uno shader applica il colore con interpolazione
trilineare (stessa resa dell'export). Se OpenGL non è disponibile si usa
CpuViewer, che riceve fotogrammi già corretti dal motore.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QSurfaceFormat
from PySide6.QtWidgets import QWidget

from .guides import draw_guides
from .text_render import draw_overlay, text_box

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
uniform float fadeAmt;
uniform vec3 fadeColor;
uniform float alpha;
void main() {
    vec3 c = texture(img, v_uv).rgb;
    if (useLut == 1) {
        vec3 s = (c * (lutSize - 1.0) + 0.5) / lutSize;
        c = texture(lut, s).rgb;
    }
    c = mix(c, fadeColor, fadeAmt);
    outColor = vec4(c, alpha);
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


class TextOverlay(QWidget):
    """Strato trasparente sopra al visore: disegna le scritte e permette di
    spostare scritte e inquadratura trascinando."""

    drag_begin = Signal(float, float)  # punto (frazione del fotogramma)
    drag_move = Signal(float, float)  # spostamento dall'inizio (frazione)
    drag_end = Signal()

    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.texts = []
        self.bars = 0.0
        self.highlight = None  # id della scritta selezionata
        self.guides = None     # guide di composizione e maschera di ritaglio (solo da guardare)
        self.ref_img = None    # QImage del riferimento per il confronto colore
        self.ref_name = ""
        self.cmp = None        # None, "side" (affiancato) o "wipe" (tendina)
        self.wipe = 0.5
        self.flash = False     # lampeggio: mostra il riferimento finché si tiene premuto il tasto
        self.parade_img = None  # QImage della parade sovrapposta (se richiesta)
        self.parade_title = ""
        self._press = None
        self._wiping = False
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(False)

    def project_rect(self):
        x, y, w, h = self.viewer._project_rect(self.width(), self.height())
        return QRectF(x, y, w, h)

    def set_compare(self, img=None, name="", mode=None):
        self.ref_img, self.ref_name, self.cmp = img, name, (mode if img is not None else None)
        self.viewer.side = self.cmp == "side"
        self.viewer.update()
        self.update()

    def _ref_rect(self, fr):
        """Dove sta il riferimento: affiancato nella metà sinistra, altrimenti sopra al fotogramma."""
        if self.cmp == "side":
            x, y, w, h = fit_rect(1, 1, self.width() / 2, self.height(), self.viewer.aspect)
            return QRectF(x, y, w, h)
        return fr

    def _draw_compare(self, p, fr):
        if self.ref_img is None or not (self.cmp or self.flash):
            return
        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rr = self._ref_rect(fr)
        if self.flash:                          # riferimento a tutto fotogramma finché il tasto è premuto
            p.drawImage(fr, self.ref_img)
            self._tag(p, fr, "RIFERIMENTO · " + self.ref_name)
        elif self.cmp == "side":
            p.fillRect(QRectF(0, 0, self.width() / 2, self.height()), QColor(20, 21, 23))
            p.drawImage(rr, self.ref_img)
            self._tag(p, rr, "RIFERIMENTO · " + self.ref_name)
            self._tag(p, fr, "CLIP")
        elif self.cmp == "wipe":
            x = fr.left() + self.wipe * fr.width()
            p.setClipRect(QRectF(fr.left(), fr.top(), x - fr.left(), fr.height()))
            p.drawImage(fr, self.ref_img)
            p.setClipping(False)
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.5))
            p.drawLine(QPointF(x, fr.top()), QPointF(x, fr.bottom()))
            p.setBrush(QColor(232, 163, 61))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x, fr.center().y()), 9, 9)
            p.setPen(QColor(27, 27, 27))
            p.drawText(QRectF(x - 9, fr.center().y() - 9, 18, 18), Qt.AlignCenter, "↔")
            self._tag(p, QRectF(fr.left(), fr.top(), max(0.0, x - fr.left()), fr.height()),
                      "RIFERIMENTO · " + self.ref_name)
        p.restore()

    def _tag(self, p, r, text):
        p.save()
        p.setClipRect(r)
        f = QFont(p.font())
        f.setPointSizeF(max(8.0, f.pointSizeF()))
        f.setBold(True)
        p.setFont(f)
        w = min(r.width() - 12, p.fontMetrics().horizontalAdvance(text) + 14)
        if w > 20:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(QRectF(r.left() + 6, r.top() + 6, w, 20), 3, 3)
            p.setPen(QColor(240, 240, 240))
            p.drawText(QRectF(r.left() + 12, r.top() + 6, w - 8, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.ElideRight, int(w - 10)))
        p.restore()

    def _draw_parade(self, p, fr):
        if self.parade_img is None:
            return
        h = max(60.0, fr.height() * 0.38)
        r = QRectF(fr.left() + 8, fr.bottom() - h - 8, fr.width() - 16, h)
        p.save()
        p.setOpacity(0.78)
        p.fillRect(r, QColor(8, 8, 9))
        p.setOpacity(0.9)
        p.drawImage(r, self.parade_img)
        p.setOpacity(1.0)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        for k in (0.25, 0.5, 0.75):
            y = r.top() + k * r.height()
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.restore()

    def set_guides(self, g):
        self.guides = g
        self.update()

    def paintEvent(self, e):
        has_g = bool(self.guides and (self.guides.get("grids") or self.guides.get("crop")))
        extra = self.ref_img is not None and bool(self.cmp or self.flash) or self.parade_img is not None
        if not self.texts and self.bars <= 0 and not has_g and not extra:
            return
        p = QPainter(self)
        fr = self.project_rect()
        p.setClipRect(fr)
        if self.bars > 0:
            bh = round(min(0.49, self.bars) * fr.height())
            p.fillRect(QRectF(fr.left(), fr.top(), fr.width(), bh), Qt.black)
            p.fillRect(QRectF(fr.left(), fr.bottom() - bh, fr.width(), bh), Qt.black)
        for item, op in self.texts:
            draw_overlay(p, fr, item, op)
            if item.get("id") == self.highlight:
                p.setOpacity(1.0)
                p.setPen(QPen(QColor(232, 163, 61), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(text_box(item, fr))
        p.setClipping(False)
        p.setOpacity(1.0)
        self._draw_compare(p, fr)
        if has_g:
            draw_guides(p, fr, self.guides)
        self._draw_parade(p, fr)
        p.end()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        fr = self.project_rect()
        if fr.width() <= 0:
            return
        if self.cmp == "wipe" and self.ref_img is not None:      # tendina: si trascina la linea
            self._wiping = True
            self._set_wipe(e.position().x(), fr)
            return
        pos = e.position()
        self._press = pos
        self.drag_begin.emit((pos.x() - fr.left()) / fr.width(), (pos.y() - fr.top()) / fr.height())

    def _set_wipe(self, x, fr):
        self.wipe = max(0.02, min(0.98, (x - fr.left()) / max(fr.width(), 1)))
        self.update()

    def mouseMoveEvent(self, e):
        if self._wiping:
            self._set_wipe(e.position().x(), self.project_rect())
            return
        if self._press is None:
            return
        fr = self.project_rect()
        d = e.position() - self._press
        self.drag_move.emit(d.x() / fr.width(), d.y() / fr.height())

    def mouseReleaseEvent(self, e):
        if self._wiping:
            self._wiping = False
            return
        if self._press is not None:
            self._press = None
            self.drag_end.emit()


class _Layer:
    """Un'immagine da disegnare: con le transizioni ce ne sono due."""

    __slots__ = ("img", "lut", "lut_key", "xf", "alpha", "clip", "dx")

    def __init__(self, img, lut=None, lut_key="identity", xf=(1.0, 0.0, 0.0), alpha=1.0, clip=None, dx=0.0):
        self.img = img
        self.lut = lut
        self.lut_key = lut_key
        self.xf = xf
        self.alpha = alpha
        self.clip = clip  # (x0, x1) in frazioni della larghezza del fotogramma
        self.dx = dx  # spostamento orizzontale (frazione della larghezza)


def frame_layers(frame):
    if getattr(frame, "layers", None):
        return [_Layer(**ly) for ly in frame.layers]
    if frame.img is None:
        return []
    return [_Layer(frame.img, frame.lut, frame.lut_key, frame.xf)]


class _Base:
    def _init_base(self):
        self.aspect = 16 / 9
        self.side = False          # confronto affiancato: il fotogramma sta nella metà destra
        self._layers = []
        self._fade = 0.0
        self._fade_color = (0.0, 0.0, 0.0)
        self.overlay = TextOverlay(self)

    def _base_frame(self, frame):
        self._layers = frame_layers(frame)
        self._fade = frame.fade
        self._fade_color = frame.fade_color
        self.overlay.texts = frame.texts
        self.overlay.bars = max(frame.bars) if frame.bars else 0.0
        self.overlay.update()

    def _project_rect(self, ww, wh):
        if self.side:
            x, y, w, h = fit_rect(1, 1, ww / 2, wh, self.aspect)
            return x + ww / 2, y, w, h
        return fit_rect(1, 1, ww, wh, self.aspect)

    def graded_image(self, max_w=0):
        """Il fotogramma com'è mostrato (con la correzione applicata) come array uint8 HxWx3,
        eventualmente ridotto a max_w colonne; None se non c'è nulla da vedere."""
        if not self._layers:
            return None
        ly = self._layers[0]
        img = ly.img
        if max_w and img.shape[1] > max_w:
            st = int(np.ceil(img.shape[1] / max_w))
            img = img[::st, ::st]
        img = np.ascontiguousarray(img)
        if ly.lut is not None and ly.lut_key != "identity":
            from .. import color
            img = color.apply_lut_image(img, ly.lut)
        return img

    def _layer_rect(self, ly, ww, wh):
        """Rettangolo dell'immagine: adattata al formato del progetto, poi
        zoom e spostamento del pezzo (e scorrimento delle transizioni)."""
        x, y, w, h = self._project_rect(ww, wh)
        fh, fw = ly.img.shape[:2]
        ix, iy, iw, ih = fit_rect(fw, fh, w, h, fw / max(fh, 1))
        zoom, px, py = ly.xf
        iw2, ih2 = iw * zoom, ih * zoom
        cx = x + w / 2 + (px + ly.dx) * w
        cy = y + h / 2 + py * h
        return cx - iw2 / 2, cy - ih2 / 2, iw2, ih2

    def _layer_clip(self, ly, ww, wh):
        x, y, w, h = self._project_rect(ww, wh)
        if ly.clip is None:
            return x, y, w, h
        x0, x1 = ly.clip
        return x + x0 * w, y, max(0.0, (x1 - x0) * w), h

    def _bg_color(self):
        """Colore delle bande (nero che sfuma verso il colore della dissolvenza)."""
        a = self._fade
        return tuple(c * a for c in self._fade_color)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.overlay.setGeometry(self.rect())


if QOpenGLWidget is not None:

    class GlViewer(_Base, QOpenGLWidget):
        failed = Signal(str)
        SLOTS = 2

        def __init__(self, parent=None):
            super().__init__(parent)
            self._init_base()
            self._ok = False
            self._slots = [{"tex": None, "lut_tex": None, "size": None, "lut_key": None, "img_id": None}
                           for _ in range(self.SLOTS)]
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
                for sl in self._slots:
                    sl["tex"] = GL.glGenTextures(1)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, sl["tex"])
                    for p, v in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR),
                                 (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                                 (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE),
                                 (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
                        GL.glTexParameteri(GL.GL_TEXTURE_2D, p, v)
                    sl["lut_tex"] = GL.glGenTextures(1)
                    GL.glBindTexture(GL.GL_TEXTURE_3D, sl["lut_tex"])
                    for p, v in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR),
                                 (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                                 (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE),
                                 (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE),
                                 (GL.GL_TEXTURE_WRAP_R, GL.GL_CLAMP_TO_EDGE)):
                        GL.glTexParameteri(GL.GL_TEXTURE_3D, p, v)
                    sl["size"] = None
                    sl["lut_key"] = None
                    sl["img_id"] = None
                self.u_rect = GL.glGetUniformLocation(prog, "rect")
                self.u_img = GL.glGetUniformLocation(prog, "img")
                self.u_lut = GL.glGetUniformLocation(prog, "lut")
                self.u_size = GL.glGetUniformLocation(prog, "lutSize")
                self.u_use = GL.glGetUniformLocation(prog, "useLut")
                self.u_fade = GL.glGetUniformLocation(prog, "fadeAmt")
                self.u_fcol = GL.glGetUniformLocation(prog, "fadeColor")
                self.u_alpha = GL.glGetUniformLocation(prog, "alpha")
                self._ok = True
            except Exception as e:  # scheda video non adatta: si passa alla CPU
                self._ok = False
                self.failed.emit(str(e))

        def set_frame(self, frame):
            self._base_frame(frame)
            self.update()

        def clear(self):
            self._layers = []
            self.update()

        def _upload(self, sl, ly):
            GL = self.GL
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, sl["tex"])
            if sl["img_id"] is not ly.img:
                img = np.ascontiguousarray(ly.img)
                ih, iw = img.shape[:2]
                if sl["size"] != (iw, ih):
                    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGB8, iw, ih, 0, GL.GL_RGB, GL.GL_UNSIGNED_BYTE, img)
                    sl["size"] = (iw, ih)
                else:
                    GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, iw, ih, GL.GL_RGB, GL.GL_UNSIGNED_BYTE, img)
                sl["img_id"] = ly.img
            GL.glActiveTexture(GL.GL_TEXTURE1)
            GL.glBindTexture(GL.GL_TEXTURE_3D, sl["lut_tex"])
            use = 1 if (ly.lut is not None and ly.lut_key != "identity") else 0
            if use and sl["lut_key"] != ly.lut_key:
                lut = np.ascontiguousarray(ly.lut, dtype=np.float32)
                n = lut.shape[0]
                GL.glTexImage3D(GL.GL_TEXTURE_3D, 0, GL.GL_RGB16F, n, n, n, 0, GL.GL_RGB, GL.GL_FLOAT, lut)
                sl["lut_key"] = ly.lut_key
            return use

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
                x, y, w, h = self._project_rect(ww, wh)
                GL.glEnable(GL.GL_SCISSOR_TEST)
                GL.glScissor(int(x), int(y), int(w), int(h))
                bg = self._bg_color() if self._layers else (0.0, 0.0, 0.0)
                GL.glClearColor(bg[0], bg[1], bg[2], 1)
                GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                if not self._layers:
                    GL.glDisable(GL.GL_SCISSOR_TEST)
                    return
                GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
                GL.glUseProgram(self.prog)
                GL.glUniform1i(self.u_img, 0)
                GL.glUniform1i(self.u_lut, 1)
                GL.glUniform1f(self.u_fade, float(self._fade))
                GL.glUniform3f(self.u_fcol, *[float(c) for c in self._fade_color])
                GL.glBindVertexArray(self.vao)
                for k, ly in enumerate(self._layers[:self.SLOTS]):
                    use = self._upload(self._slots[k], ly)
                    GL.glUniform1f(self.u_size, float(ly.lut.shape[0]) if use else 33.0)
                    GL.glUniform1i(self.u_use, use)
                    GL.glUniform1f(self.u_alpha, float(ly.alpha))
                    cx, cy, cw, ch = self._layer_clip(ly, ww, wh)
                    GL.glScissor(int(round(cx)), int(y), int(round(cw)), int(h))
                    fx, fy, fw, fh = self._layer_rect(ly, ww, wh)
                    GL.glUniform4f(self.u_rect, fx / ww * 2 - 1, 1 - (fy + fh) / wh * 2, fw / ww * 2, fh / wh * 2)
                    GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
                GL.glDisable(GL.GL_SCISSOR_TEST)
                GL.glDisable(GL.GL_BLEND)
                GL.glBindVertexArray(0)
                GL.glUseProgram(0)
                GL.glActiveTexture(GL.GL_TEXTURE0)
            except Exception as e:
                self._ok = False
                try:
                    self.GL.glDisable(self.GL.GL_SCISSOR_TEST)
                except Exception:
                    pass
                self.failed.emit(str(e))
else:
    GlViewer = None


class CpuViewer(_Base, QWidget):
    """Ripiego senza scheda video: disegna con QPainter (le immagini arrivano
    già corrette dal motore)."""

    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_base()
        self._qimgs = []
        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def set_frame(self, frame):
        self._base_frame(frame)
        self._qimgs = []
        for ly in self._layers:
            arr = np.ascontiguousarray(ly.img)
            h, w = arr.shape[:2]
            # QImage non copia i dati: l'array resta vivo nel livello
            ly.img = arr
            self._qimgs.append(QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888))
        self.update()

    def clear(self):
        self._layers = []
        self._qimgs = []
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        x, y, w, h = self._project_rect(self.width(), self.height())
        bg = self._bg_color() if self._layers else (0.0, 0.0, 0.0)
        p.fillRect(QRectF(x, y, w, h), QColor.fromRgbF(*bg))
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for ly, qimg in zip(self._layers, self._qimgs):
            cx, cy, cw, ch = self._layer_clip(ly, self.width(), self.height())
            p.setClipRect(QRectF(cx, cy, cw, ch))
            p.setOpacity(max(0.0, min(1.0, ly.alpha)))
            fx, fy, fw, fh = self._layer_rect(ly, self.width(), self.height())
            p.drawImage(QRectF(fx, fy, fw, fh), qimg)
        p.setOpacity(1.0)
        if self._fade > 0.001 and self._layers:  # dissolvenze da/verso nero o bianco
            p.setClipRect(QRectF(x, y, w, h))
            c = QColor.fromRgbF(*self._fade_color)
            c.setAlphaF(max(0.0, min(1.0, self._fade)))
            p.fillRect(QRectF(x, y, w, h), c)
        p.end()
