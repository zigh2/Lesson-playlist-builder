# -*- coding: utf-8 -*-
"""
فحص مجلد "السلاسل" وفهم بنيته من أسماء المجلدات والملفات مباشرة، دون الحاجة
لأي تهيئة يدوية. كذلك فحص مجلد "الفواصل" (نوع واحد أو عدة أنواع حسب المجلدات
الفرعية الموجودة) ومجلد "البادئات".

نمط تسمية مجلد السلسلة (مثال):
    MK-026Le-Sتفسير السورة رقم 114 - الناسS
    => رقم السلسلة التقريبي المشتق من الكود: 026   |  اسم السلسلة: تفسير السورة رقم 114 - الناس

نمط تسمية ملفات المادة (مثال):
    232Sp004-20140131_03054-F1C00S-A.Low-Nنهى رسول الله ﷺN.mp3
    => رقم السلسلة: 232   |  رقم المادة: 004

نعتمد على الرقم المستخرج من اسم الملف نفسه لتحديد رقم المادة (وليس على عدّ
المجلدات الفرعية)، لأن بعض مجلدات المواد قد تكون فارغة من الملفات.
لكل رقم مادة، نختار أكبر ملف (بالحجم) بين كل الملفات التي تحمل نفس الرقم،
باعتباره الأعلى جودة/الأنسب للاستخدام؛ ونحتفظ ببقية الملفات (بترتيب الحجم) كبدائل
احتياطية تُستخدم تلقائياً إن تعذّرت قراءة مدة الأكبر (ملف تالف مثلاً).
"""
import re
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from audio_utils import is_audio_file, scan_folder_recursive, resolve_durations_parallel

# مثال: "232Sp004-20140131_03054-..." -> رقم السلسلة=232 حرف الرمز=Sp رقم المادة=004
FILE_PREFIX_PATTERN = re.compile(r"^(\d+)([A-Za-z]+)(\d{3})-")

# مثال: "MK-026Le-Sتفسير السورة رقم 114 - الناسS" -> الكود=026Le الاسم=تفسير السورة رقم 114 - الناس
SERIES_FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9]+-(\d+[A-Za-z]+)-S(.*)S$")

MAX_ALT_PATHS = 5          # أقصى عدد ملفات بديلة تُحفظ لكل مادة
MAX_UNMATCHED_EXAMPLES = 20


@dataclass
class Material:
    number: int
    path: Path
    size_bytes: int
    duration_seconds: Optional[float] = None
    # ملفات أخرى لنفس المادة (أصغر حجماً) بترتيب الحجم تنازلياً، احتياطاً لفشل قراءة الأكبر
    alt_paths: List[Path] = field(default_factory=list, repr=False)


@dataclass
class SeriesInfo:
    series_number: str
    series_name: str
    folder: Path
    type_code: str = ""
    materials: List[Material] = field(default_factory=list)
    unmatched_count: int = 0                       # ملفات صوتية لا تطابق نمط الاسم (تُتجاهل)
    unmatched: List[str] = field(default_factory=list)  # أمثلة من أسمائها

    @property
    def material_count(self) -> int:
        return len(self.materials)

    @property
    def missing_duration_count(self) -> int:
        return sum(1 for m in self.materials if not m.duration_seconds)

    @property
    def display_name(self) -> str:
        return f"{self.series_number} - {self.series_name}" if self.series_name else self.series_number

    @property
    def series_type(self) -> str:
        return guess_series_type(self.type_code)


@dataclass
class FasilType:
    name: str
    folder: Path
    files: List[Tuple[Path, float]] = field(default_factory=list)  # (مسار، مدة) بعد الفحص


SERIES_TYPE_CODE_MAP = {
    "le": "درس",
    "sp": "خطبة",
    "pr": "برنامج",
    "qu": "سؤال وجواب",
    "co": "دورة",
    "se": "محاضرة",
    "br": "فاصل",
}


def guess_series_type(code: str) -> str:
    """يحدد نوع السلسلة من رمز الحروف في اسم مجلدها/ملفاتها (مثال: Le=درس, Sp=خطبة...)."""
    return SERIES_TYPE_CODE_MAP.get((code or "").strip().lower(), "غير محدد")


def normalize_series_number(value) -> Optional[int]:
    """يوحّد رقم السلسلة (023 = 23)، ويعيد None إن لم يكن رقماً."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
def parse_series_folder_name(name: str) -> Tuple[str, str, str]:
    """يعيد (رقم السلسلة، اسم السلسلة، رمز النوع الحرفي مثل Le/Sp/Pr...)."""
    m = SERIES_FOLDER_PATTERN.match(name)
    if m:
        code, series_name = m.groups()
        num_match = re.match(r"(\d+)([A-Za-z]*)", code)
        if num_match:
            series_number = num_match.group(1)
            type_code = num_match.group(2)
        else:
            series_number, type_code = code, ""
        return series_number, series_name, type_code
    return name, name, ""  # تعذّر تفسير الاسم، نستخدمه كما هو


def scan_series_folder(folder: Path) -> SeriesInfo:
    """يفحص مجلد سلسلة واحدة ويستخرج المواد منها اعتماداً على أسماء الملفات."""
    folder_series_number, series_name, type_code = parse_series_folder_name(folder.name)
    groups: Dict[int, List[Path]] = {}
    file_series_numbers: Dict[str, int] = {}   # رقم السلسلة كما يظهر بالملفات -> عدد مرات تكراره
    file_type_codes: Dict[str, int] = {}
    unmatched_count = 0
    unmatched: List[str] = []

    for p in folder.rglob("*"):
        try:
            if not p.is_file() or not is_audio_file(p):
                continue
        except OSError:
            continue
        m = FILE_PREFIX_PATTERN.match(p.name)
        if not m:
            unmatched_count += 1
            if len(unmatched) < MAX_UNMATCHED_EXAMPLES:
                unmatched.append(p.name)
            continue
        file_series_numbers[m.group(1)] = file_series_numbers.get(m.group(1), 0) + 1
        file_type_codes[m.group(2)] = file_type_codes.get(m.group(2), 0) + 1
        material_num = int(m.group(3))
        groups.setdefault(material_num, []).append(p)

    # رقم السلسلة مكرر في كل ملفات السلسلة، وهو مصدر أوثق من اسم المجلد (الذي قد لا يطابق
    # النمط المتوقع أحياناً). نعتمد الرقم الأكثر تكراراً بين الملفات إن وُجد، وإلا نستخدم
    # ما استُخرج من اسم المجلد نفسه.
    if file_series_numbers:
        series_number = max(file_series_numbers.items(), key=lambda kv: kv[1])[0]
    else:
        series_number = folder_series_number

    if not type_code and file_type_codes:
        type_code = max(file_type_codes.items(), key=lambda kv: kv[1])[0]

    materials: List[Material] = []
    for num, paths in groups.items():
        sized: List[Tuple[int, Path]] = []
        for p in paths:
            try:
                sized.append((p.stat().st_size, p))
            except OSError:
                continue
        if not sized:
            continue
        sized.sort(key=lambda sp: sp[0], reverse=True)
        largest_size, largest = sized[0]
        materials.append(Material(
            number=num, path=largest, size_bytes=largest_size,
            alt_paths=[p for _s, p in sized[1:1 + MAX_ALT_PATHS]],
        ))
    materials.sort(key=lambda m: m.number)

    return SeriesInfo(
        series_number=series_number, series_name=series_name, folder=folder,
        type_code=type_code, materials=materials,
        unmatched_count=unmatched_count, unmatched=unmatched,
    )


def scan_all_series(root: Path, log: Optional[Callable[[str], None]] = None) -> List[SeriesInfo]:
    """يفحص كل المجلدات المباشرة داخل مجلد السلاسل الرئيسي، كل مجلد يمثّل سلسلة."""
    root = Path(root)
    result: List[SeriesInfo] = []
    if not root.exists():
        return result
    log = log or (lambda msg: None)
    for child in sorted(root.iterdir()):
        if child.is_dir():
            info = scan_series_folder(child)
            if info.materials:
                result.append(info)
                log(f"سلسلة: {info.display_name}  ({info.material_count} مادة)")
            else:
                log(f"تخطّي '{child.name}': لا توجد مواد صالحة فيه.")
            if info.unmatched_count:
                examples = "، ".join(info.unmatched[:3])
                log(f"⚠ '{child.name}': {info.unmatched_count} ملف صوتي لا يطابق نمط الاسم "
                    f"(مثل 023Sp004-...) وتم تجاهله. أمثلة: {examples}")
    return result


def resolve_material_durations(
    series_list: List[SeriesInfo], progress: Optional[Callable[[int, int], None]] = None
) -> None:
    """
    يقرأ مدة الملف المختار لكل مادة بالتوازي. وإن تعذّرت قراءة الملف الأكبر (تالف مثلاً)
    يجرّب تلقائياً الملفات البديلة لنفس المادة بالترتيب (الأصغر حجماً تدريجياً).
    """
    targets = [mat for s in series_list for mat in s.materials if mat.duration_seconds is None]
    if not targets:
        return
    results = resolve_durations_parallel([mat.path for mat in targets], progress=progress)
    for mat in targets:
        mat.duration_seconds = results.get(mat.path)

    pending = [m for m in targets if m.duration_seconds is None and m.alt_paths]
    level = 0
    while pending and level < MAX_ALT_PATHS:
        paths = [m.alt_paths[level] for m in pending if level < len(m.alt_paths)]
        res = resolve_durations_parallel(paths) if paths else {}
        still = []
        for m in pending:
            if level >= len(m.alt_paths):
                continue
            p = m.alt_paths[level]
            d = res.get(p)
            if d:
                m.path = p
                m.duration_seconds = d
                try:
                    m.size_bytes = p.stat().st_size
                except OSError:
                    pass
            else:
                still.append(m)
        pending = still
        level += 1


# ---------------------------------------------------------------------------
# فهرس البادئات (مجلد البادئات المشترك)
# ---------------------------------------------------------------------------
@dataclass
class IntroIndex:
    """
    فهرس بادئات السلاسل: المفتاح (رقم السلسلة الموحّد، رمز النوع بحروف صغيرة) -> (المسار، المدة).
    المطابقة: رقم + نوع أولاً؛ وإن لم يوجد نوع مطابق وكان للرقم مرشح واحد فقط يُستخدم؛
    وإن تعدّد المرشحون بلا نوع مطابق لا تُستخدم أي بادئة (مع تحذير) تفادياً للخلط.
    """
    entries: Dict[Tuple[int, str], Tuple[Path, float]] = field(default_factory=dict)
    duplicates: Dict[Tuple[int, str], int] = field(default_factory=dict)
    unmatched_count: int = 0
    unmatched: List[str] = field(default_factory=list)
    folder: str = ""

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, series_number, type_code: str = "") -> Tuple[Optional[Tuple[Path, float]], Optional[str]]:
        """يعيد (البادئة أو None، تحذير أو None)."""
        n = normalize_series_number(series_number)
        if n is None:
            return None, None
        t = (type_code or "").strip().lower()
        if t and (n, t) in self.entries:
            return self.entries[(n, t)], None
        cands = [(k, v) for k, v in self.entries.items() if k[0] == n]
        if not cands:
            return None, None
        if len(cands) == 1:
            return cands[0][1], None
        kinds = "/".join(sorted(k[1] for k, _v in cands))
        return None, (f"تعدّد بادئات للسلسلة رقم {n} ({kinds}) ولا يوجد نوع مطابق لنوع السلسلة "
                      f"'{type_code or 'غير محدد'}'، لن تُستخدم أي بادئة لها.")

    def remove_filenames(self, excluded_lower: set) -> int:
        """يزيل البادئات التي اسم ملفها ضمن الاستثناءات. يعيد عدد المزالة."""
        gone = [k for k, (p, _d) in self.entries.items() if p.name.lower() in excluded_lower]
        for k in gone:
            del self.entries[k]
        return len(gone)

    def to_dict(self) -> dict:
        return {
            "folder": self.folder,
            "entries": [
                {"number": k[0], "type": k[1], "path": str(p), "duration": d,
                 "duplicates": self.duplicates.get(k, 1)}
                for k, (p, d) in self.entries.items()
            ],
            "unmatched_count": self.unmatched_count,
            "unmatched": self.unmatched[:MAX_UNMATCHED_EXAMPLES],
        }

    @staticmethod
    def from_dict(d: dict) -> "IntroIndex":
        idx = IntroIndex(folder=d.get("folder", ""))
        for e in d.get("entries", []):
            key = (int(e["number"]), str(e.get("type", "")).lower())
            idx.entries[key] = (Path(e["path"]), float(e["duration"]))
            idx.duplicates[key] = int(e.get("duplicates", 1))
        idx.unmatched_count = int(d.get("unmatched_count", 0))
        idx.unmatched = list(d.get("unmatched", []))
        return idx


def scan_intro_folder(
    root: Path, progress: Optional[Callable[[int, int], None]] = None
) -> IntroIndex:
    """
    يفحص مجلد البادئات المشترك (بادئة كل مواد كل سلسلة بملف واحد ثابت)، ويربط كل بادئة
    برقم السلسلة ورمز نوعها من بداية اسم ملفها (مثال: 223Sp000-... => السلسلة 223، النوع Sp).
    إن وُجد أكثر من ملف لنفس (الرقم، النوع) يُختار أكبرها حجماً ويُسجَّل عدد المكرر.
    """
    root = Path(root)
    index = IntroIndex(folder=str(root))
    if not root.exists() or not root.is_dir():
        return index
    groups: Dict[Tuple[int, str], List[Path]] = {}
    for p in sorted(root.iterdir()):
        try:
            if not p.is_file() or not is_audio_file(p):
                continue
        except OSError:
            continue
        m = FILE_PREFIX_PATTERN.match(p.name)
        if not m:
            index.unmatched_count += 1
            if len(index.unmatched) < MAX_UNMATCHED_EXAMPLES:
                index.unmatched.append(p.name)
            continue
        key = (int(m.group(1)), m.group(2).lower())
        groups.setdefault(key, []).append(p)

    chosen: Dict[Tuple[int, str], Path] = {}
    for key, paths in groups.items():
        def _size(pp: Path) -> int:
            try:
                return pp.stat().st_size
            except OSError:
                return -1
        chosen[key] = max(paths, key=_size)
        index.duplicates[key] = len(paths)
    durs = resolve_durations_parallel(list(chosen.values()), progress=progress)
    for key, path in chosen.items():
        d = durs.get(path)
        if d:
            index.entries[key] = (path, d)
    return index


# ---------------------------------------------------------------------------
def scan_fasil_root(root: Path) -> List[FasilType]:
    """
    إن وجدت مجلدات فرعية داخل مجلد الفواصل، كل مجلد فرعي يمثّل نوعاً مستقلاً.
    وإن لم توجد (الملفات مباشرة في المجلد الرئيسي)، يُعتبر نوعاً واحداً فقط.
    """
    root = Path(root)
    if not root.exists():
        return []
    subdirs = [c for c in sorted(root.iterdir()) if c.is_dir()]
    if subdirs:
        return [FasilType(name=d.name, folder=d) for d in subdirs]
    return [FasilType(name=root.name, folder=root)]


def resolve_fasil_files(
    fasil_types: List[FasilType], progress: Optional[Callable[[int, int], None]] = None
) -> None:
    """يفحص ملفات كل نوع فاصل ويقرأ مددها (يدعم مجلدات فرعية عبر البحث المتكرر)."""
    total = len(fasil_types)
    for i, ft in enumerate(fasil_types, start=1):
        ft.files = scan_folder_recursive(ft.folder)
        if progress:
            progress(i, total)


# ---------------------------------------------------------------------------
# تصدير/استيراد نتائج الفحص (لتفادي إعادة فحص مكتبة ضخمة في كل اختبار للبرنامج)
# ---------------------------------------------------------------------------
SCAN_EXPORT_FORMAT_VERSION = 1


def series_to_dict(s: SeriesInfo) -> dict:
    return {
        "series_number": s.series_number,
        "series_name": s.series_name,
        "folder": str(s.folder),
        "type_code": s.type_code,
        "unmatched_count": s.unmatched_count,
        "unmatched": s.unmatched[:MAX_UNMATCHED_EXAMPLES],
        "materials": [
            {
                "number": m.number,
                "path": str(m.path),
                "size_bytes": m.size_bytes,
                "duration_seconds": m.duration_seconds,
            }
            for m in s.materials
        ],
    }


def series_from_dict(d: dict) -> SeriesInfo:
    materials = [
        Material(
            number=md["number"],
            path=Path(md["path"]),
            size_bytes=md.get("size_bytes", 0),
            duration_seconds=md.get("duration_seconds"),
        )
        for md in d.get("materials", [])
    ]
    return SeriesInfo(
        series_number=d["series_number"],
        series_name=d["series_name"],
        folder=Path(d["folder"]),
        type_code=d.get("type_code", ""),
        materials=materials,
        unmatched_count=d.get("unmatched_count", 0),
        unmatched=list(d.get("unmatched", [])),
    )


def export_series_scan(series_list: List[SeriesInfo], path: Path) -> None:
    """يحفظ نتائج فحص السلاسل (بما فيها مدد الملفات المقروءة) إلى ملف JSON واحد."""
    payload = {
        "format": "series_scan",
        "version": SCAN_EXPORT_FORMAT_VERSION,
        "series": [series_to_dict(s) for s in series_list],
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def import_series_scan(path: Path) -> List[SeriesInfo]:
    """يستعيد نتائج فحص سلاسل محفوظة مسبقاً دون الحاجة لإعادة فحص القرص."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "series_scan":
        raise ValueError("هذا الملف ليس ملف تصدير نتائج فحص سلاسل صالحاً.")
    return [series_from_dict(d) for d in data.get("series", [])]


def fasil_to_dict(ft: FasilType) -> dict:
    return {
        "name": ft.name,
        "folder": str(ft.folder),
        "files": [[str(p), dur] for p, dur in ft.files],
    }


def fasil_from_dict(d: dict) -> FasilType:
    files = [(Path(p), dur) for p, dur in d.get("files", [])]
    return FasilType(name=d["name"], folder=Path(d["folder"]), files=files)


def export_fasil_scan(fasil_types: List[FasilType], path: Path) -> None:
    """يحفظ نتائج فحص أنواع الفواصل (بما فيها مدد الملفات المقروءة) إلى ملف JSON واحد."""
    payload = {
        "format": "fasil_scan",
        "version": SCAN_EXPORT_FORMAT_VERSION,
        "fasil_types": [fasil_to_dict(ft) for ft in fasil_types],
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def import_fasil_scan(path: Path) -> List[FasilType]:
    """يستعيد نتائج فحص فواصل محفوظة مسبقاً دون الحاجة لإعادة فحص القرص."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "fasil_scan":
        raise ValueError("هذا الملف ليس ملف تصدير نتائج فحص فواصل صالحاً.")
    return [fasil_from_dict(d) for d in data.get("fasil_types", [])]


def export_intro_scan(index: IntroIndex, path: Path) -> None:
    """يحفظ نتائج فحص البادئات إلى ملف JSON واحد."""
    payload = {"format": "intro_scan", "version": SCAN_EXPORT_FORMAT_VERSION, **index.to_dict()}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def import_intro_scan(path: Path) -> IntroIndex:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "intro_scan":
        raise ValueError("هذا الملف ليس ملف تصدير نتائج فحص بادئات صالحاً.")
    return IntroIndex.from_dict(data)
