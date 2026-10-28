# -*- coding: utf-8 -*-
"""
قائمة أسماء ملفات مستثناة عالمياً: أي ملف يحمل أحد هذه الأسماء (بغضّ النظر
عن مساره أو المجلد الذي يوجد فيه) يُستبعد تلقائياً من أي فحص (سلاسل، بادئات،
فواصل) في أي مكان بالبرنامج. المطابقة بالاسم الكامل للملف (بلا حساسية لحالة
الأحرف)، وليست بالمسار الكامل، لأن الهدف استثناء اسم مكرر أينما ظهر.
"""
from typing import Iterable, Set

_excluded_names: Set[str] = set()


def set_excluded_filenames(names: Iterable[str]) -> None:
    global _excluded_names
    _excluded_names = {n.strip().lower() for n in names if n and n.strip()}


def get_excluded_filenames() -> Set[str]:
    return set(_excluded_names)


def is_excluded_filename(name: str) -> bool:
    return name.strip().lower() in _excluded_names
