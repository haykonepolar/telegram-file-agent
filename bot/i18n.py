# i18n.py - minimal RU/EN string dictionary + language detection.
# detect: any Cyrillic char -> ru, otherwise en. Empty/unclear -> en.
import json
import re
from pathlib import Path

_DIR = Path(__file__).parent / "i18n"
_cache: dict = {}
_CYR = re.compile(r"[а-яёА-ЯЁ]")


def detect_lang(text: str | None) -> str:
    if text and _CYR.search(text):
        return "ru"
    return "en"


def load(lang: str) -> dict:
    lang = lang if lang in ("ru", "en") else "en"
    if lang not in _cache:
        _cache[lang] = json.loads(
            (_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    return _cache[lang]


def tr(lang: str, key: str, **kw) -> str:
    """Format string from dict. {placeholders} via str.format; missing
    key falls back to en, then to the key itself."""
    try:
        s = load(lang)[key]
    except KeyError:
        s = load("en").get(key, key)
    return s.format(**kw) if kw else s
