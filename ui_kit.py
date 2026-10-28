# -*- coding: utf-8 -*-
"""
عناصر واجهة مشتركة: أحجام موحّدة، أقسام قابلة للطي، حقل مسار مختصر، منتقي ألوان،
جدول بسحب الصفوف، صفحة قابلة للتمرير، وتوهج خفيف عند المرور/التركيز.
"""
import re
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QObject, QEvent, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QColorDialog, QComboBox, QFrame, QGraphicsDropShadowEffect,
    QGridLayout, QLineEdit, QListWidget, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QDateEdit, QListWidgetItem, QTableWidget, QTableWidgetItem, QTimeEdit, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

import theme

CTRL_W = 136      # عرض موحّد لكل الحقول القصيرة (قوائم، تواريخ، أوقات، أرقام)
CTRL_H = 30
BTN_W = 124       # عرض موحّد لكل الأزرار
BTN_H = 30
FILE_W = 420     # عرض حقل ملف البديل (اسم الملف + مدته)
ROW_H = 26
SCROLL_GAP = 18   # فراغ جهة شريط التمرير (يسار الصفحة في الواجهة العربية)


def screen_fraction_height(fraction: float = 0.25, minimum: int = 140) -> int:
    scr = QGuiApplication.primaryScreen()
    h = scr.availableGeometry().height() if scr else 800
    return max(minimum, int(h * fraction))


def tall(widget: QWidget, fraction: float = 0.25) -> QWidget:
    """يضمن ألا يقل ارتفاع الجدول/السجل عن نسبة من ارتفاع الشاشة، ويتمدد إن اتسع المكان."""
    widget.setMinimumHeight(screen_fraction_height(fraction))
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return widget


def fit(widget: QWidget, width: int = CTRL_W) -> QWidget:
    """حجم موحّد للحقول القصيرة. الأرقام والتواريخ والأوقات تُعرض LTR ومحاذاة لليمين."""
    widget.setFixedSize(width, CTRL_H)
    if isinstance(widget, QAbstractSpinBox):
        widget.setButtonSymbols(QAbstractSpinBox.NoButtons)
        widget.setLayoutDirection(Qt.LeftToRight)
        widget.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if isinstance(widget, QTimeEdit):
        widget.setDisplayFormat("HH:mm")        # صيغة صريحة: الصيغة الافتراضية تنعكس في الواجهة العربية
    elif isinstance(widget, QDateEdit):
        widget.setDisplayFormat("yyyy-MM-dd")
    return widget


def btn(text: str, slot: Optional[Callable] = None, name: Optional[str] = None,
        tooltip: Optional[str] = None) -> QPushButton:
    b = QPushButton(text)
    b.setFixedSize(BTN_W, BTN_H)
    b.setCursor(Qt.PointingHandCursor)
    if name:
        b.setObjectName(name)
    if tooltip:
        b.setToolTip(tooltip)
    if slot is not None:
        b.clicked.connect(lambda _checked=False, s=slot: s())
    return b


def button_column(*buttons: QPushButton) -> QWidget:
    """عمود أزرار متراصّة فوق بعضها (يظهر على يمين القسم في الواجهة العربية)."""
    w = QWidget()
    w.setFixedWidth(BTN_W)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for b in buttons:
        lay.addWidget(b)
    lay.addStretch(1)
    return w


def page_layout(content: QWidget) -> QVBoxLayout:
    lay = QVBoxLayout(content)
    lay.setContentsMargins(SCROLL_GAP, 12, 14, 14)
    lay.setSpacing(4)
    return lay


def make_page(content: QWidget, min_width: int = 560) -> QScrollArea:
    """كل صفحة داخل مساحة تمرير مستقلة (عمودياً وأفقياً عند الحاجة)."""
    content.setMinimumWidth(min_width)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    sc.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    sc.setWidget(content)
    return sc


# ---------------------------------------------------------------------------
class PathEdit(QLineEdit):
    """حقل مسار: يعرض اسم المجلد الأخير فقط، ويكشف المسار كاملاً عند الضغط عليه. يقبل السحب والإفلات."""
    pathChanged = Signal(str)

    def __init__(self, parent=None, folders_only: bool = False, placeholder: str = ""):
        super().__init__(parent)
        self.folders_only = folders_only
        self._full = ""
        self.setAcceptDrops(True)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self.returnPressed.connect(self._commit)

    @staticmethod
    def short(path: str) -> str:
        p = (path or "").strip()
        if not p:
            return ""
        parts = [x for x in re.split(r"[\\/]", p.rstrip("\\/")) if x]
        return parts[-1] if parts else p

    def _display(self, path: str) -> str:
        return self.short(path)

    def text(self) -> str:   # noqa: D401
        return super().text() if self.hasFocus() else self._full

    def setText(self, text: str):
        text = text or ""
        self._set_full(text, emit=True)
        super().setText(text if self.hasFocus() else self._display(text))

    def _set_full(self, text: str, emit: bool):
        text = text.strip()
        changed = text != self._full
        self._full = text
        self.setToolTip(text)
        if changed and emit:
            self.pathChanged.emit(text)

    def _commit(self):
        self._set_full(super().text(), emit=True)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        super().setText(self._full)
        self.selectAll()

    def focusOutEvent(self, e):
        self._commit()
        super().focusOutEvent(e)
        super().setText(self._display(self._full))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if not urls:
            super().dropEvent(e)
            return
        p = Path(urls[0].toLocalFile())
        if self.folders_only and p.is_file():
            p = p.parent
        self.setText(str(p))
        e.acceptProposedAction()


def fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class FileEdit(PathEdit):
    """حقل ملف: يعرض اسم الملف مع مدته فقط (بلا المسار)، ويكشف المسار الكامل عند الضغط عليه."""

    def __init__(self, parent=None, placeholder: str = "", duration_fn: Optional[Callable] = None):
        super().__init__(parent, folders_only=False, placeholder=placeholder)
        self.duration_fn = duration_fn
        self.setFixedWidth(FILE_W)

    def _display(self, path: str) -> str:
        name = self.short(path)
        if name and self.duration_fn is not None:
            try:
                d = self.duration_fn(Path(path))
            except Exception:  # noqa: BLE001
                d = None
            if d:
                name += f"   ({fmt_dur(d)})"
        return name


# ---------------------------------------------------------------------------
class Section(QWidget):
    """قسم بعنوان نصي بسيط يُطوى ويُفتح بالنقر، بلا إطار."""
    toggled = Signal(bool)

    def __init__(self, title: str, body: QWidget, expanded: bool = True, key: str = ""):
        super().__init__()
        self.title = title
        self.key = key or title
        self.body = body
        lay = QVBoxLayout(self)
        self._lay = lay
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.header = QPushButton()
        self.header.setObjectName("sectionHeader")
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setFocusPolicy(Qt.NoFocus)
        self.header.clicked.connect(lambda _c=False: self.set_expanded(not self._expanded))
        lay.addWidget(self.header)
        lay.addWidget(body)
        self._expanded = True
        self.set_expanded(expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, value: bool):
        value = bool(value)
        changed = value != self._expanded
        self._expanded = value
        self.body.setVisible(value)
        self._lay.setContentsMargins(0, 0, 0, 14 if value else 0)
        # مطوي: بلا أي تمدد رأسي (لا فراغات بين الأقسام المطوية)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.header.setText(("▾  " if value else "◂  ") + self.title)
        if changed:
            self.toggled.emit(value)


# ---------------------------------------------------------------------------
class ColorPicker(QPushButton):
    """زر بلون محدد يفتح لوحة ألوان جاهزة + خيار لون مخصص."""
    colorChanged = Signal(str)

    def __init__(self, color: str):
        super().__init__()
        self.setFixedSize(CTRL_W, CTRL_H)
        self.setCursor(Qt.PointingHandCursor)
        self._color = color
        self._menu = QMenu(self)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        for i, col in enumerate(theme.PRESET_COLORS):
            b = QToolButton()
            b.setObjectName("swatch")
            b.setFixedSize(22, 22)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QToolButton#swatch {{ background-color: {col}; border: 1px solid rgba(0,0,0,60); }}"
                            f"QToolButton#swatch:hover {{ border: 2px solid #ffffff; }}")
            b.clicked.connect(lambda _c=False, cc=col: self._pick(cc))
            grid.addWidget(b, i // 8, i % 8)
        custom = QPushButton("لون آخر…")
        custom.setFixedHeight(BTN_H)
        custom.clicked.connect(self._custom)
        grid.addWidget(custom, 7, 0, 1, 8)
        act = QWidgetAction(self._menu)
        act.setDefaultWidget(holder)
        self._menu.addAction(act)
        self.clicked.connect(lambda _c=False: self._menu.exec(self.mapToGlobal(self.rect().bottomLeft())))
        self._paint()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str, emit: bool = False):
        if not QColor(color).isValid():
            return
        self._color = QColor(color).name()
        self._paint()
        if emit:
            self.colorChanged.emit(self._color)

    def _paint(self):
        fg = theme.on_color(self._color)
        self.setText(self._color.upper())
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._color}; color: {fg}; border-radius: 5px; }}"
            f"QPushButton:hover {{ background-color: {self._color}; }}"
        )

    def _pick(self, col: str):
        self._menu.close()
        self.set_color(col, emit=True)

    def _custom(self):
        self._menu.close()
        c = QColorDialog.getColor(QColor(self._color), self, "اختر لوناً")
        if c.isValid():
            self.set_color(c.name(), emit=True)


# ---------------------------------------------------------------------------
class DragTable(QTableWidget):
    """جدول تُعاد ترتيب صفوفه بالسحب والإفلات (العمود 0 مقبض ⠿ اختياري). يُصدر rows_moved بعد النقل."""
    rows_moved = Signal()

    def __init__(self, rows: int = 0, cols: int = 0, parent=None):
        super().__init__(rows, cols, parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)    # كي لا يحذف Qt الصف المصدر بنفسه
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)

    def dropEvent(self, e):
        if e.source() is not self:
            e.ignore()
            return
        sel = sorted({i.row() for i in self.selectedIndexes()})
        if not sel:
            e.ignore()
            return
        pos = e.position().toPoint()
        target = self.rowAt(pos.y())
        if target < 0:
            target = self.rowCount() - 1
        self.move_rows(sel, target)
        e.setDropAction(Qt.CopyAction)
        e.accept()

    def move_rows(self, rows: List[int], target: int):
        n, c = self.rowCount(), self.columnCount()
        grid = [[self.takeItem(r, k) for k in range(c)] for r in range(n)]
        moving = [grid[r] for r in rows]
        rest = [grid[r] for r in range(n) if r not in rows]
        # موضع الإدراج: أمام الصف المستهدف إن كنا نسحب لأعلى، وبعده إن كنا نسحب لأسفل
        before = sum(1 for r in range(target) if r not in rows)
        idx = before + (1 if rows[0] < target else 0)
        idx = max(0, min(idx, len(rest)))
        new = rest[:idx] + moving + rest[idx:]
        for r, row_items in enumerate(new):
            for k, it in enumerate(row_items):
                if it is not None:
                    self.setItem(r, k, it)
        self.clearSelection()
        for r in range(idx, idx + len(moving)):
            self.selectRow(r)
        self.rows_moved.emit()


# ---------------------------------------------------------------------------
class GlowFilter(QObject):
    """توهج متدرّج خفيف من لون العنصر عند مرور الفأرة فوق الأزرار وعند التركيز على الحقول."""

    def __init__(self, get_colors: Callable[[], dict], parent=None):
        super().__init__(parent)
        self._colors = get_colors

    def _color_for(self, w: QWidget) -> QColor:
        c = self._colors()
        if isinstance(w, QPushButton):
            name = w.objectName()
            if name in ("secondaryButton", "successButton"):
                return QColor(c["secondary"])
            if name == "dangerButton":
                return QColor(c["danger"])
        return QColor(c["primary"])

    def _apply(self, w: QWidget, strength: int):
        if w is None or not w.isEnabled():
            return
        fx = QGraphicsDropShadowEffect(w)
        fx.setBlurRadius(20)
        fx.setOffset(0, 0)
        col = self._color_for(w)
        col.setAlpha(strength)
        fx.setColor(col)
        w.setGraphicsEffect(fx)

    @staticmethod
    def _clear(w: QWidget):
        if w is not None and w.graphicsEffect() is not None:
            w.setGraphicsEffect(None)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in (QEvent.Enter, QEvent.Leave, QEvent.FocusIn, QEvent.FocusOut,
                     QEvent.EnabledChange, QEvent.Hide):
            return False
        try:
            if isinstance(obj, QPushButton) and obj.objectName() != "sectionHeader":
                if t == QEvent.Enter:
                    self._apply(obj, 95)
                elif t in (QEvent.Leave, QEvent.Hide, QEvent.EnabledChange):
                    self._clear(obj)
            elif isinstance(obj, (QLineEdit, QComboBox)):
                target = obj
                par = obj.parentWidget()
                if isinstance(obj, QLineEdit) and isinstance(par, QAbstractSpinBox):
                    target = par
                elif isinstance(obj, QLineEdit) and isinstance(par, QComboBox):
                    target = par
                if t == QEvent.FocusIn:
                    self._apply(target, 110)
                elif t in (QEvent.FocusOut, QEvent.Hide):
                    self._clear(target)
        except RuntimeError:
            pass
        return False


# ---------------------------------------------------------------------------
class FileList(QListWidget):
    """
    قائمة ملفات بنفس شكل حقل الملف (إطار سفلي فقط، سطر واحد وهي فارغة، ويكبر ارتفاعها بعدد الملفات).
    تقبل إفلات ملفات من خارج البرنامج، وتُعاد ترتيبها بالسحب الداخلي.
    """
    files_dropped = Signal(list)
    reordered = Signal()

    def __init__(self, parent=None, placeholder: str = ""):
        super().__init__(parent)
        self.setObjectName("fileList")
        self.placeholder = placeholder
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedWidth(FILE_W)
        self.model().rowsInserted.connect(lambda *_: QTimer.singleShot(0, self.fit_height))
        self.model().rowsRemoved.connect(lambda *_: QTimer.singleShot(0, self.fit_height))
        self.fit_height()

    def addItem(self, item):   # noqa: N802
        if not isinstance(item, QListWidgetItem):
            item = QListWidgetItem(item)
        item.setSizeHint(QSize(0, ROW_H))
        super().addItem(item)

    def fit_height(self):
        n = max(1, self.count())
        self.setFixedHeight(CTRL_H + (n - 1) * ROW_H)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0 and self.placeholder:
            p = QPainter(self.viewport())
            col = QColor(self.palette().color(self.foregroundRole()))
            col.setAlpha(110)
            p.setPen(col)
            p.drawText(self.viewport().rect().adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeading,
                       self.placeholder)
            p.end()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            files = [Path(u.toLocalFile()) for u in e.mimeData().urls()]
            files = [f for f in files if f.is_file()]
            if files:
                self.files_dropped.emit(files)
            e.acceptProposedAction()
            return
        super().dropEvent(e)
        self.reordered.emit()


# ---------------------------------------------------------------------------
def enable_row_numbers(table: QTableWidget):
    """يُظهر أرقام الأسطر (متسلسلة للأسطر الظاهرة فقط) ويحدّثها عند الإضافة/الحذف/الفرز؛ table.renumber() بعد الإخفاء."""
    table.verticalHeader().setVisible(True)
    table.verticalHeader().setMinimumWidth(34)
    table.verticalHeader().setDefaultAlignment(Qt.AlignCenter)

    def renumber():
        n = 0
        for r in range(table.rowCount()):
            if table.isRowHidden(r):
                continue
            n += 1
            it = table.verticalHeaderItem(r)
            if it is None:
                table.setVerticalHeaderItem(r, QTableWidgetItem(str(n)))
            elif it.text() != str(n):
                it.setText(str(n))

    timer = QTimer(table)
    timer.setSingleShot(True)
    timer.setInterval(0)
    timer.timeout.connect(renumber)
    model = table.model()
    for sig in (model.rowsInserted, model.rowsRemoved, model.layoutChanged, model.modelReset):
        sig.connect(lambda *_a: timer.start())
    table.renumber = renumber
    renumber()
