"""Avvio dell'applicazione."""
from __future__ import annotations

import json
import os
import sys
import traceback


def _load_cfg(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cfg(path, cfg):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1)
    except Exception:
        pass


def main():
    missing = []
    for mod, pkg in (("PySide6", "PySide6"), ("av", "av"), ("numpy", "numpy")):
        try:
            __import__(mod)
        except Exception:
            missing.append(pkg)
    if missing:
        print("Mancano dei pacchetti: " + ", ".join(missing))
        print("Avvia il programma con il file 'avvia_mac.command' (Mac) o 'avvia_windows.bat' (Windows),")
        print("oppure installa con:  python -m pip install -r requirements.txt")
        sys.exit(1)

    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import paths
    from .ui.viewer import setup_default_format

    cfg_path = paths.config_path()
    cfg = _load_cfg(cfg_path)
    log_path = os.path.join(paths.cache_root(), "errori.log")

    if cfg.get("gpu", True):
        setup_default_format()
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Moviola")
    app.setOrganizationName("Moviola")
    if sys.platform == "darwin":
        app.setFont(QFont(".AppleSystemUIFont", 12))

    from .ui.style import QSS

    app.setStyleSheet(QSS)

    def excepthook(et, ev, tb):
        text = "".join(traceback.format_exception(et, ev, tb))
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass
        sys.__stderr__.write(text)
        try:
            QMessageBox.warning(None, "Errore inatteso",
                                f"Si è verificato un errore (il lavoro non è perso, salva il progetto).\n\n"
                                f"{ev}\n\nDettagli in:\n{log_path}")
        except Exception:
            pass

    sys.excepthook = excepthook

    from .ui.main_window import MainWindow

    win = MainWindow(cfg)
    try:
        if cfg.get("geometry"):
            win.restoreGeometry(QByteArray.fromHex(cfg["geometry"].encode()))
        if cfg.get("state"):
            win.restoreState(QByteArray.fromHex(cfg["state"].encode()))
    except Exception:
        pass
    win.show()
    args = [a for a in sys.argv[1:] if a.endswith(".moviola") and os.path.exists(a)]
    if args:
        win.open_project(args[0])
    else:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(150, win.show_welcome)
    code = app.exec()
    _save_cfg(cfg_path, cfg)
    sys.exit(code)


if __name__ == "__main__":
    main()
