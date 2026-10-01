"""Shared helpers for opt-in tracker naming rules."""

import re
import unicodedata
from collections.abc import Iterable, Iterator
from typing import Any

import langcodes
from unidecode import unidecode

from src.meta import Meta

INCOMPLETE_PACK_TRACKERS = frozenset(
    {
        "AITHER",
        "AVISTAZ",
        "CINEMAZ",
        "DARKPEERS",
        "HAWKEUNO",
        "HDBITS",
        "HDSPACE",
        "HDTORRENTS",
        "IPTORRENTS",
        "LST",
        "OLDTOONSWORLD",
        "ONLYENCODES",
        "PRIVATEHD",
        "RASTASTUGAN",
        "TORRENTLEECH",
        "ULCX",
        "YUSCENE",
    }
)


def title_key(title: str) -> str:
    return "".join(char for char in unidecode(title).casefold() if char.isalnum())


def _latin_title(title: str) -> bool:
    return all("LATIN" in unicodedata.name(char, "") for char in title if char.isalpha())


def _first_distinct_title(candidates: Iterable[Any], title: str, *, latin_only: bool = False) -> str:
    primary_key = title_key(title)
    for value in candidates:
        if not isinstance(value, str):
            continue
        candidate = re.sub(r"^AKA\s+", "", value.strip(), flags=re.IGNORECASE)
        if candidate and title_key(candidate) != primary_key and (not latin_only or _latin_title(candidate)):
            return candidate
    return ""


def _romanized_imdb_titles(meta: Meta) -> Iterator[str]:
    aliases = meta.imdb_info.get("akas", [])
    for alias in aliases if isinstance(aliases, list) else []:
        if not isinstance(alias, dict):
            continue
        attributes = str(alias.get("attributes", "")).casefold()
        if not any(marker in attributes for marker in ("romanized", "romanised", "transliterated")):
            continue
        language = alias.get("language")
        if language and meta.original_language:
            try:
                if langcodes.find(str(language)).language != langcodes.get(meta.original_language).language:
                    continue
            except LookupError, ValueError:
                continue
        name = alias.get("title")
        if isinstance(name, str):
            yield name


def select_aka(meta: Meta, title: str) -> str:
    """Prefer a source AKA, using romanization when its script is non-Latin."""
    if meta.no_aka:
        return ""
    candidate = _first_distinct_title((meta.imdb_info.get("aka"), meta.original_title), title)
    prepared = _first_distinct_title((meta.retrieved_aka, meta.aka), title, latin_only=True) if meta.anime else ""
    if candidate and not _latin_title(candidate):
        candidate = _first_distinct_title(_romanized_imdb_titles(meta), title, latin_only=True) or prepared or candidate
    selected = candidate or prepared
    return f"AKA {selected}" if selected else ""


def add_incomplete_pack_marker(name: str, meta: Meta, tracker: str) -> str:
    """Mark confirmed incomplete packs without changing shared season metadata."""
    if tracker not in INCOMPLETE_PACK_TRACKERS or not getattr(meta, "season_pack_incomplete", False) or not meta.tv_pack or meta.category != "TV":
        return name

    season = str(meta.season or "")
    if season.isdigit():
        season = f"S{int(season):02d}"
    if not re.fullmatch(r"S\d+(?:-S?\d+)?", season, flags=re.IGNORECASE):
        return name

    # Match the whole season token, never the prefix of S030 or S03E02.
    # Consume an existing adjacent marker so repeated formatting is idempotent.
    pattern = rf"(?<![A-Za-z0-9])({re.escape(season)})(?![A-Za-z0-9])(?:[ ._-]+INCOMPLETE(?![A-Za-z0-9]))*"

    def insert(match: re.Match[str]) -> str:
        separator = "." if name[match.end() : match.end() + 1] == "." else " "
        return f"{match.group(1)}{separator}INCOMPLETE"

    return re.sub(pattern, insert, name, count=1, flags=re.IGNORECASE)
