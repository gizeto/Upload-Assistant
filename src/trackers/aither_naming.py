# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
import unicodedata
from typing import Any

import langcodes
from unidecode import unidecode

from src.meta import Meta


def title_key(title: str) -> str:
    return "".join(char for char in unidecode(title).casefold() if char.isalnum())


def _latin_title(title: str) -> bool:
    return all("LATIN" in unicodedata.name(char, "") for char in title if char.isalpha())


def select_aka(meta: Meta, title: str) -> str:
    if meta.no_aka:
        return ""
    candidates = [meta.imdb_info.get("aka"), meta.original_title]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        candidate = re.sub(r"^AKA\s+", "", candidate.strip(), flags=re.IGNORECASE)
        if not candidate or title_key(candidate) == title_key(title):
            continue
        if not _latin_title(candidate):
            aliases: Any = meta.imdb_info.get("akas", [])
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
        return f"AKA {candidate}"
    return ""
