# -*- coding: utf-8 -*-
"""
لوحة الترتيب التفاعلية (Block Puzzle) لترتيب السلاسل على شبكة أيام × فقرات زمنية.

- كل عمود = فقرة زمنية ثابتة (نصف ساعة مثلاً) تتكرر يومياً، وعرضه ثابت =
  (عرض النافذة - عرض عمود تسميات الأيام) ÷ عدد الأعمدة، يتجاوب تلقائياً مع
  تغيير حجم النافذة.
- كل سطر = يوم واحد؛ عدد الأسطر = المدى بين تاريخ البداية وتاريخ النهاية.
- كل سلسلة شكل: ارتفاعه بعدد المواد، وعرض كل مادة يُحسب من مدتها (+ مدة بادئة
  السلسلة إن وُجدت) بعدد الفترات. يمكن سحبها لأي مكان فارغ يتسع لبصمتها.
- التكبير/التصغير بدولاب الفأرة يُغيّر ارتفاع المربعات فقط (لا العرض).
- رأس ثابت أعلى الشبكة يعرض بداية ونهاية كل عمود (مثال: 8:00 / 8:30).
- تسميات الأيام (اسم اليوم + التاريخ) تظهر يمين كل سطر.
- التراجع (Ctrl+Z) يشمل: النقل، الإضافة، الحذف، المسح الكامل، والترتيب العشوائي.
- أي تغيير في الأبعاد (مدة الفقرة/عدد الأعمدة/عدد الأيام/بادئات السلاسل) يمر عبر
  relayout() الذي يحاول إبقاء كل قطعة بمكانها، وينقلها لأقرب مكان فارغ إن لزم، ولا يحذف
  قطعة إلا إن استحال وضعها إطلاقاً (ويمكن معاينة ذلك مسبقاً بـ dry_run).
"""
import math
import random
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QBrush, QColor, QPen, QFont, QPainter, QFontMetricsF, QKeySequence
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsObject, QGraphicsItem,
    QWidget, QVBoxLayout, QSizePolicy,
)

from library_scanner import SeriesInfo
from models import GridSnapshot, PieceSnap, OverrideSnapshot

DAY_LABEL_WIDTH = 190
HEADER_HEIGHT = 40
DEFAULT_CELL_HEIGHT = 34
MIN_CELL_HEIGHT = 14
MAX_CELL_HEIGHT = 110
MIN_CELL_WIDTH = 26
MAX_UNDO = 50

ARABIC_WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]

PALETTE = [
    "#4C6EF5", "#12B886", "#F59F00", "#E8590C", "#E64980", "#7048E8",
    "#1098AD", "#82C91E", "#F03E3E", "#845EF7", "#0CA678", "#FAB005",
    "#3B5BDB", "#0B7285", "#D9480F", "#A61E4D",
]

BG_LINE_COLOR = QColor("#33343a")
DAY_TEXT_COLOR = QColor("#d8d9dc")
HEADER_TEXT_COLOR_1 = QColor("#e6e7ea")
HEADER_TEXT_COLOR_2 = QColor("#8a8b91")
SEP_COLOR = QColor("#55565c")
STRIPE_COLOR = QColor(255, 255, 255, 8)


def set_canvas_theme(mode: str):
    """يضبط ألوان خطوط وأرقام اللوحتين بحسب الوضع (فاتح/داكن)؛ تُعاد الرسم بـ apply_theme()."""
    global BG_LINE_COLOR, DAY_TEXT_COLOR, HEADER_TEXT_COLOR_1, HEADER_TEXT_COLOR_2, SEP_COLOR, STRIPE_COLOR
    if mode == "light":
        BG_LINE_COLOR = QColor("#dde0e6")
        DAY_TEXT_COLOR = QColor("#3a3e47")
        HEADER_TEXT_COLOR_1 = QColor("#1c1e23")
        HEADER_TEXT_COLOR_2 = QColor("#7b808b")
        SEP_COLOR = QColor("#b9bdc6")
        STRIPE_COLOR = QColor(0, 0, 0, 10)
    else:
        BG_LINE_COLOR = QColor("#33343a")
        DAY_TEXT_COLOR = QColor("#d8d9dc")
        HEADER_TEXT_COLOR_1 = QColor("#e6e7ea")
        HEADER_TEXT_COLOR_2 = QColor("#8a8b91")
        SEP_COLOR = QColor("#55565c")
        STRIPE_COLOR = QColor(255, 255, 255, 8)

_UNSET = object()


def _fmt_hm(total_minutes: int) -> str:
    total_minutes = total_minutes % (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def cells_for_duration(duration_seconds: Optional[float], slot_seconds: float) -> int:
    """عدد الفترات (نصف الساعة أو أي مدة فقرة أخرى) اللازمة لاستيعاب مدة معينة، بحد أدنى فترة واحدة."""
    if not duration_seconds or duration_seconds <= 0 or slot_seconds <= 0:
        return 1
    return max(1, math.ceil(duration_seconds / slot_seconds - 1e-9))


def series_row_widths(series: SeriesInfo, slot_seconds: float, intro_seconds: float = 0.0) -> List[int]:
    """
    عرض كل مادة على حدة بالفترات، بنفس ترتيب مواد السلسلة. تُضاف مدة بادئة السلسلة
    (إن وُجدت) إلى مدة كل مادة قبل الحساب، لأن البادئة تُبثّ قبل المادة ضمن نفس الحجز.
    """
    if not series.materials:
        return [1]
    return [
        cells_for_duration((m.duration_seconds + intro_seconds) if m.duration_seconds else None, slot_seconds)
        for m in series.materials
    ]


# ---------------------------------------------------------------------------
class SeriesPieceItem(QGraphicsObject):
    """
    قطعة تمثّل سلسلة كاملة: طولها (عدد الصفوف) بعدد المواد، وعرض كل صف على حدة
    يُحسب من مدة تلك المادة (+ بادئتها) بعدد الفترات. كل الصفوف تشترك بنفس عمود
    البداية، وتمتد الصفوف الأطول أكثر نحو الفترات التالية (شكل متدرّج شبيه بالسلّم).
    """

    def __init__(self, series: SeriesInfo, grid: "SeriesGridWidget", color: str):
        super().__init__()
        self.series = series
        self.grid = grid
        self.row_widths = grid._widths_for(series)
        self.color = QColor(color)
        self.start_row = 0
        self.col = 0
        self._press_offset = QPointF(0, 0)

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(5)
        self.setCursor(Qt.OpenHandCursor)

    @property
    def n_cells(self) -> int:
        return max(len(self.row_widths), 1)

    @property
    def width_cells(self) -> int:
        """أقصى عرض بين كل صفوف القطعة (لأغراض حجز المساحة/المربع المحيط)."""
        return max(self.row_widths) if self.row_widths else 1

    def recompute_row_widths(self):
        self.row_widths = self.grid._widths_for(self.series)

    # -- الأبعاد والرسم --------------------------------------------------
    def boundingRect(self) -> QRectF:
        cw = self.grid.cell_width
        ch = self.grid.cell_height
        return QRectF(0, 0, cw * self.width_cells, ch * self.n_cells)

    def paint(self, painter: QPainter, option, widget=None):
        cw = self.grid.cell_width
        ch = self.grid.cell_height
        max_w = self.width_cells
        full_w = cw * max_w

        painter.setRenderHint(QPainter.Antialiasing, True)
        base = self.color
        for i in range(self.n_cells):
            row_w = self.row_widths[i] if i < len(self.row_widths) else 1
            x0 = (max_w - row_w) * cw
            rect = QRectF(x0 + 1, i * ch + 1, row_w * cw - 2, ch - 2)
            painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
            painter.setBrush(QBrush(base))
            painter.drawRoundedRect(rect, 3, 3)

        if self.isSelected():
            painter.setPen(QPen(QColor("#ffffff"), 3))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(0, 0, full_w, ch * self.n_cells), 4, 4)

        if ch >= 14 and full_w >= 18:
            painter.setPen(QPen(QColor("#ffffff")))
            font = QFont()
            font.setPointSizeF(max(6.5, min(9.5, ch * 0.30)))
            painter.setFont(font)
            total_h = ch * self.n_cells
            painter.save()
            painter.translate(full_w * 0.5 + 5, total_h - 4)
            painter.rotate(-90)
            text_rect = QRectF(0, -full_w * 0.5, total_h - 8, full_w)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.series.display_name)
            painter.restore()

    # -- الموضع ------------------------------------------------------------
    def snap_to(self, row: int, col: int):
        self.start_row = row
        self.col = col
        self.prepareGeometryChange()
        self.setPos(QPointF(self.grid._span_left_x(col, self.width_cells), row * self.grid.cell_height))

    def refresh_geometry(self):
        """يُستدعى عند تغيّر حجم الخلية (زوم/تحجيم النافذة) لإعادة رسم القطعة بمكانها الصحيح."""
        self.prepareGeometryChange()
        self.setPos(QPointF(
            self.grid._span_left_x(self.col, self.width_cells), self.start_row * self.grid.cell_height
        ))
        self.update()

    # -- التفاعل مع الفأرة ---------------------------------------------
    def mousePressEvent(self, event):
        self.setCursor(Qt.ClosedHandCursor)
        self.setZValue(10)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.OpenHandCursor)
        self.setZValue(5)
        ctrl_held = bool(event.modifiers() & Qt.ControlModifier)
        self.grid.handle_piece_dropped(self, ctrl_held=ctrl_held)


# ---------------------------------------------------------------------------
class _TimeHeaderView(QGraphicsView):
    """رأس ثابت أعلى الشبكة يعرض بداية/نهاية كل عمود، بلا أي تمرير، ومتصل بصرياً بالجدول تماماً."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setFixedHeight(HEADER_HEIGHT)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setViewportMargins(0, 0, 0, 0)
        self.setStyleSheet("border: none; margin: 0px; padding: 0px;")
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setRenderHint(QPainter.Antialiasing, True)

    def rebuild(self, cols: int, cell_width: float, slot_minutes: int, day_start_minutes: int):
        scene = self.scene()
        scene.clear()
        grid_w = cols * cell_width
        total_w = grid_w + DAY_LABEL_WIDTH  # نفس عرض الشبكة الرئيسية تماماً (تحجز مساحة عمود التسميات)
        scene.setSceneRect(0, 0, total_w, HEADER_HEIGHT)
        for c in range(cols):
            x = (cols - 1 - c) * cell_width  # العمود 0 (أول فقرة) في أقصى اليمين
            start_m = day_start_minutes + c * slot_minutes
            end_m = start_m + slot_minutes

            t1_text = _fmt_hm(start_m)
            t1 = scene.addSimpleText(t1_text)
            t1.setBrush(QBrush(HEADER_TEXT_COLOR_1))
            f = t1.font(); f.setPointSizeF(8.5); t1.setFont(f)
            fm1 = QFontMetricsF(f)
            t1.setPos(x + (cell_width - fm1.horizontalAdvance(t1_text)) / 2, 1)

            t2_text = _fmt_hm(end_m)
            t2 = scene.addSimpleText(t2_text)
            t2.setBrush(QBrush(HEADER_TEXT_COLOR_2))
            f2 = t2.font(); f2.setPointSizeF(8.0); t2.setFont(f2)
            fm2 = QFontMetricsF(f2)
            t2.setPos(x + (cell_width - fm2.horizontalAdvance(t2_text)) / 2, 20)

            scene.addLine(x, 0, x, HEADER_HEIGHT, QPen(BG_LINE_COLOR))
        scene.addLine(grid_w, 0, grid_w, HEADER_HEIGHT, QPen(BG_LINE_COLOR))
        self.setFixedWidth(int(total_w) + 2)


class _GridView(QGraphicsView):
    def __init__(self, owner: "SeriesGridWidget"):
        super().__init__()
        self.owner = owner
        self.setScene(QGraphicsScene(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setStyleSheet("border: none; margin: 0px; padding: 0px;")
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)

    def mousePressEvent(self, event):
        self.setFocus(Qt.MouseFocusReason)
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.owner._recompute_layout()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.owner.zoom(factor)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.owner.remove_selected_pieces(record_undo=True)
            event.accept()
            return
        if event.matches(QKeySequence.Undo):
            self.owner.undo_last()
            event.accept()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
class SeriesGridWidget(QWidget):
    order_changed = Signal()

    def __init__(
        self,
        cols_per_day: int = 16,
        slot_minutes: int = 30,
        day_start_minutes: int = 8 * 60,
        parent=None,
    ):
        super().__init__(parent)
        self.cols = max(1, int(cols_per_day))
        self.slot_minutes = max(1, int(slot_minutes))
        self.day_start_minutes = int(day_start_minutes)
        self.cell_height = float(DEFAULT_CELL_HEIGHT)
        self.cell_width = 40.0
        self.rows = 30
        self.start_date: date = date.today().replace(day=1)
        self.intro_index = None  # IntroIndex | None : لحساب عرض المواد مع بادئاتها

        self.pieces: List[SeriesPieceItem] = []
        self.occupied: Dict[Tuple[int, int], int] = {}  # (row,col) -> id(piece)
        self.column_heights: List[int] = [0] * self.cols  # أعلى صف مشغول+1 لكل عمود (حد أدنى آمن للبحث)
        self._bg_items = []
        # كل عنصر: لقطة كاملة لمواضع القطع [(series, row, col, color_hex), ...]
        self._undo_stack: List[list] = []

        self.header = _TimeHeaderView()
        self.main_view = _GridView(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.main_view, stretch=1)

        self._recompute_layout()

    # ---------------- حساب الأبعاد المتجاوبة ----------------
    def _available_cell_area_width(self) -> float:
        vp = self.main_view.viewport().width()
        return max(vp - DAY_LABEL_WIDTH, self.cols * MIN_CELL_WIDTH)

    def _col_x(self, col: int) -> float:
        """يعكس ترتيب الأعمدة أفقياً: العمود 0 (أول فقرة زمنية) يظهر في أقصى اليمين."""
        return (self.cols - 1 - col) * self.cell_width

    def _col_from_x(self, x: float) -> int:
        return self.cols - 1 - round(x / self.cell_width)

    def _span_left_x(self, col: int, width_cells: int) -> float:
        """الحافة اليسرى المرئية لقطعة تبدأ منطقياً عند العمود col وعرضها width_cells فترات."""
        return (self.cols - col - width_cells) * self.cell_width

    def _col_from_left_x(self, x: float, width_cells: int) -> int:
        return self.cols - width_cells - round(x / self.cell_width)

    def _recompute_layout(self):
        self.cell_width = max(self._available_cell_area_width() / self.cols, MIN_CELL_WIDTH)
        self.header.rebuild(self.cols, self.cell_width, self.slot_minutes, self.day_start_minutes)
        self._redraw_background()
        for p in self.pieces:
            p.refresh_geometry()

    def zoom(self, factor: float):
        new_h = self.cell_height * factor
        new_h = max(MIN_CELL_HEIGHT, min(MAX_CELL_HEIGHT, new_h))
        if abs(new_h - self.cell_height) < 0.4:
            return
        self.cell_height = new_h
        self._redraw_background()
        for p in self.pieces:
            p.refresh_geometry()

    def apply_theme(self):
        """إعادة رسم الخلفية والترويسة بعد تغيير الوضع."""
        self._redraw_background()
        self.header.rebuild(self.cols, self.cell_width, self.slot_minutes, self.day_start_minutes)

    def _redraw_background(self):
        scene = self.main_view.scene()
        for item in self._bg_items:
            scene.removeItem(item)
        self._bg_items = []

        grid_w = self.cols * self.cell_width
        total_w = grid_w + DAY_LABEL_WIDTH
        total_h = self.rows * self.cell_height
        scene.setSceneRect(0, 0, total_w, max(total_h, 1))

        pen = QPen(BG_LINE_COLOR)
        for c in range(self.cols + 1):
            it = scene.addLine(c * self.cell_width, 0, c * self.cell_width, total_h, pen)
            it.setZValue(-10)
            self._bg_items.append(it)
        for r in range(self.rows + 1):
            it = scene.addLine(0, r * self.cell_height, grid_w, r * self.cell_height, pen)
            it.setZValue(-10)
            self._bg_items.append(it)

        sep = scene.addLine(grid_w, 0, grid_w, total_h, QPen(SEP_COLOR))
        sep.setZValue(-9)
        self._bg_items.append(sep)

        show_labels = self.cell_height >= 16
        for r in range(self.rows):
            d = self.start_date + timedelta(days=r)
            if show_labels:
                weekday = ARABIC_WEEKDAYS[d.weekday()]
                label = scene.addSimpleText(f"{weekday}   {d.isoformat()}")
                label.setBrush(QBrush(DAY_TEXT_COLOR))
                f = label.font(); f.setPointSizeF(max(7.0, min(9.5, self.cell_height * 0.3)))
                label.setFont(f)
                ty = r * self.cell_height + max(self.cell_height / 2 - 8, 2)
                label.setPos(grid_w + 10, ty)
                label.setZValue(-5)
                self._bg_items.append(label)
            if r % 2 == 1:
                band = scene.addRect(
                    0, r * self.cell_height, grid_w, self.cell_height,
                    QPen(Qt.NoPen), QBrush(STRIPE_COLOR),
                )
                band.setZValue(-11)
                self._bg_items.append(band)

    # ---------------- حساب العرض (مع البادئة) ----------------
    def _intro_seconds(self, series: SeriesInfo, index=_UNSET) -> float:
        idx = self.intro_index if index is _UNSET else index
        if idx is None:
            return 0.0
        entry, _warn = idx.lookup(series.series_number, series.type_code)
        return float(entry[1]) if entry else 0.0

    def _widths_for(self, series: SeriesInfo, slot_minutes: Optional[int] = None, index=_UNSET) -> List[int]:
        slot = (slot_minutes or self.slot_minutes) * 60
        return series_row_widths(series, slot, self._intro_seconds(series, index))

    # ---------------- إعادة التخطيط الآمنة ----------------
    def relayout(
        self,
        slot_minutes: Optional[int] = None,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
        intro_index=_UNSET,
        dry_run: bool = False,
    ) -> Tuple[List[str], List[str]]:
        """
        يطبّق (أو يحاكي عند dry_run=True) تغييراً في مدة الفقرة/عدد الأعمدة/عدد الأيام/
        فهرس البادئات. تبقى كل قطعة بمكانها إن اتسع، وإلا تُنقل لأقرب مكان فارغ، ولا تُحذف
        إلا إن استحال وضعها. يعيد (أسماء ما نُقل، أسماء ما سيُحذف/حُذف).
        """
        new_slot = max(1, int(slot_minutes)) if slot_minutes else self.slot_minutes
        new_cols = max(1, int(cols)) if cols else self.cols
        new_rows = max(1, int(rows)) if rows else self.rows

        occ: Dict[Tuple[int, int], int] = {}
        plan = []
        moved: List[str] = []
        lost: List[str] = []
        lost_ids = set()
        for p in sorted(self.pieces, key=lambda x: (x.start_row, x.col)):
            widths = self._widths_for(p.series, new_slot, intro_index)
            row, col = p.start_row, p.col
            if not self._can_place(row, col, widths, occ=occ, cols=new_cols, rows=new_rows):
                spot = self._scan_free_spot(widths, occ, new_cols, new_rows, prefer_col=col)
                if spot is None:
                    lost.append(p.series.display_name)
                    lost_ids.add(id(p))
                    continue
                row, col = spot
                moved.append(p.series.display_name)
            for cell in self._footprint(row, col, widths):
                occ[cell] = id(p)
            plan.append((p, widths, row, col))

        if dry_run:
            return moved, lost

        scene = self.main_view.scene()
        for p in list(self.pieces):
            if id(p) in lost_ids:
                self.pieces.remove(p)
                if p.scene() is scene:
                    scene.removeItem(p)
        self.slot_minutes, self.cols, self.rows = new_slot, new_cols, new_rows
        if intro_index is not _UNSET:
            self.intro_index = intro_index
        self.occupied = {}
        self.column_heights = [0] * self.cols
        for p, widths, row, col in plan:
            p.prepareGeometryChange()
            p.row_widths = widths
            p.start_row, p.col = row, col
            self._mark_occupied(p, row, col)
        self._undo_stack.clear()
        self._recompute_layout()
        self.order_changed.emit()
        return moved, lost

    def _scan_free_spot(self, widths, occ, cols, rows, prefer_col: int = 0) -> Optional[Tuple[int, int]]:
        n = len(widths)
        max_w = max(widths) if widths else 1
        if n > rows or max_w > cols:
            return None
        order = [prefer_col] + [c for c in range(cols) if c != prefer_col]
        for col in order:
            if col < 0 or col + max_w > cols:
                continue
            for row in range(0, rows - n + 1):
                if self._can_place(row, col, widths, occ=occ, cols=cols, rows=rows):
                    return row, col
        return None

    # ---------------- إعدادات خارجية (من تبويب الإعدادات) ----------------
    def set_cols_per_day(self, cols: int):
        return self.relayout(cols=cols)

    def set_slot_minutes(self, minutes: int):
        return self.relayout(slot_minutes=minutes)

    def set_intro_index(self, index):
        return self.relayout(intro_index=index)

    def set_day_start_minutes(self, minutes: int):
        self.day_start_minutes = int(minutes)
        self.header.rebuild(self.cols, self.cell_width, self.slot_minutes, self.day_start_minutes)

    def set_date_range(self, start: date, end: date):
        """يضبط مدى الأيام. إن كان end < start لا يُطبَّق شيء. يعيد (المنقول، المحذوف)."""
        if end < start:
            return [], []
        self.start_date = start
        new_rows = max(1, (end - start).days + 1)
        return self.relayout(rows=new_rows)

    def preview_date_range(self, start: date, end: date) -> Tuple[List[str], List[str]]:
        """محاكاة set_date_range دون تطبيق: يعيد (المنقول، المعرّض للحذف)."""
        if end < start:
            return [], []
        return self.relayout(rows=max(1, (end - start).days + 1), dry_run=True)

    # ---------------- التراجع ----------------
    def _snapshot_positions(self) -> list:
        return [(p.series, p.start_row, p.col, p.color.name()) for p in self.pieces]

    def push_undo(self):
        self._undo_stack.append(self._snapshot_positions())
        if len(self._undo_stack) > MAX_UNDO:
            self._undo_stack.pop(0)

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def undo_last(self) -> bool:
        if not self._undo_stack:
            return False
        snap = self._undo_stack.pop()
        self._clear_pieces_silent()
        for series, row, col, color in snap:
            self._place(series, row, col, color)
        self.order_changed.emit()
        return True

    def clear_undo(self):
        self._undo_stack.clear()

    # ---------------- إدارة القطع ----------------
    def _next_color(self) -> str:
        counts = {c: 0 for c in PALETTE}
        for p in self.pieces:
            name = p.color.name().upper()
            for c in PALETTE:
                if c.upper() == name:
                    counts[c] += 1
        return min(PALETTE, key=lambda c: counts[c])

    def _place(self, series: SeriesInfo, row: int, col: int, color: Optional[str] = None) -> Optional[SeriesPieceItem]:
        widths = self._widths_for(series)
        if not self._can_place(row, col, widths):
            return None
        piece = SeriesPieceItem(series, self, color or self._next_color())
        piece.snap_to(row, col)
        self._mark_occupied(piece, row, col)
        self.main_view.scene().addItem(piece)
        self.pieces.append(piece)
        return piece

    def add_series(self, series: SeriesInfo, prefer_col: Optional[int] = None,
                   record_undo: bool = True) -> Optional[SeriesPieceItem]:
        """
        يضيف سلسلة بأول مكان مناسب. إن حُدد prefer_col يجرَّب أولاً، وإن لم يتسع (عرض السلسلة
        يتجاوز نهاية اللوحة أو العمود ممتلئ) يُبحث عن أي مكان آخر بدل الفشل.
        """
        widths = self._widths_for(series)
        max_w = max(widths) if widths else 1
        spot = None
        if prefer_col is not None and 0 <= prefer_col and prefer_col + max_w <= self.cols:
            r = self._find_free_row_in_column(prefer_col, widths)
            if r is not None:
                spot = (r, prefer_col)
        if spot is None:
            spot = self._find_free_spot(widths)
        if spot is None:
            return None
        if record_undo:
            self.push_undo()
        piece = self._place(series, spot[0], spot[1])
        if piece is not None:
            self.order_changed.emit()
        return piece

    def place_series_at(self, series: SeriesInfo, row: int, col: int,
                        color: Optional[str] = None) -> Optional[SeriesPieceItem]:
        """يضع سلسلة عند موضع (سطر، عمود) محدد بالضبط دون أي بحث (لاستعادة ترتيب محفوظ مسبقاً)."""
        piece = self._place(series, row, col, color)
        if piece is not None:
            self.order_changed.emit()
        return piece

    def _find_free_spot(self, row_widths: List[int]) -> Optional[Tuple[int, int]]:
        n_cells = len(row_widths)
        max_w = max(row_widths) if row_widths else 1
        if n_cells > self.rows:
            return None
        for col in range(0, self.cols - max_w + 1):
            row = self._find_free_row_in_column(col, row_widths)
            if row is not None:
                return row, col
        return None

    def _find_free_row_in_column(self, col: int, row_widths: List[int], ignore=None) -> Optional[int]:
        """
        يعيد أعلى (أصغر) رقم سطر يمكن أن تبدأ منه قطعة بهذه الأعراض، بدءاً من هذا العمود.
        يستخدم مصفوفة ارتفاعات الأعمدة كحد أعلى مضمون الخلوّ، فلا يفحص خلية-خلية إلا
        ضمن النطاق غير المؤكد (التحقق النهائي عبر _can_place يضمن الصحة).
        """
        n_cells = len(row_widths)
        max_w = max(row_widths) if row_widths else 1
        if n_cells > self.rows or col + max_w > self.cols:
            return None

        relevant_heights = self.column_heights[col:col + max_w]
        safe_row = max(relevant_heights) if relevant_heights else 0
        scan_limit = min(safe_row, self.rows - n_cells + 1)

        for row in range(0, max(scan_limit, 0)):
            if self._can_place(row, col, row_widths, ignore=ignore):
                return row
        if safe_row + n_cells <= self.rows and self._can_place(safe_row, col, row_widths, ignore=ignore):
            return safe_row
        for row in range(max(scan_limit, 0), self.rows - n_cells + 1):
            if self._can_place(row, col, row_widths, ignore=ignore):
                return row
        return None

    def _footprint(self, row: int, col: int, row_widths: List[int]):
        """مولّد خلايا البصمة (وليس قائمة جاهزة)، ليتمكن _can_place من التوقف فور أول تصادم."""
        for i, rw in enumerate(row_widths):
            for w in range(rw):
                yield (row + i, col + w)

    def _can_place(self, row: int, col: int, row_widths: List[int], ignore=None,
                   occ=None, cols: Optional[int] = None, rows: Optional[int] = None) -> bool:
        if col < 0 or row < 0:
            return False
        occ = self.occupied if occ is None else occ
        cols = self.cols if cols is None else cols
        rows = self.rows if rows is None else rows
        n_cells = len(row_widths)
        max_w = max(row_widths) if row_widths else 1
        if col + max_w > cols or row + n_cells > rows:
            return False
        for cell in self._footprint(row, col, row_widths):
            o = occ.get(cell)
            if o is not None and o != ignore:
                return False
        return True

    def _mark_occupied(self, piece: SeriesPieceItem, row: int, col: int):
        for cell in self._footprint(row, col, piece.row_widths):
            self.occupied[cell] = id(piece)
            r, c = cell
            if 0 <= c < len(self.column_heights) and r + 1 > self.column_heights[c]:
                self.column_heights[c] = r + 1

    def _unmark_occupied(self, piece: SeriesPieceItem):
        touched_cols = set()
        for cell in self._footprint(piece.start_row, piece.col, piece.row_widths):
            if self.occupied.get(cell) == id(piece):
                del self.occupied[cell]
                touched_cols.add(cell[1])
        if touched_cols:
            new_heights = {c: 0 for c in touched_cols}
            for (r, c) in self.occupied.keys():
                if c in new_heights and r + 1 > new_heights[c]:
                    new_heights[c] = r + 1
            for c, h in new_heights.items():
                if 0 <= c < len(self.column_heights):
                    self.column_heights[c] = h

    def _clear_pieces_silent(self):
        """يمسح كل القطع دفعة واحدة (أسرع بكثير من إزالتها واحدة واحدة) دون إشارات ولا تراجع."""
        scene = self.main_view.scene()
        for p in self.pieces:
            if p.scene() is scene:
                scene.removeItem(p)
        self.pieces = []
        self.occupied = {}
        self.column_heights = [0] * self.cols

    def remove_piece(self, piece: SeriesPieceItem, record_undo: bool = False):
        if piece not in self.pieces:
            return
        if record_undo:
            self.push_undo()
        self._unmark_occupied(piece)
        self.pieces.remove(piece)
        scene = self.main_view.scene()
        if piece.scene() is scene:
            scene.removeItem(piece)
        self.order_changed.emit()

    def remove_selected_pieces(self, record_undo: bool = True):
        selected = [p for p in self.pieces if p.isSelected()]
        if not selected:
            return
        if record_undo:
            self.push_undo()
        for p in selected:
            self.remove_piece(p, record_undo=False)

    def clear_all(self, record_undo: bool = False):
        if record_undo and self.pieces:
            self.push_undo()
        self._clear_pieces_silent()
        self.order_changed.emit()

    def randomize_arrangement(self) -> List[str]:
        """
        يعيد ترتيب كل السلاسل الموجودة حالياً بشكل عشوائي بلا فراغات، مع تفضيل الأعمدة
        الأقل امتلاءً. العملية ذرّية: إن تعذّر وضع أي سلسلة يبقى الترتيب الحالي كما هو
        تماماً ويُعاد أسماء السلاسل التي لم تتسع. وعند النجاح يمكن التراجع عنها (Ctrl+Z).
        """
        items = [(p.series, p.color.name()) for p in self.pieces]
        random.shuffle(items)

        heights = [0] * self.cols
        placements = []
        failed: List[str] = []
        for series, color in items:
            widths = self._widths_for(series)
            n_cells = len(widths)
            max_w = max(widths) if widths else 1
            candidates = [
                c for c in range(0, self.cols - max_w + 1)
                if max(heights[c:c + max_w]) + n_cells <= self.rows
            ]
            if not candidates:
                failed.append(series.display_name)
                continue
            candidate_heights = {c: max(heights[c:c + max_w]) for c in candidates}
            min_height = min(candidate_heights.values())
            col = random.choice([c for c, h in candidate_heights.items() if h == min_height])
            row = min_height
            placements.append((series, row, col, color))
            for c in range(col, col + max_w):
                heights[c] = row + n_cells

        if failed:
            return failed

        self.push_undo()
        self._clear_pieces_silent()
        for series, row, col, color in placements:
            self._place(series, row, col, color)
        self.order_changed.emit()
        return []

    def handle_piece_dropped(self, piece: SeriesPieceItem, ctrl_held: bool = False):
        raw_col = self._col_from_left_x(piece.pos().x(), piece.width_cells)
        col = max(0, min(self.cols - piece.width_cells, raw_col))

        if not ctrl_held and col != piece.col:
            new_row = self._find_free_row_in_column(col, piece.row_widths, ignore=id(piece))
            if new_row is None:
                piece.snap_to(piece.start_row, piece.col)
                return
            row = new_row
        else:
            raw_row = round(piece.pos().y() / self.cell_height)
            row = max(0, min(self.rows - piece.n_cells, raw_row))
            if not self._can_place(row, col, piece.row_widths, ignore=id(piece)):
                piece.snap_to(piece.start_row, piece.col)
                return

        if row == piece.start_row and col == piece.col:
            piece.snap_to(row, col)  # لا تغيير فعلي، فقط إعادة تثبيت بصرية
            return

        self.push_undo()
        self._unmark_occupied(piece)
        piece.snap_to(row, col)
        self._mark_occupied(piece, row, col)
        self.order_changed.emit()

    # ---------------- الاستعلام عن الترتيب الناتج ----------------
    def get_ordered_series(self) -> List[SeriesInfo]:
        """قائمة السلاسل بترتيب أول ظهور لها ضمن التسلسل اليومي."""
        seen = set()
        result = []
        for series, _mat in self.get_playback_sequence():
            key = str(series.folder)
            if key not in seen:
                seen.add(key)
                result.append(series)
        return result

    def get_playback_sequence(self):
        """يوماً بيوم (سطراً بسطر)، وضمن كل يوم عموداً بعمود: يعيد [(SeriesInfo, Material), ...]."""
        by_id = {id(p): p for p in self.pieces}
        seq = []
        for row in range(self.rows):
            for col in range(self.cols):
                pid = self.occupied.get((row, col))
                if pid is None:
                    continue
                piece = by_id.get(pid)
                if not piece or piece.col != col:
                    continue
                idx = row - piece.start_row
                if 0 <= idx < len(piece.series.materials):
                    seq.append((piece.series, piece.series.materials[idx]))
        return seq

    def snapshot_for_build(self) -> GridSnapshot:
        """لقطة ثابتة (بلا عناصر Qt) تُمرَّر لخيط البناء بأمان."""
        pieces = {
            id(p): PieceSnap(p.series, p.start_row, p.col, tuple(p.row_widths))
            for p in self.pieces
        }
        return GridSnapshot(
            start_date=self.start_date, rows=self.rows, cols=self.cols,
            slot_minutes=self.slot_minutes, day_start_minutes=self.day_start_minutes,
            occupied=dict(self.occupied), pieces=pieces,
        )


# ---------------------------------------------------------------------------
class _PreviewGridView(QGraphicsView):
    """عرض للقراءة فقط (بلا سحب) للوحة 24 ساعة، مع تكبير/تصغير بدولاب الفأرة."""

    def __init__(self, owner: "Preview24hGridWidget"):
        super().__init__()
        self.owner = owner
        self.setScene(QGraphicsScene(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setInteractive(False)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setStyleSheet("border: none; margin: 0px; padding: 0px;")
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.owner._recompute_layout()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.owner.zoom(factor)
        event.accept()


class Preview24hGridWidget(QWidget):
    """
    عرض للقراءة فقط (بلا سحب) يُظهر جدول 24 ساعة كاملاً، بتكرار نمط "ترتيب 8 ساعات"
    ليغطي اليوم الكامل، إضافة إلى ثلاثة بدائل تحلّ محل ما هو مجدول أصلاً في مواعيدها:
    بديل الفجر (يومي)، بديل المساء (يومي)، وبدائل الجمعة (عدة ملفات تُعامَل كمادة واحدة
    مجموع مددها هو ما يحدد عدد الفترات المحجوزة).
    """

    def __init__(self, source_grid: "SeriesGridWidget", parent=None):
        super().__init__(parent)
        self.source = source_grid
        self.cell_height = float(DEFAULT_CELL_HEIGHT)
        self.cell_width = 40.0
        self._dirty = True

        self.morning_minutes = 6 * 60
        self.morning_file: Optional[object] = None  # Path | None
        self.morning_duration: float = 0.0

        self.evening_minutes = 20 * 60
        self.evening_file: Optional[object] = None
        self.evening_duration: float = 0.0

        self.friday_minutes = 12 * 60
        self.friday_files: List[Tuple[object, float]] = []  # [(Path, duration), ...]

        self.header = _TimeHeaderView()
        self.main_view = _PreviewGridView(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.main_view, stretch=1)

        self.source.order_changed.connect(self._request_refresh)
        self._recompute_layout()

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def _request_refresh(self):
        """
        يُستدعى كلما تغيّرت لوحة "ترتيب 8 ساعات". إعادة رسم هذه اللوحة مكلفة، فنكتفي
        بوضع علامة "غير محدَّث" ونؤجل الرسم الفعلي حتى يصبح هذا التبويب مرئياً.
        """
        self._dirty = True
        if self.isVisible():
            self.refresh()

    def snapshot(self) -> OverrideSnapshot:
        return OverrideSnapshot(
            morning_minutes=self.morning_minutes, morning_file=self.morning_file,
            morning_duration=self.morning_duration,
            evening_minutes=self.evening_minutes, evening_file=self.evening_file,
            evening_duration=self.evening_duration,
            friday_minutes=self.friday_minutes, friday_files=list(self.friday_files),
        )

    # ---------------- الأبعاد ----------------
    @property
    def cols(self) -> int:
        base = max(self.source.cols, 1)
        slot_minutes = max(self.source.slot_minutes, 1)
        total_cols = (24 * 60) // slot_minutes
        return max(total_cols, base)

    def _available_cell_area_width(self) -> float:
        vp = self.main_view.viewport().width()
        return max(vp - DAY_LABEL_WIDTH, self.cols * MIN_CELL_WIDTH)

    def _col_x(self, col: int) -> float:
        return (self.cols - 1 - col) * self.cell_width

    def _recompute_layout(self):
        self.cell_width = max(self._available_cell_area_width() / self.cols, MIN_CELL_WIDTH)
        self.refresh()

    def zoom(self, factor: float):
        new_h = self.cell_height * factor
        new_h = max(MIN_CELL_HEIGHT, min(MAX_CELL_HEIGHT, new_h))
        if abs(new_h - self.cell_height) < 0.4:
            return
        self.cell_height = new_h
        self.refresh()

    # ---------------- إعداد البدائل ----------------
    def set_morning(self, minutes: int, path, duration: float, refresh_now: bool = True):
        self.morning_minutes = int(minutes)
        self.morning_file = path
        self.morning_duration = duration or 0.0
        if refresh_now:
            self.refresh()

    def set_evening(self, minutes: int, path, duration: float, refresh_now: bool = True):
        self.evening_minutes = int(minutes)
        self.evening_file = path
        self.evening_duration = duration or 0.0
        if refresh_now:
            self.refresh()

    def set_friday(self, minutes: int, files: List[Tuple[object, float]], refresh_now: bool = True):
        self.friday_minutes = int(minutes)
        self.friday_files = list(files)
        if refresh_now:
            self.refresh()

    # ---------------- الرسم ----------------
    def _draw_override(self, scene, row: int, start_col: int, span: int, color_hex: str, label_text: str):
        start_col = max(0, min(self.cols - 1, start_col))
        span = max(1, min(span, self.cols - start_col))
        x0 = self._col_x(start_col + span - 1)
        width = span * self.cell_width
        y = row * self.cell_height
        rect = scene.addRect(
            x0 + 1, y + 1, width - 2, self.cell_height - 2,
            QPen(QColor("#ffffff"), 1, Qt.DashLine), QBrush(QColor(color_hex)),
        )
        rect.setZValue(5)
        if self.cell_height >= 14 and width >= 22:
            label = scene.addSimpleText(label_text)
            label.setBrush(QBrush(QColor("#111111")))
            f = label.font(); f.setPointSizeF(max(6.5, min(9.0, self.cell_height * 0.3)))
            label.setFont(f)
            fm = QFontMetricsF(f)
            label.setPos(x0 + (width - fm.horizontalAdvance(label_text)) / 2, y + 2)
            label.setZValue(6)

    def apply_theme(self):
        self.refresh()

    def refresh(self):
        scene = self.main_view.scene()
        scene.clear()
        self._dirty = False

        cols = self.cols
        rows = self.source.rows
        grid_w = cols * self.cell_width
        total_w = grid_w + DAY_LABEL_WIDTH
        total_h = rows * self.cell_height
        scene.setSceneRect(0, 0, total_w, max(total_h, 1))

        self.header.rebuild(cols, self.cell_width, self.source.slot_minutes, self.source.day_start_minutes)

        pen = QPen(BG_LINE_COLOR)
        for c in range(cols + 1):
            it = scene.addLine(c * self.cell_width, 0, c * self.cell_width, total_h, pen)
            it.setZValue(-10)
        for r in range(rows + 1):
            it = scene.addLine(0, r * self.cell_height, grid_w, r * self.cell_height, pen)
            it.setZValue(-10)
        sep = scene.addLine(grid_w, 0, grid_w, total_h, QPen(SEP_COLOR))
        sep.setZValue(-9)

        show_labels = self.cell_height >= 16
        base_cols = max(self.source.cols, 1)
        slot_minutes = max(self.source.slot_minutes, 1)
        slot_secs = slot_minutes * 60
        by_id = {id(p): p for p in self.source.pieces}

        def col_of(minutes: int) -> int:
            rel = (minutes - self.source.day_start_minutes) % (24 * 60)
            return round(rel / slot_minutes) % cols

        def span_of(duration: float) -> int:
            return max(1, math.ceil(duration / slot_secs - 1e-9)) if duration else 1

        morning_col, morning_span = col_of(self.morning_minutes), span_of(self.morning_duration)
        evening_col, evening_span = col_of(self.evening_minutes), span_of(self.evening_duration)
        friday_start_col = col_of(self.friday_minutes)
        friday_total = sum(d for _p, d in self.friday_files if d)
        friday_span = span_of(friday_total)

        for row in range(rows):
            d = self.source.start_date + timedelta(days=row)
            is_friday = d.weekday() == 4

            for col in range(cols):
                logical_col = col % base_cols
                pid = self.source.occupied.get((row, logical_col))
                if not pid:
                    continue
                piece = by_id.get(pid)
                if not piece or piece.col != logical_col:
                    continue
                idx = row - piece.start_row
                if not (0 <= idx < len(piece.series.materials)):
                    continue
                w = piece.row_widths[idx] if idx < len(piece.row_widths) else 1
                w = max(1, min(w, cols - col))
                x = self._col_x(col + w - 1)
                rect = scene.addRect(
                    x + 1, row * self.cell_height + 1, w * self.cell_width - 2, self.cell_height - 2,
                    QPen(QColor(0, 0, 0, 90)), QBrush(piece.color),
                )
                rect.setZValue(1)
                if show_labels and w * self.cell_width >= 18:
                    mat = piece.series.materials[idx]
                    label = scene.addSimpleText(f"{piece.series.series_number} - {mat.number}")
                    label.setBrush(QBrush(QColor("#ffffff")))
                    f = label.font(); f.setPointSizeF(max(6.0, min(8.0, self.cell_height * 0.28)))
                    label.setFont(f)
                    label.setPos(x + 2, row * self.cell_height + 2)
                    label.setZValue(2)

            if self.morning_file and self.morning_duration:
                self._draw_override(scene, row, morning_col, morning_span, "#F59F00", "صباح")
            if self.evening_file and self.evening_duration:
                self._draw_override(scene, row, evening_col, evening_span, "#7048E8", "مساء")
            if is_friday and friday_total:
                self._draw_override(scene, row, friday_start_col, friday_span, "#12B886", "الجمعة")

            if show_labels:
                weekday = ARABIC_WEEKDAYS[d.weekday()]
                label = scene.addSimpleText(f"{weekday}   {d.isoformat()}")
                label.setBrush(QBrush(DAY_TEXT_COLOR))
                f = label.font(); f.setPointSizeF(max(7.0, min(9.5, self.cell_height * 0.3)))
                label.setFont(f)
                ty = row * self.cell_height + max(self.cell_height / 2 - 8, 2)
                label.setPos(grid_w + 10, ty)
                label.setZValue(-5)

            if row % 2 == 1:
                band = scene.addRect(
                    0, row * self.cell_height, grid_w, self.cell_height,
                    QPen(Qt.NoPen), QBrush(STRIPE_COLOR),
                )
                band.setZValue(-11)
