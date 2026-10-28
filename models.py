# -*- coding: utf-8 -*-
"""
نماذج البيانات المستخدمة بين الواجهة ومحرك البناء.

- FasilConfig: إعدادات نوع فاصل واحد (النسبتان والبادئة الاختيارية).
- GridSnapshot / PieceSnap / OverrideSnapshot: لقطات ثابتة (غير مرتبطة بعناصر Qt) من
  لوحتي الترتيب، تُمرَّر إلى خيط البناء بحيث لا يتأثر البناء بأي تعديل يجريه المستخدم
  على الواجهة أثناء عمله (تفادي سباق الخيوط).
"""
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from library_scanner import SeriesInfo, FasilType


@dataclass
class FasilConfig:
    """
    ربط نوع فاصل (من الماسح) بنسبتين وبادئة اختيارية وترتيب:
    - duration_percentage: تحدد حجم الملف المطلوب نسبة إلى الوقت الواجب تعبئته ضمن الفقرة.
    - daily_presence_percentage: احتمال "وجود" هذا النوع في فقرة معينة (بوابة عشوائية).
    - intro_path: ملف بادئة واحد اختياري خاص بهذا النوع، يُدرَج قبل كل ملف يُختار منه.
    - ترتيب قائمة fasil_configs نفسها هو أولوية الظهور (الأول أولاً).
    """
    fasil_type: FasilType
    duration_percentage: float          # 0-100
    daily_presence_percentage: float    # 0-100
    intro_path: Optional[Path] = None


@dataclass
class PieceSnap:
    series: SeriesInfo
    start_row: int
    col: int
    row_widths: Tuple[int, ...]


@dataclass
class GridSnapshot:
    start_date: date
    rows: int
    cols: int
    slot_minutes: int
    day_start_minutes: int
    occupied: Dict[Tuple[int, int], int] = field(default_factory=dict)   # (row,col) -> id القطعة
    pieces: Dict[int, PieceSnap] = field(default_factory=dict)           # id القطعة -> القطعة


@dataclass
class OverrideSnapshot:
    morning_minutes: int = 6 * 60
    morning_file: Optional[Path] = None
    morning_duration: float = 0.0
    evening_minutes: int = 20 * 60
    evening_file: Optional[Path] = None
    evening_duration: float = 0.0
    friday_minutes: int = 12 * 60
    friday_files: List[Tuple[Path, float]] = field(default_factory=list)
