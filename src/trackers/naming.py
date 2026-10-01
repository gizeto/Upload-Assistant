"""Shared helpers for opt-in tracker naming rules."""

import re
import unicodedata

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


def select_aka(meta: Meta, title: str) -> str:
    """Select a distinct source AKA, preserving available romanized titles."""
    if meta.no_aka:
        return ""
    romanized_aka = ""
    if meta.anime:
        # Preparation already resolves AniList's romaji title.
        for name in (meta.retrieved_aka, meta.aka):
            name = re.sub(r"^AKA\s+", "", (name or "").strip(), flags=re.IGNORECASE)
            if name and _latin_title(name) and title_key(name) != title_key(title):
                romanized_aka = f"AKA {name}"
                break
    for candidate in (meta.imdb_info.get("aka"), meta.original_title):
        if not isinstance(candidate, str):
            continue
        candidate = re.sub(r"^AKA\s+", "", candidate.strip(), flags=re.IGNORECASE)
        if not candidate or title_key(candidate) == title_key(title):
            continue
        if not _latin_title(candidate):
            aliases = meta.imdb_info.get("akas", [])
            for alias in aliases if isinstance(aliases, list) else []:
                if not isinstance(alias, dict):
                    continue
                language = alias.get("language")
                if language and meta.original_language:
                    try:
                        if langcodes.find(str(language)).language != langcodes.get(meta.original_language).language:
                            continue
                    except LookupError, ValueError:
                        continue
                name = alias.get("title")
                attributes = str(alias.get("attributes", "")).casefold()
                if (isinstance(name, str) and name.strip() and _latin_title(name)
                    and any(marker in attributes for marker in ("romanized", "romanised", "transliterated"))
                    and title_key(name) != title_key(title)):
                    return f"AKA {name.strip()}"
            if romanized_aka:
                return romanized_aka
        return f"AKA {candidate}"
    return romanized_aka


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
