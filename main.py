# -*- coding: utf-8 -*-
"""
منظّم قوائم التشغيل الصوتية - واجهة رسومية احترافية (PySide6)

الصفحات: المكتبة (سلاسل، بادئات، فواصل، استثناءات)، ترتيب 8 ساعات، ترتيب 24 ساعة، المعاينة،
الإعدادات، حول. (الشرح التفصيلي الكامل داخل تبويب "حول".)
"""
import sys
import os
import json
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import (
    Qt, QThread, Signal, QDate, QTime, QItemSelectionModel, QSettings, QUrl, QTimer,
    QStandardPaths,
)
from PySide6.QtGui import QKeySequence, QShortcut, QDesktopServices, QBrush, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QListWidgetItem, QLabel, QLineEdit,
    QFileDialog, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QCheckBox, QProgressBar, QTextEdit, QMessageBox, QHeaderView, QTabWidget,
    QAbstractItemView, QDateEdit, QTimeEdit, QComboBox,

)

from library_scanner import (
    SeriesInfo, FasilType, IntroIndex, scan_all_series, resolve_material_durations,
    scan_fasil_root, resolve_fasil_files, scan_intro_folder,
    export_series_scan, import_series_scan, export_fasil_scan, import_fasil_scan,
    export_intro_scan, import_intro_scan,
    series_to_dict, series_from_dict, fasil_to_dict, fasil_from_dict,
)
from models import FasilConfig
from playlist_builder import DailyScheduleBuilder, DEFAULT_CLOSE_TOLERANCE
from audio_utils import get_duration_cached
from grid_widget import SeriesGridWidget, Preview24hGridWidget, series_row_widths, set_canvas_theme
import theme
from ui_kit import (
    PathEdit, FileEdit, Section, ColorPicker, DragTable, FileList, GlowFilter, enable_row_numbers, btn, button_column, fit, tall,
    page_layout, make_page, screen_fraction_height,
)
from about import build_about_html
from exclusions import set_excluded_filenames, get_excluded_filenames




def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class SlotCombo(QComboBox):
    """
    قائمة بمدد الفقرة المسموحة (كلها تقسم 24 ساعة بالتساوي)، بواجهة تشبه الـSpinBox
    (value / setValue / valueChanged) كي لا يتأثر بقية الكود.
    """
    valueChanged = Signal(float)
    CHOICES = [5, 10, 15, 20, 30, 45, 60, 90, 120]

    def __init__(self, parent=None):
        super().__init__(parent)
        for c in self.CHOICES:
            self.addItem(str(c), c)
        self.setCurrentIndex(self.CHOICES.index(30))
        self.currentIndexChanged.connect(lambda _i: self.valueChanged.emit(self.value()))

    def value(self) -> float:
        return float(self.currentData())

    def setValue(self, v: float):
        best = min(self.CHOICES, key=lambda c: abs(c - float(v)))
        self.setCurrentIndex(self.CHOICES.index(best))


def open_folder(path) -> None:
    if path:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def play_file_external(path) -> None:
    """يفتح الملف بالمشغل الافتراضي المرتبط به في نظام التشغيل (مشغل خارجي، وليس مضمّناً بالبرنامج)."""
    if path:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def reveal_in_file_manager(path) -> None:
    """يفتح مستكشف الملفات على موقع الملف، مع تحديده وتمييزه عند الإمكان (ويندوز/ماك)."""
    if not path:
        return
    p = Path(path)
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["explorer", "/select,", str(p)])
            return
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(p)])
            return
    except Exception:
        pass
    open_folder(p.parent)


class NumericTableWidgetItem(QTableWidgetItem):
    """عنصر جدول يُفرَز حسب قيمة رقمية فعلية بدل النص المعروض."""

    def __init__(self, value, display_text: Optional[str] = None):
        super().__init__(display_text if display_text is not None else str(value))
        self.sort_value = value

    def __lt__(self, other):
        ov = getattr(other, "sort_value", None)
        if isinstance(ov, (int, float)) and isinstance(self.sort_value, (int, float)):
            return self.sort_value < ov
        return super().__lt__(other)


def _series_duration_stats(series: SeriesInfo):
    durations = [m.duration_seconds for m in series.materials if m.duration_seconds]
    if not durations:
        return None, None
    return max(durations), sum(durations) / len(durations)


# ---------------------------------------------------------------------------
class LibrarySeriesScanWorker(QThread):
    log_signal = Signal(str)
    progress_signal = Signal(int, int)
    finished_signal = Signal(object, object)  # (List[SeriesInfo] | None, error)

    def __init__(self, root: Path):
        super().__init__()
        self.root = root

    def run(self):
        try:
            self.log_signal.emit("جارٍ فحص بنية مجلد السلاسل...")
            series_list = scan_all_series(self.root, log=self.log_signal.emit)
            self.log_signal.emit(f"تم العثور على {len(series_list)} سلسلة. جارٍ قراءة مدد الملفات...")
            resolve_material_durations(
                series_list, progress=lambda i, t: self.progress_signal.emit(i, t)
            )
            self.log_signal.emit("اكتملت قراءة المدد.")
            self.finished_signal.emit(series_list, None)
        except Exception as e:  # noqa: BLE001
            self.finished_signal.emit(None, str(e))


class LibraryFasilScanWorker(QThread):
    log_signal = Signal(str)
    progress_signal = Signal(int, int)
    finished_signal = Signal(object, object)  # (List[FasilType] | None, error)

    def __init__(self, root: Path):
        super().__init__()
        self.root = root

    def run(self):
        try:
            self.log_signal.emit("جارٍ فحص أنواع الفواصل...")
            fasil_types = scan_fasil_root(self.root)
            self.log_signal.emit(f"تم العثور على {len(fasil_types)} نوع. جارٍ قراءة مدد الملفات...")
            resolve_fasil_files(
                fasil_types, progress=lambda i, t: self.progress_signal.emit(i, t)
            )
            self.log_signal.emit("اكتملت قراءة المدد.")
            self.finished_signal.emit(fasil_types, None)
        except Exception as e:  # noqa: BLE001
            self.finished_signal.emit(None, str(e))


class IntroScanWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(object, object)  # (IntroIndex | None, error)

    def __init__(self, root: Path):
        super().__init__()
        self.root = root

    def run(self):
        try:
            self.finished_signal.emit(scan_intro_folder(self.root), None)
        except Exception as e:  # noqa: BLE001
            self.finished_signal.emit(None, str(e))


class DailyPreviewWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(object, object, object)  # (Dict[date,BuildResult]|None, builder|None, error|None)

    def __init__(self, builder: DailyScheduleBuilder, start: date, end: date):
        super().__init__()
        self.builder = builder
        self.start_date = start
        self.end_date = end
        self.builder.log = self.log_signal.emit

    def run(self):
        try:
            results = self.builder.build_range(self.start_date, self.end_date)
            self.finished_signal.emit(results, self.builder, None)
        except Exception as e:  # noqa: BLE001
            self.finished_signal.emit(None, None, str(e))


class DailyCopyWorker(QThread):
    log_signal = Signal(str)
    progress_signal = Signal(int, int)
    finished_signal = Signal(object, object, object)  # (output_root|None, error|None, report)

    def __init__(self, builder: DailyScheduleBuilder, results, output_root: Path,
                 skip_days=(), replace_existing: bool = True):
        super().__init__()
        self.builder = builder
        self.results = results
        self.output_root = output_root
        self.skip_days = set(skip_days)
        self.replace_existing = replace_existing
        self._cancel = False
        self.builder.log = self.log_signal.emit

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            days = [d for d in sorted(self.results.keys()) if d not in self.skip_days]
            total_files = sum(len(self.results[d].entries) for d in days) or 1
            done = 0
            day_rows = {}
            errors = []
            for day in days:
                if self._cancel:
                    break
                result = self.results[day]

                def _progress(i, t, _done=done, _total_files=total_files):
                    self.progress_signal.emit(_done + i, _total_files)

                _folder, rows, errs = self.builder.copy_day_to_output(
                    day, result, self.output_root, progress=_progress,
                    replace_existing=self.replace_existing, cancel=lambda: self._cancel,
                )
                errors.extend(errs)
                done += len(result.entries)
                if self._cancel:
                    self.log_signal.emit(f"تم إيقاف النسخ أثناء يوم {day.isoformat()} (هذا اليوم غير مكتمل).")
                    break
                day_rows[day] = rows

            workbook_path = None
            if day_rows:
                self.log_signal.emit("جارٍ بناء/تحديث الجدول الشامل (صفحة لكل يوم)...")
                workbook_path = self.builder.write_combined_schedule_workbook(
                    day_rows, self.output_root, results=self.results)
                self.log_signal.emit(f"تم إنشاء الجدول الشامل: {workbook_path}")
            self.finished_signal.emit(self.output_root, None, {
                "errors": errors, "cancelled": self._cancel,
                "days_done": len(day_rows), "workbook": workbook_path,
            })
        except Exception as e:  # noqa: BLE001
            self.finished_signal.emit(None, str(e), None)


# ---------------------------------------------------------------------------
APP_VERSION = "0.9-271026"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"منظّم قوائم التشغيل الصوتية — الإصدار {APP_VERSION}")
        self.resize(1200, 820)
        self.setMinimumSize(380, 320)

        self._loading = True
        self.series_pool: List[SeriesInfo] = []
        self.fasil_types: List[FasilType] = []
        self.intro_index: IntroIndex = IntroIndex()
        self.current_daily_results: Optional[dict] = None
        self.current_daily_builder: Optional[DailyScheduleBuilder] = None
        self._preview_stale = False
        self._state_version = 0
        self._preview_built_version = 0
        self._preview_linked = True
        self._programmatic_preview_range = False
        self._last_grid_range = None
        self._scan_worker = None
        self._fasil_worker = None
        self._intro_worker = None
        self._daily_preview_worker = None
        self._daily_copy_worker = None
        self._sections = {}
        self._theme = theme.palette_for(theme.DEFAULT_MODE, theme.DEFAULT_PRIMARY, theme.DEFAULT_SECONDARY)
        self.settings = QSettings("LessonPlaylistBuilder", "MainWindow")

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(make_page(self._build_library_tab()), "المكتبة")
        self.tabs.addTab(make_page(self._build_arrangement_tab(), 620), "ترتيب 8 ساعات")
        self.tabs.addTab(make_page(self._build_24h_tab(), 620), "ترتيب 24 ساعة")
        self.tabs.addTab(make_page(self._build_preview_tab()), "المعاينة")
        self.tabs.addTab(make_page(self._build_settings_tab(), 420), "الإعدادات")
        self.tabs.addTab(make_page(self._build_about_tab(), 420), "حول")

        # توهج خفيف عند مرور الفأرة على الأزرار وعند التركيز على الحقول
        self._glow = GlowFilter(lambda: self._theme, self)
        QApplication.instance().installEventFilter(self._glow)

        # تحديث لوحة 24 ساعة بعد توقف الكتابة/الإفلات في حقول مسارات البدائل (debounce)
        self._ov_timer = QTimer(self)
        self._ov_timer.setSingleShot(True)
        self._ov_timer.setInterval(450)
        self._ov_timer.timeout.connect(self.on_overrides_changed)
        self.morning_file_edit.pathChanged.connect(lambda *_: self._ov_timer.start())
        self.evening_file_edit.pathChanged.connect(lambda *_: self._ov_timer.start())

        self._wire_invalidation()
        self.load_settings()
        self._loading = False
        QTimer.singleShot(400, self._offer_restore)

    def _wire_invalidation(self):
        """أي تعديل بعد بناء المعاينة يجعلها 'قديمة' ويعطّل زر التأكيد حتى إعادة البناء."""
        self.grid.order_changed.connect(self.invalidate_preview)
        self.fill_gaps_checkbox.toggled.connect(self.invalidate_preview)
        self.tolerance_spin.valueChanged.connect(self.invalidate_preview)
        self.close_tol_spin.valueChanged.connect(self.invalidate_preview)
        self.intro_path_edit.pathChanged.connect(self.invalidate_preview)
        self.preview_start_edit.dateChanged.connect(self._on_preview_range_edited)
        self.preview_end_edit.dateChanged.connect(self._on_preview_range_edited)

    def _on_preview_range_edited(self, *_args):
        if not self._programmatic_preview_range:
            self._preview_linked = False   # عدّله المستخدم يدوياً: لا يتبع مدى اللوحة بعد الآن
        self.invalidate_preview()

    def invalidate_preview(self, *_args):
        if self._loading:
            return
        self._state_version += 1
        if self.current_daily_results and not self._preview_stale:
            self._preview_stale = True
            self.confirm_btn.setEnabled(False)
            self.preview_summary_label.setText(
                "⚠ تغيّر شيء (ترتيب/إعدادات/فواصل...) بعد بناء المعاينة — المعاينة الحالية قديمة، "
                "أعد بناءها قبل التخريج."
            )

    def on_theme_changed(self, *_args):
        """يطبّق (الوضع + اللون الرئيسي + اللون الثانوي) فوراً على كل الواجهة ولوحتي الترتيب."""
        mode = self.mode_combo.currentData() or theme.DEFAULT_MODE
        primary = self.primary_picker.color()
        secondary = self.secondary_picker.color()
        self._theme = theme.palette_for(mode, primary, secondary)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_stylesheet(mode, primary, secondary))
        set_canvas_theme(mode)
        self.grid.apply_theme()
        self.grid24.apply_theme()
        self._refresh_about()

    def _all_workers(self):
        return [getattr(self, n, None) for n in (
            "_scan_worker", "_fasil_worker", "_intro_worker", "_daily_preview_worker", "_daily_copy_worker")]

    def closeEvent(self, event):
        running = [w for w in self._all_workers() if w is not None and w.isRunning()]
        if running:
            reply = QMessageBox.question(
                self, "عملية جارية",
                "توجد عملية (فحص/بناء/نسخ) ما زالت تعمل. هل تريد إيقافها وإغلاق البرنامج؟",
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            for w in running:
                if hasattr(w, "cancel"):
                    w.cancel()
                w.requestInterruption()
            for w in running:
                if not w.wait(4000):
                    w.terminate()
                    w.wait(1000)
        self.save_settings()
        self.autosave_session()
        super().closeEvent(event)

    # =================================================================
    # الحفظ التلقائي واستعادة الجلسة الأخيرة
    # =================================================================
    def _autosave_path(self) -> Path:
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or str(Path.home())
        d = Path(base)
        d.mkdir(parents=True, exist_ok=True)
        return d / "autosave_session.json"

    def autosave_session(self):
        """يحفظ المشروع الكامل تلقائياً (لا يُستبدل حفظ سابق بجلسة فارغة)."""
        try:
            if not (self.series_pool or self.grid.pieces or self.fasil_types or len(self.intro_index)):
                return
            data = self._collect_full_project_dict()
            data["autosaved_at"] = datetime.now().isoformat(timespec="seconds")
            path = self._autosave_path()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001
            pass   # الحفظ التلقائي لا يجوز أن يعطّل الإغلاق

    def _autosave_info(self) -> Optional[str]:
        try:
            p = self._autosave_path()
            if not p.exists():
                return None
            return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            return None

    def _offer_restore(self):
        if os.environ.get("LPB_NO_PROMPT"):
            return
        when = self._autosave_info()
        if not when:
            return
        reply = QMessageBox.question(
            self, "استعادة الجلسة الأخيرة",
            f"يوجد حفظ تلقائي من آخر جلسة ({when}).\nهل تريد استعادة الجلسة الأخيرة؟",
        )
        if reply == QMessageBox.Yes:
            self.restore_last_session(confirm=False)

    def restore_last_session(self, confirm: bool = True):
        p = self._autosave_path()
        if not p.exists():
            QMessageBox.information(self, "لا يوجد حفظ", "لا توجد جلسة محفوظة تلقائياً بعد.")
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("format") != "full_project":
                raise ValueError("ملف الحفظ التلقائي غير صالح.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "فشل الاستعادة", str(e))
            return
        if confirm:
            r = QMessageBox.question(
                self, "تأكيد الاستعادة",
                "سيستبدل هذا كل بيانات المشروع الحالية بالجلسة الأخيرة المحفوظة تلقائياً. هل تريد المتابعة؟",
            )
            if r != QMessageBox.Yes:
                return
        failed = self._apply_project_dict(data)
        msg = "تمت استعادة الجلسة الأخيرة."
        if failed:
            msg += f"\n(تعذّر إعادة وضع {len(failed)} سلسلة لعدم توفر المكان المحفوظ.)"
        self.project_io_label.setText("✅ " + msg)
        QMessageBox.information(self, "اكتملت الاستعادة", msg)

    # =================================================================
    # حفظ واسترجاع المسارات والخيارات بين مرات التشغيل
    # =================================================================
    def save_settings(self):
        s = self.settings
        s.setValue("theme_mode", self.mode_combo.currentData())
        s.setValue("theme_primary", self.primary_picker.color())
        s.setValue("theme_secondary", self.secondary_picker.color())
        s.setValue("window_geometry", self.saveGeometry())
        s.setValue("tab_index", self.tabs.currentIndex())
        for key, sec in self._sections.items():
            s.setValue(f"section_{key}", sec.is_expanded())
        s.setValue("series_root", self.series_root_edit.text())
        s.setValue("fasil_root", self.fasil_root_edit.text())
        s.setValue("intro_folder", self.intro_path_edit.text())
        s.setValue("slot_minutes", self.slot_minutes_spin.value())
        s.setValue("total_hours", self.total_hours_spin.value())
        s.setValue("tolerance", self.tolerance_spin.value())
        s.setValue("close_tolerance", self.close_tol_spin.value())
        s.setValue("output_folder", self.output_edit.text())
        s.setValue("fill_gaps_v2", self.fill_gaps_checkbox.isChecked())

        s.setValue("range_start", self.range_start_edit.date().toString(Qt.ISODate))
        s.setValue("range_end", self.range_end_edit.date().toString(Qt.ISODate))
        s.setValue("day_start_time", self.day_start_time_edit.time().toString("HH:mm"))

        s.setValue("morning_time", self.morning_time_edit.time().toString("HH:mm"))
        s.setValue("morning_file", self.morning_file_edit.text())
        s.setValue("evening_time", self.evening_time_edit.time().toString("HH:mm"))
        s.setValue("evening_file", self.evening_file_edit.text())
        s.setValue("friday_time", self.friday_time_edit.time().toString("HH:mm"))
        friday_paths = [
            str(self.friday_files_list.item(i).data(Qt.UserRole))
            for i in range(self.friday_files_list.count())
        ]
        s.setValue("friday_files", friday_paths)

        s.setValue("preview_start", self.preview_start_edit.date().toString(Qt.ISODate))
        s.setValue("preview_end", self.preview_end_edit.date().toString(Qt.ISODate))

        excluded_names = [
            self.exclusions_table.item(r, 1).text()
            for r in range(self.exclusions_table.rowCount())
            if self.exclusions_table.item(r, 1)
        ]
        s.setValue("excluded_filenames", excluded_names)

    def load_settings(self):
        self._loading = True
        s = self.settings

        def get_str(key: str, default: str = "") -> str:
            val = s.value(key, default)
            return val if isinstance(val, str) else default

        def get_float(key: str, default: float) -> float:
            try:
                return float(s.value(key, default))
            except (TypeError, ValueError):
                return default

        def get_bool(key: str, default: bool) -> bool:
            v = s.value(key, default)
            return str(v).lower() in ("true", "1") if v is not None else default

        def set_date_from_key(edit: QDateEdit, key: str):
            text = get_str(key, "")
            if text:
                d = QDate.fromString(text, Qt.ISODate)
                if d.isValid():
                    edit.setDate(d)

        def set_time_from_key(edit: QTimeEdit, key: str):
            text = get_str(key, "")
            if text:
                t = QTime.fromString(text, "HH:mm")
                if t.isValid():
                    edit.setTime(t)

        # المظهر
        mode = get_str("theme_mode", theme.DEFAULT_MODE)
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(mode)))
        self.mode_combo.blockSignals(False)
        self.primary_picker.set_color(get_str("theme_primary", theme.DEFAULT_PRIMARY))
        self.secondary_picker.set_color(get_str("theme_secondary", theme.DEFAULT_SECONDARY))
        self.on_theme_changed()

        # حالة طيّ الأقسام (الاستثناءات مطوية افتراضياً)
        for key, sec in self._sections.items():
            sec.set_expanded(get_bool(f"section_{key}", key != "exclusions"))

        geo = s.value("window_geometry")
        if geo is not None and not os.environ.get("LPB_NO_PROMPT"):
            try:
                self.restoreGeometry(geo)
            except Exception:  # noqa: BLE001
                pass
        try:
            idx = int(s.value("tab_index", 0))
            if 0 <= idx < self.tabs.count():
                self.tabs.setCurrentIndex(idx)
        except (TypeError, ValueError):
            pass

        self.series_root_edit.setText(get_str("series_root"))
        self.fasil_root_edit.setText(get_str("fasil_root"))
        self.intro_path_edit.setText(get_str("intro_folder"))
        self.slot_minutes_spin.setValue(get_float("slot_minutes", 30))
        self.total_hours_spin.setValue(get_float("total_hours", 8))
        self.tolerance_spin.setValue(get_float("tolerance", 1))
        self.close_tol_spin.setValue(get_float("close_tolerance", DEFAULT_CLOSE_TOLERANCE))
        self.output_edit.setText(get_str("output_folder"))
        self.fill_gaps_checkbox.setChecked(get_bool("fill_gaps_v2", True))

        set_date_from_key(self.range_start_edit, "range_start")
        set_date_from_key(self.range_end_edit, "range_end")
        set_time_from_key(self.day_start_time_edit, "day_start_time")

        for widget in (self.morning_time_edit, self.evening_time_edit, self.friday_time_edit,
                       self.morning_file_edit, self.evening_file_edit, self.friday_files_list):
            widget.blockSignals(True)
        try:
            set_time_from_key(self.morning_time_edit, "morning_time")
            self.morning_file_edit.setText(get_str("morning_file"))
            set_time_from_key(self.evening_time_edit, "evening_time")
            self.evening_file_edit.setText(get_str("evening_file"))
            set_time_from_key(self.friday_time_edit, "friday_time")

            friday_files = s.value("friday_files", []) or []
            if isinstance(friday_files, str):  # قيمة عنصر واحد قد تُرجَع كنص وليس قائمة
                friday_files = [friday_files] if friday_files else []
            for p in friday_files:
                path = Path(p)
                item = QListWidgetItem(path.name)
                item.setData(Qt.UserRole, path)
                self.friday_files_list.addItem(item)
        finally:
            for widget in (self.morning_time_edit, self.evening_time_edit, self.friday_time_edit,
                           self.morning_file_edit, self.evening_file_edit, self.friday_files_list):
                widget.blockSignals(False)

        set_date_from_key(self.preview_start_edit, "preview_start")
        set_date_from_key(self.preview_end_edit, "preview_end")

        excluded_names = s.value("excluded_filenames", []) or []
        if isinstance(excluded_names, str):
            excluded_names = [excluded_names] if excluded_names else []
        for name in excluded_names:
            self._add_exclusion_name(name)
        self.sync_exclusions()

        # تطبيق قيم البدائل المُستعادة على لوحة ترتيب 24 ساعة، وضبط أبعاد اللوحة من الحقول
        self.on_overrides_changed()
        self._sync_grid_from_inputs()
        grid_range = (self.range_start_edit.date(), self.range_end_edit.date())
        self._preview_linked = (
            (self.preview_start_edit.date(), self.preview_end_edit.date()) == grid_range
        )
        self._loading = False

    # =================================================================
    # التبويب 1: المكتبة
    # =================================================================
    def _build_library_tab(self) -> QWidget:
        """صفحة واحدة: السلاسل، البادئات، الفواصل، الاستثناءات (مطوية افتراضياً)، والسجل."""
        w = QWidget()
        lay = page_layout(w)
        self._series_sync_guard = False
        self._exclusions_sync_guard = False

        # شريط التقدم: مخفي إلا أثناء الفحص
        self.library_progress = QProgressBar()
        self.library_progress.setTextVisible(False)
        self.library_progress.setVisible(False)
        lay.addWidget(self.library_progress)

        # ---------------- السلاسل ----------------
        self.series_root_edit = PathEdit(folders_only=True, placeholder="مجلد السلاسل الرئيسي")
        self.series_filter_edit = fit(QLineEdit(), 260)
        self.series_filter_edit.setPlaceholderText("بحث بالاسم أو الرقم أو النوع")
        self.series_filter_edit.textChanged.connect(self.filter_series_table)

        self.series_table = QTableWidget(0, 12)
        self.series_table.setHorizontalHeaderLabels([
            "تحديد", "رقم السلسلة", "العنوان", "عدد المواد", "نوع السلسلة",
            "أطول مدة", "متوسط الطول", "مدة الحجز", "مواد بلا مدة", "البادئة",
            "في لوحة الترتيب", "المجلد",
        ])
        enable_row_numbers(self.series_table)
        sh = self.series_table.horizontalHeader()
        sh.setMinimumSectionSize(36)
        for col in range(12):
            sh.setSectionResizeMode(col, QHeaderView.Stretch if col == 2 else QHeaderView.ResizeToContents)
        self.series_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.series_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.series_table.setSortingEnabled(True)
        tall(self.series_table, 0.35)
        self.series_table.itemSelectionChanged.connect(self.on_series_selection_changed)
        self.series_table.itemChanged.connect(self.on_series_item_changed)
        self.series_table.cellDoubleClicked.connect(self.on_series_table_double_clicked)
        QShortcut(QKeySequence.SelectAll, self.series_table, activated=self.series_table.selectAll)

        self.scan_series_btn = btn("فحص", self.start_scan_series, tooltip="فحص مجلد السلاسل وقراءة مدد المواد")
        series_body = self._split_body(
            self._col(self.series_root_edit, self._row(self.series_filter_edit), self.series_table),
            [
                btn("استعراض", self.browse_series_root, tooltip="اختيار مجلد السلاسل الرئيسي"),
                self.scan_series_btn,
                btn("إلى اللوحة", self.add_selected_series_to_grid,
                    tooltip="إضافة السلاسل المحددة إلى لوحة \"ترتيب 8 ساعات\""),
                btn("تصدير", self.export_series_results,
                    tooltip="حفظ نتائج الفحص في ملف JSON لاستعادتها لاحقاً دون إعادة الفحص"),
                btn("استيراد", self.import_series_results, tooltip="استعادة نتائج فحص محفوظة دون فحص القرص"),
            ],
        )
        lay.addWidget(self._section("series", "السلاسل", series_body, True))

        # ---------------- البادئات ----------------
        self.intro_path_edit = PathEdit(folders_only=True, placeholder="مجلد البادئات")
        self.intro_table = QTableWidget(0, 4)
        self.intro_table.setHorizontalHeaderLabels(["رقم السلسلة", "النوع", "المدة", "الملف"])
        enable_row_numbers(self.intro_table)
        self.intro_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.intro_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.intro_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.intro_table.setSortingEnabled(True)
        tall(self.intro_table)
        self.intro_status_label = QLabel("")
        self.intro_status_label.setObjectName("hint")
        self.scan_intro_btn = btn("فحص", lambda: self.start_scan_intro(auto=False),
                                  tooltip="فحص مجلد البادئات (يُفحص تلقائياً بعد فحص السلاسل)")
        intro_body = self._split_body(
            self._col(self.intro_path_edit, self.intro_status_label, self.intro_table),
            [
                btn("استعراض", self.browse_intro_folder, tooltip="اختيار مجلد البادئات"),
                self.scan_intro_btn,
                btn("تصدير", self.export_intro_results, tooltip="حفظ نتائج فحص البادئات في ملف JSON"),
                btn("استيراد", self.import_intro_results, tooltip="استعادة نتائج فحص البادئات"),
            ],
        )
        lay.addWidget(self._section("intro", "البادئات", intro_body, True))

        # ---------------- الفواصل (جدول واحد) ----------------
        self.fasil_root_edit = PathEdit(folders_only=True, placeholder="مجلد الفواصل الرئيسي")
        self._fasil_hint = QLabel("اسحب ⠿ لترتيب الأولوية  ·  انقر مزدوجاً على البادئة لاختيار ملف  ·  "
                                  "اترك النسبة فارغة أو 0 لعدم استخدام النوع")
        self._fasil_hint.setObjectName("hint")
        self._fasil_hint.setWordWrap(True)
        self.fasil_table = DragTable(0, 8)
        self.fasil_table.setHorizontalHeaderLabels([
            "", "نوع الفاصل", "الملفات", "أطول مدة", "الوجود %", "المدة %", "البادئة", "",
        ])
        self.fasil_table.horizontalHeaderItem(4).setToolTip(
            "نسبة الوجود: احتمال ظهور هذا النوع في أي فقرة (لا يلزم أن يكون المجموع 100%)")
        self.fasil_table.horizontalHeaderItem(5).setToolTip(
            "نسبة المدة: حجم الملف المطلوب نسبة إلى الوقت الواجب تعبئته في الفقرة")
        enable_row_numbers(self.fasil_table)
        hh = self.fasil_table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (0, 2, 3, 4, 5, 7):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.Interactive)
        self.fasil_table.setColumnWidth(6, 170)
        self.fasil_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fasil_table.setSelectionMode(QAbstractItemView.SingleSelection)
        tall(self.fasil_table)
        self.fasil_table.cellDoubleClicked.connect(self.on_fasil_table_double_clicked)
        self.fasil_table.itemChanged.connect(self._on_fasil_item_changed)
        self.fasil_table.rows_moved.connect(self.invalidate_preview)
        QShortcut(QKeySequence.Delete, self.fasil_table, activated=self._clear_fasil_cell)

        self.scan_fasil_btn = btn("فحص", self.start_scan_fasil, tooltip="فحص مجلد الفواصل وقراءة مددها")
        fasil_body = self._split_body(
            self._col(self.fasil_root_edit, self._fasil_hint, self.fasil_table),
            [
                btn("استعراض", self.browse_fasil_root, tooltip="اختيار مجلد الفواصل الرئيسي"),
                self.scan_fasil_btn,
                btn("نسب متساوية", self.generate_equal_ratios,
                    tooltip="توليد نسب وجود ومدة متساوية لكل الأنواع في الجدول"),
                btn("تصدير", self.export_fasil_results, tooltip="حفظ نتائج فحص الفواصل في ملف JSON"),
                btn("استيراد", self.import_fasil_results, tooltip="استعادة نتائج فحص الفواصل"),
            ],
        )
        lay.addWidget(self._section("fasil", "الفواصل", fasil_body, True))

        # ---------------- البدائل (الفجر/المساء/الجمعة) ----------------
        lay.addWidget(self._section("overrides", "البدائل", self._build_overrides_body(), True))

        # ---------------- الاستثناءات (مطوية افتراضياً) ----------------
        lay.addWidget(self._section("exclusions", "الاستثناءات", self._build_exclusions_tab(), False))

        # ---------------- السجل ----------------
        self.library_log = QTextEdit()
        self.library_log.setReadOnly(True)
        tall(self.library_log)
        lay.addWidget(self._section("log", "السجل", self.library_log, True))
        self.fasil_log = self.library_log
        self.fasil_progress = self.library_progress
        lay.addStretch(1)
        return w

    def on_series_table_double_clicked(self, row: int, column: int):
        if column != self.series_table.columnCount() - 1:
            return
        cb_item = self.series_table.item(row, 0)
        series: Optional[SeriesInfo] = cb_item.data(Qt.UserRole) if cb_item else None
        if series is not None:
            open_folder(series.folder)

    # -------- أدوات مشتركة للجداول (فرز/تحديد/مزامنة checkbox) --------
    def _sync_selection_to_checkboxes(self, table: QTableWidget, guard_attr: str):
        if getattr(self, guard_attr):
            return
        setattr(self, guard_attr, True)
        selected_rows = {idx.row() for idx in table.selectedIndexes()}
        for r in range(table.rowCount()):
            cb_item = table.item(r, 0)
            if not cb_item:
                continue
            want = Qt.Checked if r in selected_rows else Qt.Unchecked
            if cb_item.checkState() != want:
                cb_item.setCheckState(want)
        setattr(self, guard_attr, False)

    def _sync_checkbox_to_selection(self, table: QTableWidget, item: QTableWidgetItem, guard_attr: str):
        if item.column() != 0 or getattr(self, guard_attr):
            return
        setattr(self, guard_attr, True)
        sel_model = table.selectionModel()
        model_index = table.model().index(item.row(), 0)
        flag = QItemSelectionModel.Select if item.checkState() == Qt.Checked else QItemSelectionModel.Deselect
        sel_model.select(model_index, flag | QItemSelectionModel.Rows)
        setattr(self, guard_attr, False)

    def browse_series_root(self):
        folder = QFileDialog.getExistingDirectory(self, "اختر مجلد السلاسل الرئيسي")
        if folder:
            self.series_root_edit.setText(folder)

    def browse_fasil_root(self):
        folder = QFileDialog.getExistingDirectory(self, "اختر مجلد الفواصل الرئيسي")
        if folder:
            self.fasil_root_edit.setText(folder)

    def start_scan_series(self):
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        root = self.series_root_edit.text().strip()
        if not root:
            QMessageBox.warning(self, "بيانات ناقصة", "يجب تحديد مجلد السلاسل أولاً.")
            return
        self.library_log.clear()
        self._progress_start()
        self.scan_series_btn.setEnabled(False)
        self._scan_worker = LibrarySeriesScanWorker(Path(root))
        self._scan_worker.log_signal.connect(self.library_log.append)
        self._scan_worker.progress_signal.connect(
            lambda i, t: self.library_progress.setValue(int(i * 100 / max(t, 1)))
        )
        self._scan_worker.finished_signal.connect(self.on_series_scanned)
        self._scan_worker.start()

    def on_series_scanned(self, series_list, error):
        self.scan_series_btn.setEnabled(True)
        self._progress_end()
        if error:
            QMessageBox.critical(self, "خطأ في الفحص", str(error))
            return
        self.series_pool = series_list
        self.rebuild_series_table()
        self.library_log.append(f"✅ إجمالي السلاسل: {len(series_list)}")
        missing = sum(s.missing_duration_count for s in series_list)
        unmatched = sum(s.unmatched_count for s in series_list)
        if missing:
            self.library_log.append(
                f"⚠ {missing} مادة تعذّرت قراءة مدة أي من ملفاتها (انظر عمود \"مواد بلا مدة\")؛ "
                "ستُتخطّى عند البناء."
            )
        if unmatched:
            self.library_log.append(
                f"⚠ {unmatched} ملف صوتي لا يطابق نمط الاسم وتم تجاهله (التفاصيل أعلاه بالسجل)."
            )
        if self.intro_path_edit.text().strip():
            self.start_scan_intro(auto=True)

    # -------- البادئات --------
    def start_scan_intro(self, auto: bool = False):
        if self._intro_worker is not None and self._intro_worker.isRunning():
            return
        root = self.intro_path_edit.text().strip()
        if not root:
            if not auto:
                QMessageBox.warning(self, "بيانات ناقصة", "يجب تحديد مجلد البادئات أولاً.")
            return
        self.library_log.append("جارٍ فحص مجلد البادئات...")
        self.scan_intro_btn.setEnabled(False)
        self._progress_start(busy=True)
        self._intro_worker = IntroScanWorker(Path(root))
        self._intro_worker.finished_signal.connect(self.on_intro_scanned)
        self._intro_worker.start()

    def on_intro_scanned(self, index, error):
        self.scan_intro_btn.setEnabled(True)
        self._progress_end()
        if error:
            QMessageBox.critical(self, "خطأ في فحص البادئات", str(error))
            return
        if self._apply_intro_index(index):
            self._log_intro_summary(index)

    def _log_intro_summary(self, index: IntroIndex):
        dups = sum(1 for v in index.duplicates.values() if v > 1)
        self.library_log.append(f"✅ إجمالي البادئات: {len(index)} (سلسلة/نوع)")
        if dups:
            self.library_log.append(f"⚠ {dups} سلسلة لها أكثر من ملف بادئة، اختير الأكبر حجماً لكل منها.")
        if index.unmatched_count:
            self.library_log.append(
                f"⚠ {index.unmatched_count} ملف في مجلد البادئات لا يطابق نمط الاسم وتم تجاهله. أمثلة: "
                + "، ".join(index.unmatched[:3])
            )
        # بادئات متعددة النوع لنفس الرقم
        by_num = {}
        for (n, t) in index.entries:
            by_num.setdefault(n, []).append(t)
        multi = {n: ts for n, ts in by_num.items() if len(ts) > 1}
        if multi:
            ex = "، ".join(f"{n} ({'/'.join(sorted(ts))})" for n, ts in list(multi.items())[:5])
            self.library_log.append(
                f"ℹ {len(multi)} رقم سلسلة له بادئات بأكثر من نوع (تُطابَق بالرقم + النوع). أمثلة: {ex}"
            )

    def _apply_intro_index(self, index: IntroIndex) -> bool:
        """يعتمد فهرس البادئات الجديد ويعيد حساب عرض المواد في اللوحة (مع تأكيد قبل أي حذف)."""
        moved, lost = self.grid.relayout(intro_index=index, dry_run=True)
        if lost and not self._confirm_loss(
            lost, "إضافة مدد البادئات إلى عرض المواد غيّرت أبعاد بعض السلاسل في لوحة الترتيب."
        ):
            self.library_log.append("تم إلغاء تطبيق نتائج فحص البادئات (لم يتغيّر شيء).")
            return False
        self.intro_index = index
        self.grid.relayout(intro_index=index)
        self.rebuild_intro_table()
        self.rebuild_series_table()
        self.update_grid_summary()
        if moved:
            self.library_log.append(f"ℹ نُقلت {len(moved)} سلسلة لأن عرضها تغيّر بعد احتساب البادئات.")
        self.invalidate_preview()
        return True

    def export_intro_results(self):
        if not len(self.intro_index):
            QMessageBox.information(self, "لا توجد بيانات", "يجب فحص البادئات أولاً قبل التصدير.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "تصدير نتائج فحص البادئات", "intro_scan.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            export_intro_scan(self.intro_index, Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "فشل التصدير", str(e))
            return
        self.library_log.append(f"✅ تم تصدير {len(self.intro_index)} بادئة إلى: {path}")

    def import_intro_results(self):
        path, _ = QFileDialog.getOpenFileName(self, "استيراد نتائج فحص بادئات", "", "JSON (*.json)")
        if not path:
            return
        try:
            index = import_intro_scan(Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "فشل الاستيراد", str(e))
            return
        if self._apply_intro_index(index):
            self.library_log.append(f"✅ تم استيراد {len(index)} بادئة من: {path} (دون إعادة الفحص من القرص)")

    def export_series_results(self):
        if not self.series_pool:
            QMessageBox.information(self, "لا توجد بيانات", "يجب فحص السلاسل أولاً قبل التصدير.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "تصدير نتائج فحص السلاسل", "series_scan.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            export_series_scan(self.series_pool, Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "فشل التصدير", str(e))
            return
        self.library_log.append(f"✅ تم تصدير {len(self.series_pool)} سلسلة إلى: {path}")

    def import_series_results(self):
        path, _ = QFileDialog.getOpenFileName(self, "استيراد نتائج فحص سلاسل", "", "JSON (*.json)")
        if not path:
            return
        try:
            series_list = import_series_scan(Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "فشل الاستيراد", str(e))
            return
        self.series_pool = series_list
        self.rebuild_series_table()
        self.library_log.append(f"✅ تم استيراد {len(series_list)} سلسلة من: {path} (دون إعادة الفحص من القرص)")

    def rebuild_series_table(self):
        table = self.series_table
        table.setSortingEnabled(False)
        table.blockSignals(True)
        table.setRowCount(0)
        slot_seconds = self.slot_minutes_spin.value() * 60
        for s in self.series_pool:
            row = table.rowCount()
            table.insertRow(row)

            cb_item = QTableWidgetItem()
            cb_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            cb_item.setCheckState(Qt.Unchecked)
            cb_item.setData(Qt.UserRole, s)
            table.setItem(row, 0, cb_item)

            try:
                num_value = int(s.series_number)
            except (TypeError, ValueError):
                num_value = s.series_number
            series_num_item = NumericTableWidgetItem(num_value, s.series_number)
            series_num_item.setFlags(series_num_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 1, series_num_item)

            title_item = QTableWidgetItem(s.series_name)
            title_item.setFlags(title_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 2, title_item)

            count_item = NumericTableWidgetItem(s.material_count, str(s.material_count))
            count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 3, count_item)

            type_item = QTableWidgetItem(s.series_type)
            type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 4, type_item)

            max_dur, avg_dur = _series_duration_stats(s)
            max_item = NumericTableWidgetItem(max_dur if max_dur else -1, fmt_duration(max_dur) if max_dur else "—")
            max_item.setFlags(max_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 5, max_item)
            avg_item = NumericTableWidgetItem(avg_dur if avg_dur else -1, fmt_duration(avg_dur) if avg_dur else "—")
            avg_item.setFlags(avg_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 6, avg_item)

            intro_entry, intro_warn = self.intro_index.lookup(s.series_number, s.type_code)
            intro_sec = float(intro_entry[1]) if intro_entry else 0.0
            row_widths = series_row_widths(s, slot_seconds, intro_sec)
            min_w, max_w = min(row_widths), max(row_widths)
            if min_w == max_w:
                reservation_text = fmt_duration(max_w * slot_seconds)
            else:
                reservation_text = f"{fmt_duration(min_w * slot_seconds)} - {fmt_duration(max_w * slot_seconds)}"
            reservation_item = NumericTableWidgetItem(max_w, reservation_text)
            reservation_item.setFlags(reservation_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 7, reservation_item)

            miss = s.missing_duration_count
            miss_item = NumericTableWidgetItem(miss, str(miss) if miss else "0")
            miss_item.setFlags(miss_item.flags() & ~Qt.ItemIsEditable)
            if miss:
                miss_item.setForeground(QBrush(QColor("#e8590c")))
                miss_item.setToolTip("مواد تعذّرت قراءة مدتها؛ ستُتخطّى عند البناء.")
            table.setItem(row, 8, miss_item)

            if intro_entry:
                intro_text, intro_val = f"✔ {fmt_duration(intro_sec)}", intro_sec
            elif intro_warn:
                intro_text, intro_val = "⚠ متعدّدة", -1
            else:
                intro_text, intro_val = "—", -2
            intro_item = NumericTableWidgetItem(intro_val, intro_text)
            intro_item.setFlags(intro_item.flags() & ~Qt.ItemIsEditable)
            if intro_warn:
                intro_item.setToolTip(intro_warn)
            table.setItem(row, 9, intro_item)

            usage = self._count_series_in_grid(s)
            usage_item = NumericTableWidgetItem(usage, str(usage))
            usage_item.setFlags(usage_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 10, usage_item)

            open_item = QTableWidgetItem("📂 فتح المجلد")
            open_item.setFlags(open_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 11, open_item)

        table.blockSignals(False)
        table.setSortingEnabled(True)
        self.filter_series_table()

    def _count_series_in_grid(self, series: SeriesInfo) -> int:
        if not hasattr(self, "grid"):
            return 0
        return sum(1 for p in self.grid.pieces if str(p.series.folder) == str(series.folder))

    def refresh_series_usage_counts(self):
        if not hasattr(self, "grid"):
            return
        counts: dict = {}
        for p in self.grid.pieces:
            key = str(p.series.folder)
            counts[key] = counts.get(key, 0) + 1
        for r in range(self.series_table.rowCount()):
            cb_item = self.series_table.item(r, 0)
            series: Optional[SeriesInfo] = cb_item.data(Qt.UserRole) if cb_item else None
            usage_item = self.series_table.item(r, 10)
            if series is not None and usage_item is not None:
                usage = counts.get(str(series.folder), 0)
                usage_item.sort_value = usage
                usage_item.setText(str(usage))

    def filter_series_table(self):
        text = self.series_filter_edit.text().strip().lower()
        for r in range(self.series_table.rowCount()):
            if not text:
                self.series_table.setRowHidden(r, False)
                if r == self.series_table.rowCount() - 1:
                    self.series_table.renumber()
                continue
            haystack = " ".join(
                self.series_table.item(r, c).text()
                for c in (1, 2, 4)
                if self.series_table.item(r, c)
            ).lower()
            self.series_table.setRowHidden(r, text not in haystack)
        self.series_table.renumber()

    def on_series_selection_changed(self):
        self._sync_selection_to_checkboxes(self.series_table, "_series_sync_guard")

    def on_series_item_changed(self, item: QTableWidgetItem):
        self._sync_checkbox_to_selection(self.series_table, item, "_series_sync_guard")

    def add_selected_series_to_grid(self):
        checked_series: List[SeriesInfo] = []
        for r in range(self.series_table.rowCount()):
            cb_item = self.series_table.item(r, 0)
            if cb_item and cb_item.checkState() == Qt.Checked:
                series = cb_item.data(Qt.UserRole)
                if series is not None:
                    checked_series.append(series)
        if not checked_series:
            QMessageBox.information(self, "لا يوجد تحديد", "حدّد سلسلة واحدة على الأقل من الجدول أولاً (عبر مربع التحديد أو تحديد السطر).")
            return
        failed = []
        placed = 0
        cols = max(1, self.grid.cols)
        self.grid.push_undo()   # لقطة واحدة لكل الدفعة (يتراجع عنها Ctrl+Z دفعة واحدة)
        self.grid.blockSignals(True)
        try:
            for i, series in enumerate(checked_series):
                piece = self.grid.add_series(series, prefer_col=i % cols, record_undo=False)
                if piece is None:
                    failed.append(series.display_name)
                else:
                    placed += 1
        finally:
            self.grid.blockSignals(False)
        if placed == 0:
            self.grid._undo_stack.pop()
        else:
            self.grid.order_changed.emit()
        if failed:
            QMessageBox.warning(
                self, "لا يوجد مكان كافٍ",
                "تعذّر إيجاد مكان فارغ في اللوحة للسلاسل التالية:\n" + "\n".join(failed),
            )
        self.update_grid_summary()
        self.refresh_series_usage_counts()

    def start_scan_fasil(self):
        if self._fasil_worker is not None and self._fasil_worker.isRunning():
            return
        root = self.fasil_root_edit.text().strip()
        if not root:
            QMessageBox.warning(self, "بيانات ناقصة", "يجب تحديد مجلد الفواصل أولاً.")
            return
        self.fasil_log.clear()
        self._progress_start()
        self.scan_fasil_btn.setEnabled(False)
        self._fasil_worker = LibraryFasilScanWorker(Path(root))
        self._fasil_worker.log_signal.connect(self.fasil_log.append)
        self._fasil_worker.progress_signal.connect(
            lambda i, t: self.fasil_progress.setValue(int(i * 100 / max(t, 1)))
        )
        self._fasil_worker.finished_signal.connect(self.on_fasil_scanned)
        self._fasil_worker.start()

    def on_fasil_scanned(self, fasil_types, error):
        self.scan_fasil_btn.setEnabled(True)
        self._progress_end()
        if error:
            QMessageBox.critical(self, "خطأ في الفحص", str(error))
            return
        self.fasil_types = fasil_types
        self.rebuild_fasil_pool_table()
        self.fasil_log.append(f"✅ إجمالي أنواع الفواصل: {len(fasil_types)}")

    def export_fasil_results(self):
        if not self.fasil_types:
            QMessageBox.information(self, "لا توجد بيانات", "يجب فحص الفواصل أولاً قبل التصدير.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "تصدير نتائج فحص الفواصل", "fasil_scan.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            export_fasil_scan(self.fasil_types, Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "فشل التصدير", str(e))
            return
        self.fasil_log.append(f"✅ تم تصدير {len(self.fasil_types)} نوع فاصل إلى: {path}")

    def import_fasil_results(self):
        path, _ = QFileDialog.getOpenFileName(self, "استيراد نتائج فحص فواصل", "", "JSON (*.json)")
        if not path:
            return
        try:
            fasil_types = import_fasil_scan(Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "فشل الاستيراد", str(e))
            return
        self.fasil_types = fasil_types
        self.rebuild_fasil_pool_table()
        self.fasil_log.append(f"✅ تم استيراد {len(fasil_types)} نوع فاصل من: {path} (دون إعادة الفحص من القرص)")

    def rebuild_fasil_pool_table(self):
        """يبني جدول الفواصل الموحّد من نتائج الفحص، محافظاً على النسب والبادئات المضبوطة سابقاً."""
        saved = {}
        for r in range(self.fasil_table.rowCount()):
            it = self.fasil_table.item(r, 1)
            ft = it.data(Qt.UserRole) if it else None
            if ft is not None:
                saved[str(ft.folder)] = (
                    self.fasil_table.item(r, 4).text() if self.fasil_table.item(r, 4) else "",
                    self.fasil_table.item(r, 5).text() if self.fasil_table.item(r, 5) else "",
                    self.fasil_table.item(r, 6).data(Qt.UserRole) if self.fasil_table.item(r, 6) else None,
                )
        n = max(1, len(self.fasil_types))
        default_pct = str(round(100.0 / n, 1))
        self.fasil_table.blockSignals(True)
        self.fasil_table.setRowCount(0)
        for ft in self.fasil_types:
            pres, dur, intro = saved.get(str(ft.folder), (default_pct, default_pct, None))
            self._add_fasil_row(ft, pres, dur, intro)
        self.fasil_table.blockSignals(False)

    def on_fasil_table_double_clicked(self, row: int, column: int):
        it = self.fasil_table.item(row, 1)
        ft: Optional[FasilType] = it.data(Qt.UserRole) if it else None
        if column == 7 and ft is not None:
            open_folder(ft.folder)
        elif column == 6:
            path, _ = QFileDialog.getOpenFileName(self, "اختر ملف بادئة لهذا النوع من الفواصل")
            if not path:
                return
            item = self.fasil_table.item(row, 6)
            p = Path(path)
            item.setData(Qt.UserRole, p)
            item.setText(p.name)
            item.setToolTip(str(p))
            self.invalidate_preview()

    # =================================================================
    # تبويب: الفواصل
    # =================================================================
    def _on_fasil_item_changed(self, item: QTableWidgetItem):
        """يتحقق من النسب (فارغ أو رقم بين 0 و100) ويظلّل الخانة الخاطئة، ويُبطل المعاينة."""
        if item.column() in (4, 5):
            txt = item.text().strip()
            try:
                ok = (not txt) or (0.0 <= float(txt) <= 100.0)
            except ValueError:
                ok = False
            self.fasil_table.blockSignals(True)
            item.setBackground(QBrush() if ok else QBrush(QColor("#7a2a2a")))
            item.setToolTip("" if ok else "قيمة غير صالحة: أدخل رقماً بين 0 و100 (أو اتركها فارغة لتعطيل النوع)")
            self.fasil_table.blockSignals(False)
        self.invalidate_preview()

    # =================================================================
    # تبويب: استثناءات
    # =================================================================
    def _build_exclusions_tab(self) -> QWidget:
        """جسم قسم الاستثناءات (أسماء ملفات تُستبعد من كل فحص)."""
        self.exclusions_filter_edit = fit(QLineEdit(), 260)
        self.exclusions_filter_edit.setPlaceholderText("بحث بالاسم")
        self.exclusions_filter_edit.textChanged.connect(self.filter_exclusions_table)

        self.exclusions_table = QTableWidget(0, 2)
        self.exclusions_table.setHorizontalHeaderLabels(["تحديد", "اسم الملف"])
        enable_row_numbers(self.exclusions_table)
        self.exclusions_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.exclusions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.exclusions_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.exclusions_table.setSortingEnabled(True)
        tall(self.exclusions_table)
        self.exclusions_table.itemSelectionChanged.connect(self.on_exclusions_selection_changed)
        self.exclusions_table.itemChanged.connect(self.on_exclusions_item_changed)
        QShortcut(QKeySequence.SelectAll, self.exclusions_table, activated=self.exclusions_table.selectAll)

        self.exclusions_summary_label = QLabel("لا توجد أسماء مستثناة حالياً.")
        self.exclusions_summary_label.setObjectName("hint")
        self.exclusions_summary_label.setWordWrap(True)
        return self._split_body(
            self._col(self._row(self.exclusions_filter_edit), self.exclusions_table, self.exclusions_summary_label),
            [
                btn("إضافة ملفات", self.add_exclusion_files, tooltip="استثناء أسماء ملف أو عدة ملفات"),
                btn("إضافة مجلد", self.add_exclusion_folder, tooltip="استثناء أسماء كل ملفات مجلد وفروعه"),
                btn("حذف المحدد", self.remove_selected_exclusions),
                btn("مسح الكل", self.clear_exclusions, name="dangerButton"),
                btn("تطبيق الآن", self.apply_exclusions_to_current,
                    tooltip="تطبيق الاستثناءات فوراً على نتائج الفحص الحالية دون إعادة الفحص"),
            ],
        )

    def _exclusion_names_in_table(self) -> set:
        names = set()
        for r in range(self.exclusions_table.rowCount()):
            item = self.exclusions_table.item(r, 1)
            if item:
                names.add(item.text().strip().lower())
        return names

    def _add_exclusion_name(self, name: str):
        name = name.strip()
        if not name:
            return
        if name.lower() in self._exclusion_names_in_table():
            return
        was_sorting = self.exclusions_table.isSortingEnabled()
        self.exclusions_table.setSortingEnabled(False)
        self.exclusions_table.blockSignals(True)
        row = self.exclusions_table.rowCount()
        self.exclusions_table.insertRow(row)
        cb_item = QTableWidgetItem()
        cb_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        cb_item.setCheckState(Qt.Unchecked)
        self.exclusions_table.setItem(row, 0, cb_item)
        name_item = QTableWidgetItem(name)
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        self.exclusions_table.setItem(row, 1, name_item)
        self.exclusions_table.blockSignals(False)
        self.exclusions_table.setSortingEnabled(was_sorting)

    def add_exclusion_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "اختر ملف أو عدة ملفات لاستثناء أسمائها")
        if not paths:
            return
        for p in paths:
            self._add_exclusion_name(Path(p).name)
        self.sync_exclusions()

    def add_exclusion_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "اختر مجلداً لاستثناء كل أسماء ملفاته")
        if not folder:
            return
        count = 0
        for p in Path(folder).rglob("*"):
            if p.is_file():
                self._add_exclusion_name(p.name)
                count += 1
        self.sync_exclusions()
        self.exclusions_summary_label.setText(
            f"تمت إضافة أسماء {count} ملفاً من المجلد المحدد. "
            f"إجمالي الأسماء المستثناة الآن: {self.exclusions_table.rowCount()}."
        )

    def remove_selected_exclusions(self):
        rows = sorted({idx.row() for idx in self.exclusions_table.selectedIndexes()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "لا يوجد تحديد", "حدّد صفاً واحداً على الأقل لحذفه.")
            return
        for r in rows:
            self.exclusions_table.removeRow(r)
        self.sync_exclusions()

    def clear_exclusions(self):
        self.exclusions_table.setRowCount(0)
        self.sync_exclusions()

    def filter_exclusions_table(self):
        text = self.exclusions_filter_edit.text().strip().lower()
        for r in range(self.exclusions_table.rowCount()):
            if not text:
                self.exclusions_table.setRowHidden(r, False)
                if r == self.exclusions_table.rowCount() - 1:
                    self.exclusions_table.renumber()
                continue
            item = self.exclusions_table.item(r, 1)
            self.exclusions_table.setRowHidden(r, bool(item) and text not in item.text().lower())
        self.exclusions_table.renumber()

    def on_exclusions_selection_changed(self):
        self._sync_selection_to_checkboxes(self.exclusions_table, "_exclusions_sync_guard")

    def on_exclusions_item_changed(self, item: QTableWidgetItem):
        self._sync_checkbox_to_selection(self.exclusions_table, item, "_exclusions_sync_guard")

    def _confirm_loss(self, lost: List[str], reason: str) -> bool:
        """يطلب تأكيداً قبل أي حذف تلقائي لسلاسل من لوحة الترتيب. يعيد True إن لم يكن هناك ما يُحذف."""
        if not lost:
            return True
        shown = "\n".join(lost[:15])
        if len(lost) > 15:
            shown += f"\n... و{len(lost) - 15} أخرى"
        r = QMessageBox.question(
            self, "تأكيد قبل الحذف",
            f"{reason}\nسيتعذّر الاحتفاظ بـ {len(lost)} سلسلة وستُحذف من اللوحة:\n{shown}\n\n"
            "هل تريد المتابعة؟ (لن يمكن التراجع عن هذا التغيير)",
        )
        return r == QMessageBox.Yes

    def apply_exclusions_to_current(self):
        """يطبّق قائمة الاستثناءات على نتائج الفحص الحالية بلا إعادة فحص القرص."""
        self.sync_exclusions()
        names = get_excluded_filenames()
        if not names:
            QMessageBox.information(self, "لا استثناءات", "قائمة الاستثناءات فارغة.")
            return
        swapped = removed_mats = 0
        emptied = []
        for ser in self.series_pool:
            kept = []
            for m in ser.materials:
                if m.path.name.lower() in names:
                    alt = next((a for a in m.alt_paths if a.name.lower() not in names), None)
                    d = get_duration_cached(alt) if alt else None
                    if alt and d:
                        m.path, m.duration_seconds = alt, d
                        m.alt_paths = [a for a in m.alt_paths if a != alt and a.name.lower() not in names]
                        swapped += 1
                        kept.append(m)
                    else:
                        removed_mats += 1
                    continue
                m.alt_paths = [a for a in m.alt_paths if a.name.lower() not in names]
                kept.append(m)
            ser.materials = kept
            if not kept:
                emptied.append(ser)
        removed_fasil = 0
        for ft in self.fasil_types:
            before = len(ft.files)
            ft.files = [(p, d) for p, d in ft.files if p.name.lower() not in names]
            removed_fasil += before - len(ft.files)
        removed_intro = self.intro_index.remove_filenames(names)
        self.rebuild_intro_table()

        emptied_folders = {str(x.folder) for x in emptied}
        if emptied:
            self.series_pool = [x for x in self.series_pool if str(x.folder) not in emptied_folders]
            for p in list(self.grid.pieces):
                if str(p.series.folder) in emptied_folders:
                    self.grid.remove_piece(p)
        moved, lost = self.grid.relayout(dry_run=True)
        if lost and not self._confirm_loss(lost, "تغيّر عدد/أطوال مواد بعض السلاسل بعد تطبيق الاستثناءات."):
            lost = []
        self.grid.relayout()
        self.rebuild_series_table()
        self.rebuild_fasil_pool_table()
        self.update_grid_summary()
        self.invalidate_preview()
        msg = (f"استُبدلت {swapped} مادة بنسخة بديلة، وأُزيلت {removed_mats} مادة، "
               f"و{removed_fasil} ملف فاصل، و{removed_intro} بادئة، وأُزيلت {len(emptied)} سلسلة فارغة.")
        self.exclusions_summary_label.setText(msg)
        QMessageBox.information(self, "تم التطبيق", msg)

    def sync_exclusions(self):
        """يُحدّث قائمة الاستثناء العالمية (المستخدمة في كل عمليات الفحص) من محتوى الجدول الحالي."""
        names = [
            self.exclusions_table.item(r, 1).text()
            for r in range(self.exclusions_table.rowCount())
            if self.exclusions_table.item(r, 1)
        ]
        set_excluded_filenames(names)
        self.exclusions_summary_label.setText(f"عدد الأسماء المستثناة حالياً: {len(names)}.")

    # =================================================================
    # التبويب 2: الترتيب (Block Puzzle)
    # =================================================================
    def _build_arrangement_tab(self) -> QWidget:
        w = QWidget()
        lay = page_layout(w)

        today = QDate.currentDate()
        first_of_month = QDate(today.year(), today.month(), 1)
        last_of_month = QDate(today.year(), today.month(), first_of_month.daysInMonth())

        self.range_start_edit = fit(QDateEdit(first_of_month))
        self.range_start_edit.setCalendarPopup(True)
        self.range_start_edit.setDisplayFormat("yyyy-MM-dd")
        self.range_start_edit.dateChanged.connect(self.on_date_range_changed)
        self.range_end_edit = fit(QDateEdit(last_of_month))
        self.range_end_edit.setCalendarPopup(True)
        self.range_end_edit.setDisplayFormat("yyyy-MM-dd")
        self.range_end_edit.dateChanged.connect(self.on_date_range_changed)
        self.day_start_time_edit = fit(QTimeEdit(QTime(8, 0)))
        self.day_start_time_edit.timeChanged.connect(self.on_day_start_changed)
        self.fill_gaps_checkbox = QCheckBox("ملء الفراغات بفواصل")
        self.fill_gaps_checkbox.setChecked(True)
        self.fill_gaps_checkbox.setToolTip(
            "أي فراغ بين السلاسل في اللوحة يُملأ عند البناء بفواصل بدل تجاوزه، "
            "فتحصل على قائمة تشغيل متواصلة")

        controls = self._row(
            QLabel("من"), self.range_start_edit, QLabel("إلى"), self.range_end_edit,
            QLabel("بداية اليوم"), self.day_start_time_edit, self.fill_gaps_checkbox,
            spacing=10,
        )

        self.grid = SeriesGridWidget(cols_per_day=16, slot_minutes=30, day_start_minutes=8 * 60)
        self.grid.setMinimumHeight(max(450, screen_fraction_height(0.5)))
        self.grid.set_date_range(first_of_month.toPython(), last_of_month.toPython())
        self.grid.order_changed.connect(self.update_grid_summary)
        self.grid.order_changed.connect(self.refresh_series_usage_counts)

        self.grid_summary_label = QLabel("لم تتم إضافة أي سلسلة بعد.")
        self.grid_summary_label.setObjectName("hint")

        body = self._split_body(
            self._col(controls, self.grid, self.grid_summary_label),
            [
                btn("مسح الكل", self.clear_grid, name="dangerButton"),
                btn("إزالة المحدد", self.remove_selected_piece, tooltip="إزالة الشكل المحدد (Delete)"),
                btn("ترتيب عشوائي", self.randomize_grid_arrangement,
                    tooltip="إعادة توزيع كل السلاسل عشوائياً بلا فراغات"),
                btn("تراجع", self.undo_grid, tooltip="التراجع عن آخر تعديل (Ctrl+Z)"),
            ],
        )
        lay.addWidget(body, 1)
        return w

    def randomize_grid_arrangement(self):
        if not self.grid.pieces:
            QMessageBox.information(self, "اللوحة فارغة", "لا توجد سلاسل في اللوحة لإعادة ترتيبها.")
            return
        failed = self.grid.randomize_arrangement()
        if failed:
            QMessageBox.warning(
                self, "تعذّر الترتيب العشوائي",
                "لم يتوفر مكان كافٍ للسلاسل التالية، لذلك أُبقي الترتيب الحالي كما هو دون أي تغيير:\n"
                + "\n".join(failed),
            )
        self.update_grid_summary()

    def undo_grid(self):
        if not self.grid.undo_last():
            QMessageBox.information(self, "لا يوجد تراجع", "لا توجد عملية يمكن التراجع عنها.")
        self.update_grid_summary()

    def on_date_range_changed(self, *_args):
        if self._loading:
            return
        start = self.range_start_edit.date().toPython()
        end = self.range_end_edit.date().toPython()
        if end < start:
            self.grid_summary_label.setText("⚠ تاريخ النهاية يسبق تاريخ البداية — لم يُطبَّق التغيير بعد.")
            return
        moved, lost = self.grid.preview_date_range(start, end)
        if lost and not self._confirm_loss(lost, "المدى الجديد للأيام أقصر من أن يتسع لهذه السلاسل."):
            self._restore_range_edits()
            return
        self.grid.set_date_range(start, end)
        self._last_grid_range = (self.range_start_edit.date(), self.range_end_edit.date())
        if self._preview_linked:
            self._programmatic_preview_range = True
            try:
                self.preview_start_edit.setDate(self.range_start_edit.date())
                self.preview_end_edit.setDate(self.range_end_edit.date())
            finally:
                self._programmatic_preview_range = False
        self.update_grid_summary()
        self.invalidate_preview()

    def _restore_range_edits(self):
        if not self._last_grid_range:
            return
        for edit, d in ((self.range_start_edit, self._last_grid_range[0]),
                        (self.range_end_edit, self._last_grid_range[1])):
            edit.blockSignals(True)
            edit.setDate(d)
            edit.blockSignals(False)

    def on_day_start_changed(self):
        t = self.day_start_time_edit.time()
        self.grid.set_day_start_minutes(t.hour() * 60 + t.minute())

    def clear_grid(self):
        if self.grid.pieces:
            r = QMessageBox.question(self, "مسح الكل", "سيُمسح كل ما في لوحة الترتيب (يمكن التراجع بـ Ctrl+Z). متابعة؟")
            if r != QMessageBox.Yes:
                return
        self.grid.clear_all(record_undo=True)
        self.update_grid_summary()

    def remove_selected_piece(self):
        self.grid.remove_selected_pieces()
        self.update_grid_summary()

    def update_grid_summary(self):
        ordered = self.grid.get_ordered_series()
        total_materials = sum(s.material_count for s in ordered)
        self.grid_summary_label.setText(
            f"عدد السلاسل في اللوحة: {len(ordered)}  |  إجمالي المواد: {total_materials}"
        )

    # =================================================================
    # التبويب 3: ترتيب 24 ساعة (عرض فقط + البدائل)
    # =================================================================
    def _build_overrides_body(self) -> QWidget:
        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)

        self.morning_time_edit = fit(QTimeEdit(QTime(6, 0)))
        self.morning_time_edit.timeChanged.connect(self.on_overrides_changed)
        self.morning_file_edit = FileEdit(placeholder="ملف بديل الفجر", duration_fn=get_duration_cached)
        self.evening_time_edit = fit(QTimeEdit(QTime(20, 0)))
        self.evening_time_edit.timeChanged.connect(self.on_overrides_changed)
        self.evening_file_edit = FileEdit(placeholder="ملف بديل المساء", duration_fn=get_duration_cached)
        self.friday_time_edit = fit(QTimeEdit(QTime(12, 0)))
        self.friday_time_edit.timeChanged.connect(self.on_overrides_changed)

        self.friday_files_list = FileList(placeholder="ملفات بديل الجمعة")
        self.friday_files_list.files_dropped.connect(self._friday_files_dropped)
        self.friday_files_list.reordered.connect(self.on_overrides_changed)
        self.friday_total_label = QLabel("إجمالي مدة ملفات الجمعة: 0:00")
        self.friday_total_label.setObjectName("hint")

        def lbl(t):
            x = QLabel(t)
            x.setFixedWidth(60)
            return x

        g.addWidget(btn("استعراض", self.browse_morning_file), 0, 0)
        g.addWidget(lbl("الفجر"), 0, 1)
        g.addWidget(self.morning_time_edit, 0, 2)
        g.addWidget(self.morning_file_edit, 0, 3)
        g.addWidget(btn("استعراض", self.browse_evening_file), 1, 0)
        g.addWidget(lbl("المساء"), 1, 1)
        g.addWidget(self.evening_time_edit, 1, 2)
        g.addWidget(self.evening_file_edit, 1, 3)
        g.addWidget(btn("إضافة", self.add_friday_files, tooltip="إضافة ملف أو عدة ملفات لبدائل الجمعة"), 2, 0)
        g.addWidget(lbl("الجمعة"), 2, 1, Qt.AlignTop)
        g.addWidget(self.friday_time_edit, 2, 2, Qt.AlignTop)
        g.addWidget(btn("حذف", self.remove_friday_file, tooltip="حذف الملفات المحددة من بدائل الجمعة"), 3, 0)
        g.addWidget(self.friday_total_label, 3, 3)
        g.addWidget(self.friday_files_list, 2, 3, Qt.AlignTop)
        g.setColumnStretch(4, 1)
        holder = QWidget()
        holder.setLayout(g)
        return holder


    def _build_24h_tab(self) -> QWidget:
        w = QWidget()
        lay = page_layout(w)

        self.grid24 = Preview24hGridWidget(self.grid)
        self.grid24.setMinimumHeight(max(450, screen_fraction_height(0.5)))
        lay.addWidget(self.grid24, 1)
        return w

    def browse_morning_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "اختر ملف بديل الفجر")
        if path:
            self.morning_file_edit.setText(path)
            self.on_overrides_changed()

    def browse_evening_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "اختر ملف بديل المساء")
        if path:
            self.evening_file_edit.setText(path)
            self.on_overrides_changed()

    def add_friday_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "اختر ملف/ملفات بدائل الجمعة")
        for p in paths:
            item = QListWidgetItem(Path(p).name)
            item.setData(Qt.UserRole, Path(p))
            self.friday_files_list.addItem(item)
        if paths:
            self.on_overrides_changed()

    def remove_friday_file(self):
        for item in self.friday_files_list.selectedItems():
            self.friday_files_list.takeItem(self.friday_files_list.row(item))
        self.on_overrides_changed()

    def on_overrides_changed(self, *_args):
        self.friday_files_list.fit_height()
        if not hasattr(self, "grid24"):
            return
        morning_path_text = self.morning_file_edit.text().strip()
        morning_path = Path(morning_path_text) if morning_path_text else None
        morning_dur = (get_duration_cached(morning_path) or 0.0) if morning_path else 0.0
        t1 = self.morning_time_edit.time()
        self.grid24.set_morning(t1.hour() * 60 + t1.minute(), morning_path, morning_dur, refresh_now=False)

        evening_path_text = self.evening_file_edit.text().strip()
        evening_path = Path(evening_path_text) if evening_path_text else None
        evening_dur = (get_duration_cached(evening_path) or 0.0) if evening_path else 0.0
        t2 = self.evening_time_edit.time()
        self.grid24.set_evening(t2.hour() * 60 + t2.minute(), evening_path, evening_dur, refresh_now=False)

        friday_files: List[Tuple[Path, float]] = []
        total_dur = 0.0
        for i in range(self.friday_files_list.count()):
            item = self.friday_files_list.item(i)
            p: Path = item.data(Qt.UserRole)
            dur = (get_duration_cached(p) or 0.0) if p else 0.0
            friday_files.append((p, dur))
            total_dur += dur
            item.setText(f"{p.name}   ({fmt_duration(dur)})" if dur else f"{p.name}   (تعذّرت قراءة المدة)")
        self.friday_total_label.setText(f"إجمالي مدة ملفات الجمعة: {fmt_duration(total_dur)}")
        t3 = self.friday_time_edit.time()
        self.grid24.set_friday(t3.hour() * 60 + t3.minute(), friday_files, refresh_now=False)

        self.grid24.refresh()
        self.invalidate_preview()

    # =================================================================
    # التبويب 3: الفواصل (نسبتان)
    # =================================================================
    # =================================================================
    # الإعدادات العامة (تبويب الإعدادات)
    # =================================================================
    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        lay = page_layout(w)

        def form_row(label: str, control: QWidget, tip: str = "") -> QWidget:
            lab = QLabel(label)
            lab.setFixedWidth(210)
            if tip:
                lab.setToolTip(tip)
                control.setToolTip(tip)
            return self._row(lab, control, spacing=12)

        # ---- المظهر ----
        self.mode_combo = fit(QComboBox())
        self.mode_combo.addItem("داكن", "dark")
        self.mode_combo.addItem("فاتح", "light")
        self.mode_combo.currentIndexChanged.connect(self.on_theme_changed)
        self.primary_picker = ColorPicker(theme.DEFAULT_PRIMARY)
        self.primary_picker.colorChanged.connect(self.on_theme_changed)
        self.secondary_picker = ColorPicker(theme.DEFAULT_SECONDARY)
        self.secondary_picker.colorChanged.connect(self.on_theme_changed)
        look = self._col(
            form_row("الوضع", self.mode_combo),
            form_row("اللون الرئيسي", self.primary_picker, "الأزرار والتبويب المحدد وإطار الحقول والتوهج"),
            form_row("اللون الثانوي", self.secondary_picker, "عناوين الأقسام والتحديد وشريط التقدم وزر التخريج"),
        )
        lay.addWidget(self._section("set_look", "المظهر", look, True))

        # ---- عام ----
        self.slot_minutes_spin = SlotCombo()
        fit(self.slot_minutes_spin)
        self.slot_minutes_spin.valueChanged.connect(self.on_slot_or_day_changed)

        self.total_hours_spin = fit(QDoubleSpinBox())
        self.total_hours_spin.setRange(0.5, 48)
        self.total_hours_spin.setValue(8)
        self.total_hours_spin.setKeyboardTracking(False)   # لا تُطبَّق القيم الوسيطة أثناء الكتابة
        self.total_hours_spin.valueChanged.connect(self.on_slot_or_day_changed)

        self.tolerance_spin = fit(QDoubleSpinBox())
        self.tolerance_spin.setRange(0.1, 90)
        self.tolerance_spin.setDecimals(1)
        self.tolerance_spin.setValue(1)
        self.tolerance_spin.setKeyboardTracking(False)

        self.close_tol_spin = fit(QDoubleSpinBox())
        self.close_tol_spin.setRange(0.1, 10)
        self.close_tol_spin.setDecimals(1)
        self.close_tol_spin.setValue(DEFAULT_CLOSE_TOLERANCE)
        self.close_tol_spin.setKeyboardTracking(False)

        general = self._col(
            form_row("مدة الفقرة (دقيقة)", self.slot_minutes_spin, "مدة الفقرة الزمنية لكل عمود في لوحة الترتيب"),
            form_row("طول اليوم المعروض (ساعة)", self.total_hours_spin,
                     "طول اليوم في لوحة \"ترتيب 8 ساعات\" (يحدّد عدد أعمدتها)"),
            form_row("تسامح مدة الفواصل (%)", self.tolerance_spin,
                     "نافذة حجم الملف حول الهدف (تتوسّع تلقائياً إن لم يوجد ملف)"),
            form_row("دقة إغلاق الفقرة (ثانية)", self.close_tol_spin,
                     "أقصى انحراف مقبول عن نهاية الفقرة بالثواني"),
        )
        lay.addWidget(self._section("set_general", "عام", general, True))

        # ---- المشروع ----
        self.project_io_label = QLabel("")
        self.project_io_label.setObjectName("hint")
        self.project_io_label.setWordWrap(True)
        project = self._split_body(
            self._col(self.project_io_label),
            [
                btn("تصدير الكل", self.export_full_project, name="secondaryButton",
                    tooltip="حفظ كل ما في البرنامج (الفحوص، اللوحة، البدائل، النسب، الاستثناءات، الإعدادات) في ملف واحد"),
                btn("استيراد الكل", self.import_full_project, tooltip="استعادة مشروع كامل من ملف"),
                btn("آخر جلسة", lambda: self.restore_last_session(confirm=True),
                    tooltip="استعادة المشروع المحفوظ تلقائياً عند آخر إغلاق"),
            ],
        )
        lay.addWidget(self._section("set_project", "المشروع", project, True))
        lay.addStretch(1)
        return w

    def _cols_from_settings(self) -> int:
        slot_seconds = self.slot_minutes_spin.value() * 60
        return max(1, round(self.total_hours_spin.value() * 3600 / slot_seconds))

    def _sync_grid_from_inputs(self):
        """يضبط أبعاد اللوحة (فقرة/أعمدة/تواريخ/بداية اليوم) من قيم الحقول الحالية دون أسئلة."""
        self.grid.relayout(slot_minutes=int(round(self.slot_minutes_spin.value())),
                           cols=self._cols_from_settings())
        t = self.day_start_time_edit.time()
        self.grid.set_day_start_minutes(t.hour() * 60 + t.minute())
        st = self.range_start_edit.date()
        en = self.range_end_edit.date()
        if en >= st:
            self.grid.set_date_range(st.toPython(), en.toPython())
        self._last_grid_range = (st, en)

    def _revert_layout_inputs(self):
        self.slot_minutes_spin.blockSignals(True)
        self.total_hours_spin.blockSignals(True)
        self.slot_minutes_spin.setValue(self.grid.slot_minutes)
        self.total_hours_spin.setValue(self.grid.cols * self.grid.slot_minutes / 60.0)
        self.slot_minutes_spin.blockSignals(False)
        self.total_hours_spin.blockSignals(False)

    def on_slot_or_day_changed(self, *_args):
        if self._loading or not hasattr(self, "grid"):
            return
        slot = int(round(self.slot_minutes_spin.value()))
        cols = self._cols_from_settings()
        if slot == self.grid.slot_minutes and cols == self.grid.cols:
            return
        moved, lost = self.grid.relayout(slot_minutes=slot, cols=cols, dry_run=True)
        if lost and not self._confirm_loss(lost, "الإعداد الجديد (مدة الفقرة/طول اليوم) يغيّر أبعاد اللوحة."):
            self._revert_layout_inputs()
            return
        self.grid.relayout(slot_minutes=slot, cols=cols)
        self.rebuild_series_table()
        self.update_grid_summary()
        if moved:
            self.grid_summary_label.setText(
                self.grid_summary_label.text() + f"  |  نُقلت {len(moved)} سلسلة لتناسب الأبعاد الجديدة"
            )
        self.invalidate_preview()

    # =================================================================
    # تصدير/استيراد المشروع الكامل (كل التبويبات بزر واحد)
    # =================================================================
    def _collect_full_project_dict(self) -> dict:
        friday_files = []
        for i in range(self.friday_files_list.count()):
            item = self.friday_files_list.item(i)
            p = item.data(Qt.UserRole)
            if p:
                friday_files.append(str(p))

        fasil_configs = []
        for r in range(self.fasil_table.rowCount()):
            name_item = self.fasil_table.item(r, 1)
            ft = name_item.data(Qt.UserRole) if name_item else None
            if ft is None:
                continue
            presence_item = self.fasil_table.item(r, 4)
            duration_item = self.fasil_table.item(r, 5)
            intro_item = self.fasil_table.item(r, 6)
            intro_path = intro_item.data(Qt.UserRole) if intro_item else None
            fasil_configs.append({
                "fasil_folder": str(ft.folder),
                "duration_pct": duration_item.text() if duration_item else "",
                "presence_pct": presence_item.text() if presence_item else "",
                "intro_path": str(intro_path) if intro_path else None,
            })

        grid_pieces = [
            {"series_folder": str(p.series.folder), "start_row": p.start_row, "col": p.col}
            for p in self.grid.pieces
        ]

        excluded_names = [
            self.exclusions_table.item(r, 1).text()
            for r in range(self.exclusions_table.rowCount())
            if self.exclusions_table.item(r, 1)
        ]

        return {
            "format": "full_project",
            "version": 1,
            "series_scan": {"series": [series_to_dict(s) for s in self.series_pool]},
            "fasil_scan": {"fasil_types": [fasil_to_dict(ft) for ft in self.fasil_types]},
            "intro_scan": self.intro_index.to_dict(),
            "grid8": {
                "date_start": self.range_start_edit.date().toString(Qt.ISODate),
                "date_end": self.range_end_edit.date().toString(Qt.ISODate),
                "day_start_time": self.day_start_time_edit.time().toString("HH:mm"),
                "slot_minutes": self.slot_minutes_spin.value(),
                "total_hours": self.total_hours_spin.value(),
                "pieces": grid_pieces,
            },
            "grid24_overrides": {
                "morning_time": self.morning_time_edit.time().toString("HH:mm"),
                "morning_file": self.morning_file_edit.text(),
                "evening_time": self.evening_time_edit.time().toString("HH:mm"),
                "evening_file": self.evening_file_edit.text(),
                "friday_time": self.friday_time_edit.time().toString("HH:mm"),
                "friday_files": friday_files,
            },
            "fasil_configs": fasil_configs,
            "exclusions": excluded_names,
            "settings": {
                "series_root": self.series_root_edit.text(),
                "fasil_root": self.fasil_root_edit.text(),
                "intro_folder": self.intro_path_edit.text(),
                "output_folder": self.output_edit.text(),
                "tolerance": self.tolerance_spin.value(),
                "close_tolerance": self.close_tol_spin.value(),
                "fill_gaps": self.fill_gaps_checkbox.isChecked(),
                "preview_start": self.preview_start_edit.date().toString(Qt.ISODate),
                "preview_end": self.preview_end_edit.date().toString(Qt.ISODate),
            },
        }

    def export_full_project(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "تصدير المشروع الكامل", "مشروع_كامل.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            data = self._collect_full_project_dict()
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "فشل التصدير", str(e))
            return
        self.project_io_label.setText(f"✅ تم تصدير المشروع الكامل إلى: {path}")

    def import_full_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "استيراد مشروع كامل", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("format") != "full_project":
                raise ValueError("هذا الملف ليس ملف تصدير مشروع كامل صالحاً.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "فشل الاستيراد", str(e))
            return

        reply = QMessageBox.question(
            self, "تأكيد الاستيراد",
            "سيستبدل هذا كل بيانات المشروع الحالية (السلاسل، الفواصل، لوحة الترتيب، "
            "البدائل، الاستثناءات، والإعدادات). هل تريد المتابعة؟",
        )
        if reply != QMessageBox.Yes:
            return
        failed_pieces = self._apply_project_dict(data)
        msg = f"تم استيراد المشروع الكامل من: {path}"
        if failed_pieces:
            msg += f"\n(تعذّر إعادة وضع {len(failed_pieces)} سلسلة لعدم توفر المكان المحفوظ؛ ربما تغيّر المدى الزمني أو الإعدادات.)"
        self.project_io_label.setText(msg)
        QMessageBox.information(self, "اكتمل الاستيراد", "تم استيراد المشروع الكامل بنجاح.")

    def _apply_project_dict(self, data: dict) -> List[str]:
        """يطبّق قاموس مشروع كامل على الواجهة (دون فحص وجود الملفات عمداً). يعيد أسماء ما تعذّر وضعه."""
        self._loading = True
        try:
            # 1) الإعدادات والمسارات
            settings = data.get("settings", {})
            self.series_root_edit.setText(settings.get("series_root", ""))
            self.fasil_root_edit.setText(settings.get("fasil_root", ""))
            self.intro_path_edit.setText(settings.get("intro_folder", ""))
            self.output_edit.setText(settings.get("output_folder", ""))
            if "tolerance" in settings:
                self.tolerance_spin.setValue(float(settings["tolerance"]))
            if "close_tolerance" in settings:
                self.close_tol_spin.setValue(float(settings["close_tolerance"]))
            self.fill_gaps_checkbox.setChecked(bool(settings.get("fill_gaps", True)))

            def set_date(edit, text):
                d = QDate.fromString(text or "", Qt.ISODate)
                if d.isValid():
                    edit.setDate(d)

            set_date(self.preview_start_edit, settings.get("preview_start"))
            set_date(self.preview_end_edit, settings.get("preview_end"))

            # 2) نتائج الفحص (سلاسل/فواصل/بادئات)
            self.series_pool = [series_from_dict(d) for d in data.get("series_scan", {}).get("series", [])]
            self.fasil_types = [fasil_from_dict(d) for d in data.get("fasil_scan", {}).get("fasil_types", [])]
            self.intro_index = IntroIndex.from_dict(data["intro_scan"]) if data.get("intro_scan") else IntroIndex()
            self.grid.clear_all()
            self.grid.clear_undo()
            self.grid.intro_index = self.intro_index
            self.rebuild_intro_table()

            # 3) إعدادات اللوحة
            grid8 = data.get("grid8", {})
            if grid8.get("slot_minutes"):
                self.slot_minutes_spin.setValue(float(grid8["slot_minutes"]))
            if grid8.get("total_hours"):
                self.total_hours_spin.setValue(float(grid8["total_hours"]))
            set_date(self.range_start_edit, grid8.get("date_start"))
            set_date(self.range_end_edit, grid8.get("date_end"))
            if grid8.get("day_start_time"):
                t = QTime.fromString(grid8["day_start_time"], "HH:mm")
                if t.isValid():
                    self.day_start_time_edit.setTime(t)
            self._sync_grid_from_inputs()

            self.rebuild_series_table()
            self.rebuild_fasil_pool_table()

            series_by_folder = {str(s.folder): s for s in self.series_pool}
            failed_pieces = []
            self.grid.blockSignals(True)
            try:
                for pd in grid8.get("pieces", []):
                    series = series_by_folder.get(pd.get("series_folder"))
                    if series is None:
                        continue
                    piece = self.grid.place_series_at(series, pd.get("start_row", 0), pd.get("col", 0))
                    if piece is None:
                        failed_pieces.append(series.display_name)
            finally:
                self.grid.blockSignals(False)
            self.grid.order_changed.emit()

            # 4) بدائل ترتيب 24 ساعة
            ov = data.get("grid24_overrides", {})
            override_widgets = (self.morning_time_edit, self.evening_time_edit, self.friday_time_edit,
                                self.morning_file_edit, self.evening_file_edit, self.friday_files_list)
            for widget in override_widgets:
                widget.blockSignals(True)
            try:
                for edit, key in ((self.morning_time_edit, "morning_time"), (self.evening_time_edit, "evening_time"),
                                  (self.friday_time_edit, "friday_time")):
                    if ov.get(key):
                        t = QTime.fromString(ov[key], "HH:mm")
                        if t.isValid():
                            edit.setTime(t)
                self.morning_file_edit.setText(ov.get("morning_file", ""))
                self.evening_file_edit.setText(ov.get("evening_file", ""))
                self.friday_files_list.clear()
                for p in ov.get("friday_files", []):
                    path_obj = Path(p)
                    item = QListWidgetItem(path_obj.name)
                    item.setData(Qt.UserRole, path_obj)
                    self.friday_files_list.addItem(item)
            finally:
                for widget in override_widgets:
                    widget.blockSignals(False)
            self.on_overrides_changed()

            # 5) جدول الفواصل الموحّد: المضبوطة أولاً بترتيبها المحفوظ، ثم الباقي بلا نسب (غير مستخدم)
            fasil_by_folder = {str(ft.folder): ft for ft in self.fasil_types}
            ordered = []
            for fc in data.get("fasil_configs", []):
                ft = fasil_by_folder.get(fc.get("fasil_folder"))
                if ft is not None:
                    ordered.append((ft, fc))
            used_ids = {id(ft) for ft, _fc in ordered}
            for ft in self.fasil_types:
                if id(ft) not in used_ids:
                    ordered.append((ft, {}))
            self.fasil_table.blockSignals(True)
            try:
                self.fasil_table.setRowCount(0)
                for ft, fc in ordered:
                    self._add_fasil_row(ft, fc.get("presence_pct", ""), fc.get("duration_pct", ""),
                                        fc.get("intro_path"))
            finally:
                self.fasil_table.blockSignals(False)

            # 6) الاستثناءات
            self.exclusions_table.setRowCount(0)
            for name in data.get("exclusions", []):
                self._add_exclusion_name(name)
            self.sync_exclusions()

            self.update_grid_summary()
            self.refresh_series_usage_counts()
            grid_range = (self.range_start_edit.date(), self.range_end_edit.date())
            self._preview_linked = (
                (self.preview_start_edit.date(), self.preview_end_edit.date()) == grid_range
            )
            self.current_daily_results = None
            self._preview_stale = False
            self.confirm_btn.setEnabled(False)
        finally:
            self._loading = False
        self._state_version += 1
        return failed_pieces

    def browse_intro_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "اختر مجلد البادئات")
        if folder:
            self.intro_path_edit.setText(folder)

    def browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "اختر مجلد الإخراج")
        if folder:
            self.output_edit.setText(folder)

    # =================================================================
    # تبويب: حول
    # =================================================================
    def _refresh_about(self):
        b = getattr(self, "about_browser", None)
        if b is None:
            return
        pos = b.verticalScrollBar().value()
        b.document().setDefaultStyleSheet(f"a {{ color: {self._theme['primary']}; }}")
        b.setHtml(build_about_html(self._theme, APP_VERSION))
        b.verticalScrollBar().setValue(pos)

    def _build_about_tab(self) -> QWidget:
        from PySide6.QtWidgets import QTextBrowser

        w = QWidget()
        lay = page_layout(w)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        self.about_browser = browser
        self._refresh_about()
        tall(browser, 0.6)
        lay.addWidget(browser, 1)
        return w

    # =================================================================
    # التبويب: المعاينة (تخريج يومي + إعدادات البادئة/الإخراج/الفواصل)
    # =================================================================
    def _build_preview_tab(self) -> QWidget:
        w = QWidget()
        lay = page_layout(w)

        self.output_edit = PathEdit(folders_only=True, placeholder="مجلد الإخراج")
        self.output_edit.setFixedWidth(300)

        today = QDate.currentDate()
        first_of_month = QDate(today.year(), today.month(), 1)
        last_of_month = QDate(today.year(), today.month(), first_of_month.daysInMonth())
        self.preview_start_edit = fit(QDateEdit(first_of_month))
        self.preview_start_edit.setCalendarPopup(True)
        self.preview_start_edit.setDisplayFormat("yyyy-MM-dd")
        self.preview_end_edit = fit(QDateEdit(last_of_month))
        self.preview_end_edit.setCalendarPopup(True)
        self.preview_end_edit.setDisplayFormat("yyyy-MM-dd")
        self.preview_day_combo = fit(QComboBox())
        self.preview_day_combo.currentIndexChanged.connect(self.show_selected_preview_day)

        controls = self._row(
            QLabel("من"), self.preview_start_edit, QLabel("إلى"), self.preview_end_edit,
            QLabel("اليوم المعروض"), self.preview_day_combo,
        )

        self.preview_summary_label = QLabel("لم يتم بناء أي معاينة بعد.")
        self.preview_summary_label.setObjectName("status")
        self.preview_summary_label.setWordWrap(True)

        self.preview_table = QTableWidget(0, 3)
        self.preview_table.setHorizontalHeaderLabels(["الدور", "الوصف", "المدة"])
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        enable_row_numbers(self.preview_table)
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preview_table.customContextMenuRequested.connect(self.show_preview_table_context_menu)
        self.preview_table.cellDoubleClicked.connect(self.on_preview_table_double_clicked)
        tall(self.preview_table, 0.35)

        self.copy_progress = QProgressBar()
        self.copy_progress.setTextVisible(False)
        self.copy_progress.setVisible(False)
        self.build_log = QTextEdit()
        self.build_log.setReadOnly(True)
        tall(self.build_log)

        self.preview_btn = btn("معاينة", self.start_preview, tooltip="بناء المعاينة دون نسخ أي ملف")
        self.confirm_btn = btn("تخريج", self.start_copy, name="successButton",
                               tooltip="تأكيد وتخريج كل الأيام إلى مجلد الإخراج")
        self.confirm_btn.setEnabled(False)
        self.cancel_copy_btn = btn("إيقاف", self.cancel_copy, tooltip="إيقاف النسخ الجاري")
        self.cancel_copy_btn.setEnabled(False)

        body = self._split_body(
            self._col(self._row(self.output_edit), controls, self.preview_summary_label, self.preview_table,
                      self.copy_progress, self.build_log),
            [
                btn("استعراض", self.browse_output_folder, tooltip="اختيار مجلد الإخراج"),
                self.preview_btn,
                self.confirm_btn,
                self.cancel_copy_btn,
                btn("جدول", self.export_schedule_xlsx,
                    tooltip="حفظ جدول المواعيد (xlsx) للأيام المعروضة في المعاينة"),
                btn("تشغيل", self.play_selected_preview_file,
                    tooltip="تشغيل الملف المحدد بالمشغل الافتراضي (أو نقر مزدوج على السطر)"),
                btn("الموقع", self.reveal_selected_preview_file, tooltip="فتح مكان الملف المحدد في المستكشف"),
            ],
        )
        lay.addWidget(body)
        return w

    def collect_daily_builder(self) -> DailyScheduleBuilder:
        if not self.grid.pieces:
            raise ValueError("يجب إضافة سلسلة واحدة على الأقل إلى لوحة \"ترتيب 8 ساعات\".")

        intro_folder = self.intro_path_edit.text().strip()
        if not intro_folder and not len(self.intro_index):
            raise ValueError("يجب تحديد مجلد البادئات وفحصه (أو استيراد نتائج فحصه) من تبويب سلاسل المواد.")

        fasil_configs: List[FasilConfig] = []
        for r in range(self.fasil_table.rowCount()):
            name_item = self.fasil_table.item(r, 1)
            if not name_item:
                continue
            ft: FasilType = name_item.data(Qt.UserRole)
            vals = []
            for col, label in ((4, "نسبة الوجود"), (5, "نسبة تحديد المدة")):
                it = self.fasil_table.item(r, col)
                txt = it.text().strip() if it else ""
                if not txt:
                    vals.append(0.0)
                    continue
                try:
                    v = float(txt)
                except ValueError:
                    raise ValueError(f"قيمة غير رقمية في \"{label}\" للنوع '{ft.name}'.")
                if not 0.0 <= v <= 100.0:
                    raise ValueError(f"\"{label}\" للنوع '{ft.name}' يجب أن تكون بين 0 و100.")
                vals.append(v)
            if vals[0] <= 0 or vals[1] <= 0:
                continue     # نسبة فارغة أو صفر = هذا النوع غير مستخدم
            intro_item = self.fasil_table.item(r, 6)
            intro_path = intro_item.data(Qt.UserRole) if intro_item else None
            fasil_configs.append(FasilConfig(ft, vals[1], vals[0], intro_path=intro_path))
        if not fasil_configs:
            raise ValueError(
                "لا يوجد أي نوع فاصل مفعّل: افحص مجلد الفواصل ثم اضغط \"نسب متساوية\" "
                "أو أدخل نسباً أكبر من 0 (المكتبة ← الفواصل)."
            )

        return DailyScheduleBuilder(
            grid=self.grid.snapshot_for_build(),
            overrides=self.grid24.snapshot(),
            intro_folder=Path(intro_folder) if intro_folder else None,
            intro_index=self.intro_index,
            fasil_configs=fasil_configs,
            tolerance_ratio=self.tolerance_spin.value() / 100.0,
            close_tolerance=self.close_tol_spin.value(),
            fill_gaps=self.fill_gaps_checkbox.isChecked(),
        )

    def start_preview(self):
        if self._daily_preview_worker is not None and self._daily_preview_worker.isRunning():
            return
        start = self.preview_start_edit.date().toPython()
        end = self.preview_end_edit.date().toPython()
        if end < start:
            QMessageBox.warning(self, "مجال تاريخ غير صحيح", "تاريخ الانتهاء يجب أن يكون بعد تاريخ البدء أو يساويه.")
            return
        grid_end = self.grid.start_date + timedelta(days=self.grid.rows - 1)
        if start < self.grid.start_date or end > grid_end:
            r = QMessageBox.question(
                self, "المجال يتجاوز مدى اللوحة",
                f"مدى لوحة الترتيب هو {self.grid.start_date.isoformat()} إلى {grid_end.isoformat()}. "
                "الأيام خارجه لن تحتوي مواد (فقط البدائل والفواصل). هل تريد المتابعة؟",
            )
            if r != QMessageBox.Yes:
                return
        try:
            builder = self.collect_daily_builder()
        except ValueError as e:
            QMessageBox.warning(self, "بيانات ناقصة", str(e))
            return

        self.preview_btn.setEnabled(False)
        self.confirm_btn.setEnabled(False)
        self.build_log.clear()
        self.preview_table.setRowCount(0)
        self.preview_day_combo.clear()
        self.preview_summary_label.setText("جارٍ بناء المعاينة يوماً بيوم...")
        self._preview_built_version = self._state_version

        self._daily_preview_worker = DailyPreviewWorker(builder, start, end)
        self._daily_preview_worker.log_signal.connect(self.build_log.append)
        self._daily_preview_worker.finished_signal.connect(self.on_preview_finished)
        self._daily_preview_worker.start()

    def on_preview_finished(self, results, builder, error):
        self.preview_btn.setEnabled(True)
        if error:
            QMessageBox.critical(self, "خطأ", str(error))
            self.preview_summary_label.setText("فشلت المعاينة.")
            return

        self.current_daily_results = results
        self.current_daily_builder = builder
        self._preview_stale = (self._state_version != self._preview_built_version)

        self.preview_day_combo.blockSignals(True)
        self.preview_day_combo.clear()
        for day in sorted(results.keys()):
            self.preview_day_combo.addItem(day.isoformat(), day)
        self.preview_day_combo.blockSignals(False)

        total_files = sum(len(r.entries) for r in results.values())
        total_duration = sum(r.total_duration for r in results.values())
        max_dev = max((abs(r.total_duration - r.expected_total) for r in results.values()), default=0.0)
        all_warnings = []
        for day in sorted(results.keys()):
            for wmsg in results[day].warnings:
                all_warnings.append(f"{day.isoformat()}: {wmsg}")
        for wmsg in all_warnings[:100]:
            self.build_log.append("⚠ " + wmsg)
        if len(all_warnings) > 100:
            self.build_log.append(f"... و{len(all_warnings) - 100} تحذيراً آخر.")

        text = (
            f"عدد الأيام: {len(results)}  |  إجمالي الملفات: {total_files}  |  "
            f"إجمالي المدة: {fmt_duration(total_duration)} ({total_duration/3600:.2f} ساعة)  |  "
            f"أقصى انحراف عن المدة المتوقعة ليوم: {max_dev:.1f} ثانية  |  تحذيرات: {len(all_warnings)}"
        )
        if self._preview_stale:
            text = "⚠ تغيّر شيء أثناء البناء — المعاينة قديمة، أعد بناءها.  " + text
        self.preview_summary_label.setText(text)
        self.confirm_btn.setEnabled(total_files > 0 and not self._preview_stale)

        if results:
            self.preview_day_combo.setCurrentIndex(0)
            self.show_selected_preview_day()

    def show_selected_preview_day(self):
        if not getattr(self, "current_daily_results", None):
            return
        day = self.preview_day_combo.currentData()
        if day is None:
            return
        result = self.current_daily_results.get(day)
        self.preview_table.setRowCount(0)
        if not result:
            return
        for order, role, src, dur, display in result.entries:
            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            role_item = QTableWidgetItem(role)
            role_item.setData(Qt.UserRole, src)
            self.preview_table.setItem(row, 0, role_item)
            self.preview_table.setItem(row, 1, QTableWidgetItem(display))
            self.preview_table.setItem(row, 2, QTableWidgetItem(fmt_duration(dur)))

    def _preview_row_source_path(self, row: int) -> Optional[Path]:
        item = self.preview_table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def play_selected_preview_file(self):
        row = self.preview_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "لا يوجد تحديد", "اختر صفاً من الجدول أولاً.")
            return
        path = self._preview_row_source_path(row)
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "الملف غير موجود", "تعذّر إيجاد الملف الأصلي في مكانه الحالي.")
            return
        play_file_external(path)

    def reveal_selected_preview_file(self):
        row = self.preview_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "لا يوجد تحديد", "اختر صفاً من الجدول أولاً.")
            return
        path = self._preview_row_source_path(row)
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "الملف غير موجود", "تعذّر إيجاد الملف الأصلي في مكانه الحالي.")
            return
        reveal_in_file_manager(path)

    def show_preview_table_context_menu(self, pos):
        row = self.preview_table.rowAt(pos.y())
        if row < 0:
            return
        self.preview_table.setCurrentCell(row, 0)
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        play_action = menu.addAction("▶ تشغيل الملف (مشغل خارجي)")
        reveal_action = menu.addAction("📂 الذهاب إلى موقع الملف")
        chosen = menu.exec(self.preview_table.viewport().mapToGlobal(pos))
        if chosen == play_action:
            self.play_selected_preview_file()
        elif chosen == reveal_action:
            self.reveal_selected_preview_file()

    def on_preview_table_double_clicked(self, row: int, _column: int):
        self.preview_table.setCurrentCell(row, 0)
        self.play_selected_preview_file()

    def _preflight_copy(self, results):
        """يفحص وجود كل الملفات المصدرية ويحسب الحجم الإجمالي المطلوب. يعيد (المفقود، الحجم)."""
        missing, sizes, needed = [], {}, 0
        for day in sorted(results):
            for (_o, _r, src, _d, _disp) in results[day].entries:
                key = str(src)
                if key not in sizes:
                    try:
                        sizes[key] = os.path.getsize(src)
                    except OSError:
                        sizes[key] = None
                if sizes[key] is None:
                    missing.append((day, src))
                else:
                    needed += sizes[key]
        return missing, needed

    def start_copy(self):
        if self._daily_copy_worker is not None and self._daily_copy_worker.isRunning():
            return
        if self._preview_stale or not self.current_daily_results or not self.current_daily_builder:
            QMessageBox.warning(self, "المعاينة قديمة", "أعد بناء المعاينة قبل التخريج.")
            return
        output_folder = self.output_edit.text().strip()
        if not output_folder:
            QMessageBox.warning(self, "بيانات ناقصة", "يجب تحديد مجلد الإخراج (تبويب المعاينة).")
            return
        out = Path(output_folder)
        results = self.current_daily_results

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            missing, needed = self._preflight_copy(results)
            out.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(out).free
        except OSError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "مجلد الإخراج", f"تعذّر الوصول إلى مجلد الإخراج:\n{e}")
            return
        QApplication.restoreOverrideCursor()

        if needed * 1.02 > free:
            QMessageBox.critical(
                self, "مساحة غير كافية",
                f"المساحة المطلوبة تقريباً {needed / 1e9:.2f} جيجابايت، والمتاحة {free / 1e9:.2f} جيجابايت.",
            )
            return
        if missing:
            ex = "\n".join(f"{d.isoformat()}: {p}" for d, p in missing[:8])
            r = QMessageBox.question(
                self, "ملفات مفقودة",
                f"{len(missing)} ملفاً مصدرياً غير موجود (مثلاً بعد نقل المشروع لجهاز آخر):\n{ex}\n\n"
                "هل تتابع؟ (تُتخطّى الملفات المفقودة وتُسجَّل كأخطاء، وتبقى فجوات في ترقيم ذلك اليوم)",
            )
            if r != QMessageBox.Yes:
                return

        existing = [d for d in sorted(results) if DailyScheduleBuilder.day_folder_has_content(out, d)]
        replace_existing, skip_days = True, set()
        if existing:
            box = QMessageBox(self)
            box.setWindowTitle("أيام مخرَّجة سابقاً")
            box.setIcon(QMessageBox.Question)
            box.setText(
                f"{len(existing)} يوماً من هذه الأيام له مجلد معبّأ من قبل في مجلد الإخراج "
                f"(مثل {existing[0].isoformat()}). ماذا تريد؟"
            )
            b_replace = box.addButton("استبدال ملفات البرنامج القديمة", QMessageBox.AcceptRole)
            b_skip = box.addButton("تخطّي الأيام الموجودة", QMessageBox.ActionRole)
            box.addButton("إلغاء", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is b_replace:
                replace_existing = True
            elif clicked is b_skip:
                skip_days = set(existing)
            else:
                return

        days_to_copy = [d for d in results if d not in skip_days]
        total_files = sum(len(results[d].entries) for d in days_to_copy)
        if not days_to_copy:
            QMessageBox.information(self, "لا شيء للنسخ", "كل الأيام موجودة مسبقاً وقد اخترت تخطّيها.")
            return
        reply = QMessageBox.question(
            self, "تأكيد التخريج",
            f"سيتم إنشاء مجلد فرعي لكل يوم ({len(days_to_copy)} يوماً) بداخل مجلد الإخراج، "
            f"بإجمالي {total_files} ملفاً. هل تريد المتابعة؟",
        )
        if reply != QMessageBox.Yes:
            return

        self.confirm_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.cancel_copy_btn.setEnabled(True)
        self.copy_progress.setValue(0)
        self.copy_progress.setVisible(True)

        self._daily_copy_worker = DailyCopyWorker(
            self.current_daily_builder, results, out,
            skip_days=skip_days, replace_existing=replace_existing,
        )
        self._daily_copy_worker.log_signal.connect(self.build_log.append)
        self._daily_copy_worker.progress_signal.connect(
            lambda i, t: self.copy_progress.setValue(int(i * 100 / max(t, 1)))
        )
        self._daily_copy_worker.finished_signal.connect(self.on_copy_finished)
        self._daily_copy_worker.start()

    def export_schedule_xlsx(self):
        if not self.current_daily_results or not self.current_daily_builder:
            QMessageBox.information(self, "لا توجد معاينة", "ابنِ المعاينة أولاً ثم صدّر الجدول.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "حفظ جدول المواعيد", "جدول_المواعيد.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            self.current_daily_builder.export_schedule_workbook(self.current_daily_results, path)
            self.build_log.append(f"تم حفظ جدول المواعيد: {path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "تعذّر الحفظ", str(e))

    def cancel_copy(self):
        if self._daily_copy_worker is not None and self._daily_copy_worker.isRunning():
            self._daily_copy_worker.cancel()
            self.cancel_copy_btn.setEnabled(False)
            self.build_log.append("جارٍ إيقاف النسخ بعد الملف الحالي...")

    def on_copy_finished(self, output_root, error, report):
        self.preview_btn.setEnabled(True)
        self.cancel_copy_btn.setEnabled(False)
        self.copy_progress.setVisible(False)
        self.confirm_btn.setEnabled(bool(self.current_daily_results) and not self._preview_stale)
        if error:
            QMessageBox.critical(self, "حدث خطأ أثناء النسخ", str(error))
            return
        errors = report.get("errors", [])
        if report.get("cancelled"):
            self.build_log.append("⏹ أُوقف النسخ بطلب المستخدم.")
            QMessageBox.information(
                self, "أُوقف النسخ",
                f"اكتمل نسخ {report.get('days_done', 0)} يوماً فقط؛ اليوم الذي أُوقف أثناءه غير مكتمل "
                "(أعد تخريجه واختر استبدال الملفات القديمة).",
            )
            return
        self.build_log.append(f"✅ انتهى التخريج إلى: {output_root}")
        if errors:
            ex = "\n".join(f"{Path(p).name}: {m}" for p, m in errors[:10])
            QMessageBox.warning(
                self, "اكتمل مع أخطاء",
                f"فشل نسخ {len(errors)} ملفاً (سُجّلت في الجدول الشامل بعلامة ⚠):\n{ex}",
            )
        else:
            QMessageBox.information(
                self, "اكتمل",
                f"تم نسخ كل يوم إلى مجلد فرعي خاص به باسم تاريخه، وتحديث الجدول الشامل "
                f"(صفحة لكل يوم) داخل:\n{output_root}",
            )

    def _section(self, key: str, title: str, body: QWidget, expanded: bool = True) -> Section:
        sec = Section(title, body, expanded=expanded, key=key)
        self._sections[key] = sec
        return sec

    @staticmethod
    def _col(*items, spacing: int = 10) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        for it in items:
            if isinstance(it, QWidget):
                lay.addWidget(it)
            else:
                lay.addLayout(it)
        return w

    @staticmethod
    def _row(*items, stretch: bool = True, spacing: int = 10) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        for it in items:
            lay.addWidget(it)
        if stretch:
            lay.addStretch(1)
        return w

    @staticmethod
    def _split_body(content: QWidget, buttons: list) -> QWidget:
        """المحتوى على اليسار وعمود الأزرار المتراصّة فوق بعضها على اليمين."""
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(button_column(*buttons), 0, Qt.AlignTop)
        lay.addWidget(content, 1)
        return w

    def _progress_start(self, busy: bool = False):
        self.library_progress.setRange(0, 0 if busy else 100)
        self.library_progress.setValue(0)
        self.library_progress.setVisible(True)

    def _progress_end(self):
        self.library_progress.setRange(0, 100)
        self.library_progress.setVisible(False)

    def _friday_files_dropped(self, files):
        for p in files:
            item = QListWidgetItem(p.name)
            item.setData(Qt.UserRole, p)
            self.friday_files_list.addItem(item)
        self.on_overrides_changed()

    def _add_fasil_row(self, ft: FasilType, presence: str = "", duration: str = "", intro_path=None):
        t = self.fasil_table
        row = t.rowCount()
        t.insertRow(row)

        handle = QTableWidgetItem("⠿")
        handle.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        handle.setTextAlignment(Qt.AlignCenter)
        handle.setToolTip("اسحب لإعادة ترتيب الأولوية")
        t.setItem(row, 0, handle)

        name_item = QTableWidgetItem(ft.name)
        name_item.setData(Qt.UserRole, ft)
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        t.setItem(row, 1, name_item)

        count_item = NumericTableWidgetItem(len(ft.files), str(len(ft.files)))
        count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
        t.setItem(row, 2, count_item)

        durations = [d for _p, d in ft.files]
        max_dur = max(durations) if durations else None
        max_item = NumericTableWidgetItem(max_dur if max_dur else -1, fmt_duration(max_dur) if max_dur else "—")
        max_item.setFlags(max_item.flags() & ~Qt.ItemIsEditable)
        t.setItem(row, 3, max_item)

        t.setItem(row, 4, QTableWidgetItem(str(presence or "")))
        t.setItem(row, 5, QTableWidgetItem(str(duration or "")))

        intro_item = QTableWidgetItem(Path(intro_path).name if intro_path else "—")
        if intro_path:
            intro_item.setData(Qt.UserRole, Path(intro_path))
            intro_item.setToolTip(str(intro_path))
        intro_item.setFlags(intro_item.flags() & ~Qt.ItemIsEditable)
        t.setItem(row, 6, intro_item)

        open_item = QTableWidgetItem("📂")
        open_item.setFlags(open_item.flags() & ~Qt.ItemIsEditable)
        open_item.setToolTip("فتح مجلد هذا النوع")
        t.setItem(row, 7, open_item)

    def _clear_fasil_cell(self):
        """Delete: يفرّغ النسبة (تعطيل النوع) أو يزيل بادئة النوع."""
        row, col = self.fasil_table.currentRow(), self.fasil_table.currentColumn()
        if row < 0:
            return
        item = self.fasil_table.item(row, col)
        if item is None:
            return
        if col in (4, 5):
            item.setText("")
        elif col == 6:
            item.setData(Qt.UserRole, None)
            item.setText("—")
            item.setToolTip("")
            self.invalidate_preview()

    def generate_equal_ratios(self):
        n = self.fasil_table.rowCount()
        if n == 0:
            QMessageBox.information(self, "لا توجد أنواع", "افحص مجلد الفواصل أولاً.")
            return
        pct = str(round(100.0 / n, 1))
        self.fasil_table.blockSignals(True)
        for r in range(n):
            for col in (4, 5):
                it = self.fasil_table.item(r, col)
                if it is None:
                    it = QTableWidgetItem()
                    self.fasil_table.setItem(r, col, it)
                it.setText(pct)
                it.setBackground(QBrush())
        self.fasil_table.blockSignals(False)
        self.invalidate_preview()

    def rebuild_intro_table(self):
        t = self.intro_table
        t.setSortingEnabled(False)
        t.setRowCount(0)
        for (num, typ), (path, dur) in sorted(self.intro_index.entries.items()):
            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, NumericTableWidgetItem(num, f"{num:03d}"))
            t.setItem(row, 1, QTableWidgetItem(typ))
            d_item = NumericTableWidgetItem(float(dur), fmt_duration(dur))
            if dur and dur > 30:
                d_item.setForeground(QBrush(QColor("#e8590c")))
                d_item.setToolTip("مدة البادئة أطول من نصف دقيقة — تأكد من الملف")
            t.setItem(row, 2, d_item)
            f_item = QTableWidgetItem(Path(path).name)
            f_item.setToolTip(str(path))
            t.setItem(row, 3, f_item)
        t.setSortingEnabled(True)
        self.intro_status_label.setText(f"{len(self.intro_index)} بادئة" if len(self.intro_index) else "")



def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("LessonPlaylistBuilder")
    app.setApplicationName("MainWindow")
    app.setLayoutDirection(Qt.RightToLeft)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
