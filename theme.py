# -*- coding: utf-8 -*-
"""
نظام الألوان: وضع (فاتح / داكن) + لون رئيسي + لون ثانوي.

كل ألوان الواجهة تُشتق من هذه الثلاثة عبر دالة واحدة (build_stylesheet)، فلا حاجة لثيمات جاهزة.
- اللون الرئيسي: الأزرار، التبويب المحدد، إطار الحقول عند التركيز، التوهج.
- اللون الثانوي: عناوين الأقسام، التحديد في الجداول، شريط التقدم، أزرار الإجراء الثانوي (التخريج).
"""
import os
import tempfile
from typing import Dict

DEFAULT_MODE = "dark"
DEFAULT_PRIMARY = "#3b82f6"
DEFAULT_SECONDARY = "#14b8a6"

# لوحة ألوان جاهزة (6 صفوف × 8 أعمدة) للاختيار السريع
PRESET_COLORS = [
    "#ef4444", "#f97316", "#f59e0b", "#eab308", "#84cc16", "#22c55e", "#10b981", "#14b8a6",
    "#06b6d4", "#0ea5e9", "#3b82f6", "#6366f1", "#8b5cf6", "#a855f7", "#d946ef", "#ec4899",
    "#dc2626", "#ea580c", "#d97706", "#ca8a04", "#65a30d", "#16a34a", "#059669", "#0d9488",
    "#0891b2", "#0284c7", "#2563eb", "#4f46e5", "#7c3aed", "#9333ea", "#c026d3", "#db2777",
    "#b91c1c", "#c2410c", "#b45309", "#a16207", "#4d7c0f", "#15803d", "#047857", "#0f766e",
    "#0e7490", "#0369a1", "#1d4ed8", "#4338ca", "#6d28d9", "#7e22ce", "#a21caf", "#be185d",
    "#64748b", "#475569", "#334155", "#1e293b", "#78716c", "#57534e", "#44403c", "#292524",
]

_BASES = {
    "dark": {
        "bg": "#16171b", "surface": "#1d1f24", "input": "#24262c", "border": "#353840",
        "text": "#e8e9ec", "dim": "#b4b7be", "muted": "#7d818b", "canvas": "#121316",
        "disabled": "#2d2f36", "handle": "#454851", "handle_hover": "#5a5e69",
    },
    "light": {
        "bg": "#f5f6f8", "surface": "#ffffff", "input": "#eceef2", "border": "#d3d7de",
        "text": "#1c1e23", "dim": "#4a4f5a", "muted": "#8a8f9a", "canvas": "#ffffff",
        "disabled": "#dfe2e8", "handle": "#c2c6ce", "handle_hover": "#a8adb7",
    },
}

DANGER = "#d64545"


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(c))):02x}" for c in rgb)


def adjust(hex_color: str, amount: int) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex((r + amount, g + amount, b + amount))


def luminance(hex_color: str) -> float:
    r, g, b = _hex_to_rgb(hex_color)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def on_color(hex_color: str) -> str:
    """لون نص مقروء فوق لون معيّن."""
    return "#111418" if luminance(hex_color) > 0.62 else "#ffffff"


def rgba(hex_color: str, alpha: int) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha})"


def make_arrow_icon(color: str, name: str = "arrow") -> str:
    """يولّد أيقونة سهم صغيرة (PNG) بلون معيّن ويعيد مسارها (بصيغة تصلح داخل stylesheet)."""
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QPixmap, QPainter, QPolygonF, QColor

    folder = os.path.join(tempfile.gettempdir(), "lpb_icons")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}_{color.lstrip('#')}.png")
    if not os.path.exists(path):
        pm = QPixmap(14, 14)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawPolygon(QPolygonF([QPointF(3, 5), QPointF(11, 5), QPointF(7, 10)]))
        p.end()
        pm.save(path, "PNG")
    return path.replace("\\", "/")


def palette_for(mode: str, primary: str, secondary: str) -> Dict[str, str]:
    base = dict(_BASES["light" if mode == "light" else "dark"])
    base.update({
        "mode": "light" if mode == "light" else "dark",
        "primary": primary,
        "secondary": secondary,
        "on_primary": on_color(primary),
        "on_secondary": on_color(secondary),
        "primary_hover": adjust(primary, 22 if mode != "light" else -18),
        "secondary_hover": adjust(secondary, 22 if mode != "light" else -18),
        "danger": DANGER,
        "danger_hover": adjust(DANGER, 25),
        "selection": rgba(secondary, 70),
    })
    return base


def build_stylesheet(mode: str = DEFAULT_MODE, primary: str = DEFAULT_PRIMARY,
                     secondary: str = DEFAULT_SECONDARY) -> str:
    c = palette_for(mode, primary, secondary)
    arrow = make_arrow_icon(c["dim"])
    return f"""
* {{
    font-family: "Segoe UI", "Tahoma", "Noto Sans Arabic", "DejaVu Sans", sans-serif;
    font-size: 10pt;
}}
QMainWindow, QDialog, QMessageBox, QWidget {{
    background-color: {c['bg']};
    color: {c['text']};
}}
QScrollArea {{ border: none; background: {c['bg']}; }}
QLabel {{ background: transparent; color: {c['dim']}; }}
QLabel#hint {{ color: {c['muted']}; }}
QLabel#status {{ color: {c['dim']}; }}

/* ---------- التبويبات ---------- */
QTabWidget::pane {{ border: none; border-top: 1px solid {c['border']}; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; color: {c['dim']};
    padding: 9px 18px; margin: 0 2px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{
    color: {c['primary']}; border-bottom: 2px solid {c['primary']}; font-weight: bold;
}}

/* ---------- الأزرار ---------- */
QPushButton {{
    background-color: {c['primary']}; color: {c['on_primary']};
    border: none; border-radius: 5px;
    padding: 3px 10px; min-height: 26px;
}}
QPushButton:hover {{ background-color: {c['primary_hover']}; }}
QPushButton:pressed {{ background-color: {c['primary']}; }}
QPushButton:disabled {{ background-color: {c['disabled']}; color: {c['muted']}; }}
QPushButton#secondaryButton {{ background-color: {c['secondary']}; color: {c['on_secondary']}; }}
QPushButton#secondaryButton:hover {{ background-color: {c['secondary_hover']}; }}
QPushButton#successButton {{ background-color: {c['secondary']}; color: {c['on_secondary']}; }}
QPushButton#successButton:hover {{ background-color: {c['secondary_hover']}; }}
QPushButton#dangerButton {{ background-color: {c['danger']}; color: #ffffff; }}
QPushButton#dangerButton:hover {{ background-color: {c['danger_hover']}; }}
QPushButton#secondaryButton:disabled, QPushButton#successButton:disabled,
QPushButton#dangerButton:disabled {{ background-color: {c['disabled']}; color: {c['muted']}; }}

/* رأس القسم القابل للطي */
QPushButton#sectionHeader {{
    background: transparent; color: {c['secondary']};
    border: none; border-bottom: 1px solid {c['border']}; border-radius: 0;
    padding: 6px 2px; font-weight: bold; text-align: left; min-height: 22px;
}}
QPushButton#sectionHeader:hover {{ background: transparent; color: {c['secondary_hover']}; }}

QToolButton {{ background: transparent; color: {c['muted']}; border: none; padding: 2px; }}
QToolButton:hover {{ color: {c['primary']}; }}
QToolButton#swatch {{ border: 1px solid {c['border']}; border-radius: 4px; padding: 0; }}

/* ---------- الحقول: بلا خلفية، إطار سفلي فقط ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QComboBox {{
    background-color: transparent; color: {c['text']};
    border: none; border-bottom: 1px solid {c['border']}; border-radius: 0;
    padding: 2px 6px; min-height: 26px;
    selection-background-color: {c['primary']}; selection-color: {c['on_primary']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus,
QTimeEdit:focus, QComboBox:focus {{
    border-bottom: 2px solid {c['primary']};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {c['muted']};
}}
QAbstractSpinBox QLineEdit {{ border: none; background: transparent; min-height: 0; padding: 0; }}
QAbstractItemView QLineEdit, QAbstractItemView QSpinBox, QAbstractItemView QDoubleSpinBox {{
    background-color: {c['surface']}; border-bottom: 1px solid {c['primary']};
}}
QListWidget#fileList {{
    background-color: transparent; border: none; border-bottom: 1px solid {c['border']};
    border-radius: 0; padding: 0;
}}
QListWidget#fileList:focus {{ border-bottom: 2px solid {c['primary']}; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url({arrow}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView, QCalendarWidget QAbstractItemView {{
    background-color: {c['surface']}; color: {c['text']};
    border: 1px solid {c['border']};
    selection-background-color: {c['primary']}; selection-color: {c['on_primary']};
    outline: none;
}}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background-color: {c['surface']}; }}
QCalendarWidget QToolButton {{ color: {c['text']}; padding: 4px 8px; }}

/* ---------- الجداول والقوائم والسجل: بلا إطار ---------- */
QTableWidget, QTableView, QListWidget, QTextEdit, QTextBrowser {{
    background-color: {c['surface']}; color: {c['text']};
    border: none; border-radius: 4px;
    gridline-color: {c['border']};
    selection-background-color: {c['selection']}; selection-color: {c['text']};
    outline: none;
}}
QTextEdit {{ color: {c['dim']}; }}
QTableWidget::item {{ padding: 2px 6px; }}
QListWidget::item {{ padding: 4px 6px; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background-color: {c['bg']}; color: {c['dim']};
    border: none; border-bottom: 1px solid {c['border']};
    padding: 5px 6px; font-weight: 600;
}}
QTableCornerButton::section {{ background: {c['bg']}; border: none; }}

QCheckBox {{ spacing: 8px; background: transparent; color: {c['dim']}; }}

QProgressBar {{
    border: none; background-color: {c['input']}; border-radius: 3px;
    max-height: 6px; min-height: 6px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background-color: {c['secondary']}; border-radius: 3px; }}

/* ---------- أشرطة التمرير ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {c['handle']}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {c['handle_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; background: none; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QGraphicsView {{ background-color: {c['canvas']}; border: none; border-radius: 4px; }}
QMenu {{ background-color: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 3px; }}
QMenu::item:selected {{ background-color: {c['primary']}; color: {c['on_primary']}; }}
QToolTip {{
    background-color: {c['surface']}; color: {c['text']};
    border: 1px solid {c['border']}; padding: 4px;
}}
"""
