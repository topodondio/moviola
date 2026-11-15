"""La rotellina non modifica cursori, caselle numeriche e menu a tendina: i valori
cambiano solo con clic e trascinamento (o scrivendo). Sopra questi controlli la
rotellina fa scorrere il pannello che li contiene."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QScrollBar

_BLOCKED = (QAbstractSlider, QAbstractSpinBox, QComboBox)


class NoWheelFilter(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Wheel and isinstance(obj, _BLOCKED) and not isinstance(obj, (QAbstractScrollArea, QScrollBar)):
            parent = obj.parentWidget()
            while parent is not None and not isinstance(parent, QAbstractScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                QApplication.sendEvent(parent.viewport(), QWheelEvent(ev))
            return True
        return False


def install(app):
    f = NoWheelFilter(app)
    app.installEventFilter(f)
    app._nowheel = f
    return f
