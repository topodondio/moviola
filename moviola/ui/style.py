"""Tema scuro sobrio, adatto a valutare i colori (grigi neutri)."""

ACCENT = "#e8a33d"
QSS = f"""
* {{ font-size: 12px; }}
QMainWindow, QWidget {{ background: #1e1f21; color: #d9d9d9; }}
QDockWidget::title {{ background: #26272a; padding: 5px 8px; font-weight: 600; }}
QDockWidget {{ titlebar-close-icon: none; }}
QMenuBar {{ background: #1a1b1d; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: #3a3c40; }}
QMenu {{ background: #26272a; border: 1px solid #3a3c40; }}
QMenu::separator {{ height: 1px; background: #3a3c40; margin: 4px 6px; }}
QTreeWidget {{ background: #191a1c; border: none; alternate-background-color: #1d1e20; }}
QTreeWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:selected {{ background: #3b4a5e; color: white; }}
QHeaderView::section {{ background: #232427; color: #9a9a9a; border: none; padding: 4px; }}
QPushButton, QToolButton {{ background: #2d2f33; border: 1px solid #3a3c40; border-radius: 4px; padding: 4px 9px; }}
QPushButton:hover, QToolButton:hover {{ background: #383b40; }}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked, QPushButton:checked {{ background: #4a4d53; }}
QPushButton:disabled {{ color: #666; }}
QSlider::groove:horizontal {{ height: 4px; background: #3a3c40; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background: #d9d9d9; }}
QSlider::handle:horizontal:hover {{ background: white; }}
QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox {{ background: #151618; border: 1px solid #3a3c40;
  border-radius: 3px; padding: 2px 4px; selection-background-color: #3b4a5e; }}
QGroupBox {{ border: 1px solid #2f3134; border-radius: 5px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {ACCENT}; font-weight: 600; }}
QTabBar::tab {{ background: #26272a; padding: 4px 10px; border: 1px solid #333; }}
QTabBar::tab:selected {{ background: #3a3c40; }}
QTabWidget::pane {{ border: 1px solid #333; }}
QScrollArea {{ border: none; }}
QScrollBar:horizontal {{ height: 12px; background: #1a1b1d; }}
QScrollBar:vertical {{ width: 12px; background: #1a1b1d; }}
QScrollBar::handle {{ background: #44474c; border-radius: 4px; min-width: 24px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QProgressBar {{ border: 1px solid #3a3c40; border-radius: 3px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; }}
QRadioButton::indicator, QCheckBox::indicator {{ width: 13px; height: 13px; }}
QLabel#modeLabel {{ color: {ACCENT}; font-weight: 700; letter-spacing: 1px; }}
QLabel#timecode {{ font-family: Menlo, Consolas, monospace; font-size: 15px; color: #f0f0f0; }}
QStatusBar {{ background: #1a1b1d; color: #999; }}
QSplitter::handle {{ background: #141516; }}
"""
