"""
Scrapes Moroccan concours/orientation sites and caches results for 24 hours.
Uses a plain thread-safe dict — no Redis dependency required.
"""

import re
import time
import threading
import traceback

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

CACHE_TTL = 86_400          # 24 h
MAX_CHARS_PER_SOURCE = 2_500  # keep per-source text short to stay within token budget

SOURCES = {
    "postbac":        "https://postbac.ma/concours/",
    "dates_concours": "https://www.dates-concours.ma/calendrier-de-concours/",
    "orientation":    "https://www.orientationmaroc.net/",
}

SOURCE_LABELS = {
    "postbac":        "PostBac.ma — concours, inscriptions",
    "dates_concours": "Dates-Concours.ma — calendrier officiel",
    "orientation":    "OrientationMaroc.net — écoles et orientation",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8",
}

# ── In-process cache: { key: (text, fetched_at) } ────────────────────────────

_cache: dict[str, tuple[str, float]] = {}
_lock  = threading.Lock()


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _extract_text(html: str) -> str:
    """Strip noise and return the main readable content of a page."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove layout/UI noise
    for tag in soup(
        ["script", "style", "nav", "footer", "header", "aside",
         "noscript", "iframe", "form", "button", "svg", "img",
         "input", "select", "textarea", "meta", "link", "figure"]
    ):
        tag.decompose()

    # Try to find a 'main' content area first
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"(main|content|primary)", re.I))
        or soup.find(class_=re.compile(r"(main|content|primary|entry)", re.I))
        or soup.body
        or soup
    )

    raw = main.get_text(separator="\n") if main else soup.get_text(separator="\n")

    # Clean whitespace; drop single-word / very short lines (menu fragments)
    lines = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) >= 25:
            lines.append(line)

    return "\n".join(lines)


def _fetch_one(key: str, url: str) -> str:
    """Fetch, parse, cache and return text for one source. Never raises."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=12, allow_redirects=True)
        resp.raise_for_status()
        text = _extract_text(resp.text)
        text = text[:MAX_CHARS_PER_SOURCE]
        with _lock:
            _cache[key] = (text, time.time())
        print(f"[scraper] {key}: OK ({len(text)} chars)")
        return text
    except Exception:
        print(f"[scraper] {key} FAILED:\n{traceback.format_exc()[:300]}")
        # Keep whatever stale entry exists
        with _lock:
            entry = _cache.get(key)
        return entry[0] if entry else ""


# ── Public API ────────────────────────────────────────────────────────────────

def _is_stale(key: str) -> bool:
    with _lock:
        entry = _cache.get(key)
    if not entry:
        return True
    return (time.time() - entry[1]) > CACHE_TTL


def _cached_text(key: str) -> str:
    with _lock:
        entry = _cache.get(key)
    return entry[0] if entry else ""


def get_context() -> str:
    """
    Return a combined, labelled text block from all three sources.

    Cold start  → fetches synchronously (blocks ~3-10 s, happens once per process).
    Warm cache  → returns instantly from memory.
    Stale cache → returns stale data immediately; background thread refreshes quietly.
    """
    parts = []

    for key, url in SOURCES.items():
        if _is_stale(key):
            stale = _cached_text(key)
            if stale:
                # Use stale data now; refresh silently in background
                threading.Thread(target=_fetch_one, args=(key, url), daemon=True).start()
                parts.append(stale)
            else:
                # No data at all → fetch synchronously
                parts.append(_fetch_one(key, url))
        else:
            parts.append(_cached_text(key))

    sections = []
    for key, text in zip(SOURCES.keys(), parts):
        if text.strip():
            sections.append(f"[ {SOURCE_LABELS[key]} ]\n{text}")

    return "\n\n".join(sections)


def warm_cache() -> None:
    """
    Pre-warm all sources in background threads at server startup.
    Returns immediately — does not block the import.
    """
    for key, url in SOURCES.items():
        if _is_stale(key):
            threading.Thread(target=_fetch_one, args=(key, url), daemon=True).start()
