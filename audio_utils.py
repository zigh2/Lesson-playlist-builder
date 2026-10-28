# -*- coding: utf-8 -*-
"""
أدوات قراءة مدة الملفات الصوتية وفحص المجلدات.
تعتمد على مكتبة mutagen التي لا تحتاج إلى تثبيت ffmpeg خارجي.
"""
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from mutagen import File as MutagenFile

from exclusions import is_excluded_filename

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".wma", ".aac"}
DEFAULT_SCAN_WORKERS = 16


def is_audio_file(path: Path) -> bool:
    """يتحقق ما إذا كانت لاحقة الملف تُعتبر صوتية معروفة، وأن اسمه ليس ضمن قائمة الاستثناءات."""
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        return False
    return not is_excluded_filename(path.name)


def get_duration_seconds(path: Path) -> Optional[float]:
    """
    يحاول قراءة مدة الملف الصوتي بالثواني.
    يعيد None في حال تعذّرت القراءة (ملف تالف أو صيغة غير مدعومة).
    """
    try:
        audio = MutagenFile(str(path))
        if audio is not None and getattr(audio, "info", None) is not None:
            length = getattr(audio.info, "length", None)
            if length and length > 0:
                return float(length)
    except Exception:
        pass
    return None


_duration_cache: Dict[Tuple[str, float, int], Optional[float]] = {}


def get_duration_cached(path) -> Optional[float]:
    """
    نفس get_duration_seconds لكن مع ذاكرة مؤقتة بمفتاح (المسار، تاريخ التعديل، الحجم)،
    فلا يُعاد فتح الملف من القرص ما دام لم يتغيّر (مفيد لملفات البدائل الثابتة التي
    تُستعلَم مدتها مراراً عند كل تعديل بالواجهة).
    """
    try:
        p = Path(path)
        st = os.stat(p)
    except (OSError, TypeError, ValueError):
        return None
    key = (str(p), st.st_mtime, st.st_size)
    if key in _duration_cache:
        return _duration_cache[key]
    dur = get_duration_seconds(p)
    _duration_cache[key] = dur
    return dur


def resolve_durations_parallel(
    paths: List[Path],
    max_workers: int = DEFAULT_SCAN_WORKERS,
    progress: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """
    يقرأ مدد عدة ملفات دفعة واحدة عبر مجمّع خيوط (I/O-bound أساساً، فقراءة عدة ملفات
    بالتوازي أسرع بكثير من قراءتها ملفاً تلو الآخر). يعيد قاموساً: المسار -> المدة
    (أو None لو تعذّرت القراءة).
    """
    results: dict = {}
    total = len(paths)
    if total == 0:
        return results
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(get_duration_seconds, p): p for p in paths}
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                results[p] = fut.result()
            except Exception:
                results[p] = None
            done += 1
            if progress:
                progress(done, total)
    return results


def scan_folder(folder: Path) -> List[Tuple[Path, float]]:
    """
    يفحص مجلداً (الملفات المباشرة فقط، دون مجلدات فرعية) ويعيد قائمة
    من أزواج (المسار، المدة بالثواني) لكل ملف صوتي أمكن قراءة مدته بنجاح.
    الملفات التي تعذّرت قراءتها يتم تجاهلها بصمت (لا تدخل في الاختيار).
    قراءة المدد تتم بالتوازي (مجمّع خيوط) لتسريع الفحص عند وجود ملفات كثيرة.
    """
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        return []
    candidates = [p for p in sorted(folder.iterdir()) if p.is_file() and is_audio_file(p)]
    durations = resolve_durations_parallel(candidates)
    return [(p, durations[p]) for p in candidates if durations.get(p) is not None]


def scan_folder_recursive(folder: Path) -> List[Tuple[Path, float]]:
    """نفس scan_folder لكن يفحص كل المجلدات الفرعية أيضاً (لدعم تنظيمات مستقبلية)."""
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        return []
    candidates = [p for p in sorted(folder.rglob("*")) if p.is_file() and is_audio_file(p)]
    durations = resolve_durations_parallel(candidates)
    return [(p, durations[p]) for p in candidates if durations.get(p) is not None]
