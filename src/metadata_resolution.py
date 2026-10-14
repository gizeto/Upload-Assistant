import asyncio
import re
from dataclasses import dataclass, fields
from typing import Any

import cli_ui

from src.console import logger, prompt_in_thread
from src.imdb import imdb_manager, imdb_match_rejection, title_similarity
from src.meta import Meta
from src.tmdb import TmdbManager


def _id(value: Any) -> int:
    try:
        return max(0, int(str(value or 0).removeprefix("tt")))
    except ValueError, TypeError:
        return 0


def _year(value: Any) -> int | None:
    match = re.search(r"\b(?:18|19|20)\d{2}\b", str(value or ""))
    return int(match[0]) if match else None


def _titles(info: dict[str, Any]) -> list[str]:
    titles = [info.get(key) for key in ("title", "original_title", "aka")]
    titles.extend(aka.get("title") for aka in info.get("akas", []) if isinstance(aka, dict))
    return [title for title in titles if isinstance(title, str) and title.strip()]


def _is_episode(info: dict[str, Any]) -> bool:
    return re.sub(r"[^a-z]", "", str(info.get("type") or "").lower()) == "tvepisode"


def _same_title(meta: Meta, category: str, tmdb_id: int) -> bool:
    return meta.metadata_identity.get("category") == category and meta.metadata_identity.get("tmdb_id") == tmdb_id


def _reset_provider(meta: Meta, provider: str) -> None:
    defaults = Meta()
    for item in fields(Meta):
        if item.name.startswith(f"{provider}_") and item.name != f"{provider}_manual":
            setattr(meta, item.name, getattr(defaults, item.name))


def _reset_title_metadata(meta: Meta) -> None:
    defaults = Meta()
    for provider in ("tmdb", "tvdb", "tvmaze", "mal"):
        if provider == "tmdb" or not getattr(meta, f"{provider}_manual"):
            _reset_provider(meta, provider)
    for name in (
        "title", "year", "aka", "retrieved_aka", "original_title", "original_language", "genres", "genre_ids", "overview", "overview_meta",
        "artwork_url", "artwork_path", "artwork_banner_path", "rehosted_artwork_url", "backdrop", "logo", "auto_episode_title",
        "imdb_info", "imdb", "imdb_tt", "imdb_rating", "we_checked_tmdb",
        "release_date", "first_air_date", "last_air_date", "origin_country", "keywords", "runtime", "youtube",
        "production_companies", "production_countries", "networks", "demographic", "anime",
    ):
        setattr(meta, name, getattr(defaults, name))
    if not meta.tvdb_manual:
        meta.we_checked_tvdb = False
    if not meta.tvmaze_manual:
        meta.we_asked_tvmaze = False


@dataclass
class Candidate:
    provider: str
    id: int
    category: str
    info: dict[str, Any]
    source: str
    loaded: bool = False
    unavailable: bool = False
    reason: str | None = None

    def rejection(self, titles: list[str], year: int | None, category: str) -> str | None:
        if self.unavailable or (self.loaded and not self.info.get("title")):
            return self.reason or "Title details are unavailable"
        if self.provider == "imdb" and self.loaded and _id(self.info.get("imdbID")) != self.id:
            return "IMDb details refer to another ID"
        if self.category != category:
            return f"Expected {category}, found {self.category}"
        info = self.info
        if self.provider == "tmdb":
            info = {**info, "type": "movie" if self.category == "MOVIE" else "tvSeries", "akas": [{"title": title} for title in _titles(info)]}
        return imdb_match_rejection(category, year, titles, info)

    def score(self, titles: list[str], year: int | None, category: str) -> float:
        self.reason = self.rejection(titles, year, category)
        if self.reason:
            return 0.0
        similarity = max((title_similarity(title, other) for title in titles for other in _titles(self.info)), default=0.0)
        return similarity + (0.05 if year and _year(self.info.get("year")) == year else 0.0)


class _MetadataLookup:
    def __init__(self, meta: Meta, filename: str, manager: TmdbManager, config: dict[str, Any], untouched_filename: str) -> None:
        self.meta = meta
        self.filename = filename
        self.manager = manager
        self.config = config
        self.untouched_filename = untouched_filename
        self.category = meta.category
        self.titles = [title for title in (filename, meta.secondary_title) if title]
        self.year = _year(meta.manual_year or meta.tvdb_series_year) if meta.category == "TV" else _year(meta.manual_year or meta.search_year or meta.year)
        self.no_imdb = bool(meta.no_imdb and not meta.automatic_imdb_rejected)
        self.candidates: list[Candidate] = []
        self.searched: set[tuple[str, str, int | None, str]] = set()

    def add(self, provider: str, id_value: Any, category: str, info: dict[str, Any], source: str) -> Candidate | None:
        candidate_id = _id(id_value)
        if not candidate_id:
            return None
        for candidate in self.candidates:
            if (candidate.provider, candidate.id, candidate.category) == (provider, candidate_id, category):
                return candidate
        candidate = Candidate(provider, candidate_id, category, info, source)
        self.candidates.append(candidate)
        return candidate

    async def initialize(self) -> dict[str, Candidate]:
        explicit: dict[str, Candidate] = {}
        existing: dict[str, Candidate | None] = {}
        for provider in ("tmdb", "imdb"):
            manual_id = getattr(self.meta, f"{provider}_manual")
            if manual_id and not _id(manual_id):
                name = "TMDb" if provider == "tmdb" else "IMDb"
                raise ValueError(f"Invalid explicit {name} ID. Correct --{provider}.")
            if provider == "imdb" and self.no_imdb:
                continue
            info = self.meta.imdb_info if provider == "imdb" else {}
            candidate = self.add(provider, manual_id or getattr(self.meta, f"{provider}_id"), self.category, info, "existing")
            existing[provider] = candidate
            if manual_id and candidate:
                explicit[provider] = candidate
        await asyncio.gather(*(self.load(candidate) for candidate in existing.values() if candidate))
        providers = []
        for provider, candidate in existing.items():
            if provider in explicit or (provider == "tmdb" and "imdb" in explicit):
                continue
            if not candidate or candidate.rejection(self.titles, self.year, self.category):
                providers.append(provider)
        await self.search(self.filename, self.year, self.category, tuple(providers))
        return explicit

    async def search(self, title: str, year: int | None, category: str, providers: tuple[str, ...] = ("tmdb", "imdb")) -> None:
        providers = tuple(provider for provider in providers if not (provider == "imdb" and self.no_imdb) and (provider, title, year, category) not in self.searched)

        async def search_provider(provider: str) -> list[dict[str, Any]]:
            self.searched.add((provider, title, year, category))
            kwargs = {"secondary_title": self.meta.secondary_title, "base_dir": self.meta.base_dir, "untouched_filename": self.untouched_filename}
            if provider == "imdb":
                return await imdb_manager.search_candidates(title, year, category, config=self.config, **kwargs)
            return await self.manager.search_candidates(title, year, category, **kwargs)

        results = await asyncio.gather(*(search_provider(provider) for provider in providers), return_exceptions=True)
        for provider, result in zip(providers, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(f"[yellow]{provider.upper()} search is unavailable: {result}[/yellow]")
                continue
            for info in result:
                if provider == "tmdb":
                    info = {
                        **info,
                        "title": info.get("title") or info.get("name", ""),
                        "original_title": info.get("original_title") or info.get("original_name", ""),
                        "year": _year(info.get("release_date") or info.get("first_air_date") or info.get("year")),
                    }
                self.add(provider, info.get("id" if provider == "tmdb" else "imdbID"), info.get("category", category), info, "search")

    async def load(self, candidate: Candidate) -> Candidate:
        if candidate.loaded:
            return candidate
        candidate.loaded = True
        try:
            if candidate.provider == "imdb":
                kwargs: dict[str, Any] = {"manual_language": self.meta.manual_language, "base_dir": self.meta.base_dir, "config": self.config, "strict": True}
                cached = self.meta.imdb_info
                if _id(cached.get("imdbID")) == candidate.id and cached.get("type") and cached.get("year"):
                    candidate.info = dict(cached)
                else:
                    candidate.info = await imdb_manager.get_imdb_info_api(candidate.id, **kwargs)
                if _is_episode(candidate.info):
                    episode = await imdb_manager.get_imdb_from_episode(candidate.id)
                    parent_id = _id((episode or {}).get("series", {}).get("series_id"))
                    if parent_id:
                        logger.info(f"[yellow]Using IMDb series tt{parent_id:07d} for episode tt{candidate.id:07d}.[/yellow]")
                        candidate.id = parent_id
                        candidate.info = await imdb_manager.get_imdb_info_api(parent_id, **kwargs)
                title_type = re.sub(r"[^a-z]", "", str(candidate.info.get("type") or "").lower())
                candidate.category = "TV" if title_type in ("tvseries", "tvminiseries", "tvepisode") else "MOVIE"
            else:
                staged = self.meta.copy()
                if not _same_title(self.meta, candidate.category, candidate.id):
                    _reset_title_metadata(staged)
                staged.category, staged.tmdb_id = candidate.category, candidate.id
                staged.imdb_id = staged.imdb_manual = 0
                staged.quickie_search = False
                staged.search_year = _year(candidate.info.get("year")) or self.year or self.meta.search_year
                staged.tvdb_id = _id(self.meta.tvdb_manual)
                await self.manager.set_tmdb_metadata(staged, self.filename, force=True)
                if not staged.tvdb_manual and _id(self.meta.tvdb_id) != _id(staged.tvdb_id):
                    tvdb_id = _id(staged.tvdb_id)
                    _reset_provider(staged, "tvdb")
                    staged.tvdb_id, staged.we_checked_tvdb = tvdb_id, False
                candidate.info = {item.name: getattr(staged, item.name) for item in fields(Meta)}
            if not candidate.info.get("title"):
                candidate.reason = "Title details are unavailable"
        except Exception as error:
            if candidate.provider == "tmdb" and _same_title(self.meta, candidate.category, candidate.id) and self.meta.title and self.meta.year:
                candidate.info = {item.name: getattr(self.meta, item.name) for item in fields(Meta)}
                logger.warning("[yellow]Using previously validated TMDb metadata while the service is unavailable.[/yellow]")
            else:
                candidate.unavailable = True
                candidate.reason = f"Details are temporarily unavailable: {error}"
                logger.warning(f"[yellow]{candidate.provider.upper()} {candidate.id}: {candidate.reason}[/yellow]")
        return candidate

    async def choose(self, provider: str, titles: list[str], year: int | None, category: str) -> Candidate | None:
        if provider == "imdb" and category == "TV":
            for candidate in self.candidates:
                if candidate.provider == "imdb" and _is_episode(candidate.info):
                    await self.load(candidate)
        while True:
            unique = {(candidate.id, candidate.category): candidate for candidate in reversed(self.candidates) if candidate.provider == provider}
            ranked = [(score, candidate) for candidate in unique.values() if (score := candidate.score(titles, year, category)) >= 0.85]
            ranked.sort(key=lambda item: item[0], reverse=True)
            if not ranked or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.10):
                return None
            candidate = await self.load(ranked[0][1])
            candidate.reason = candidate.rejection(titles, year, category)
            if not candidate.reason:
                return candidate

    async def related(self, reference: Candidate) -> Candidate | None:
        provider = "imdb" if reference.provider == "tmdb" else "tmdb"
        if provider == "imdb" and self.no_imdb:
            return None
        titles, year = _titles(reference.info), _year(reference.info.get("year"))
        category, linked_id = reference.category, reference.info.get("tmdb_imdb_id")
        source = "TMDb link" if provider == "imdb" else "IMDb link"
        if provider == "tmdb":
            category, linked_id, _language, _filename_search = await self.manager.get_tmdb_from_imdb(
                reference.id, _id(self.meta.tvdb_manual) or None, filename=reference.info.get("title") or self.filename,
                category_preference=reference.category, imdb_info=reference.info, external_only=True,
            )
        linked = self.add(provider, linked_id, category, {"year": year}, source)
        if linked:
            await self.load(linked)
            linked.reason = linked.rejection(titles, year, reference.category)
            if not linked.reason:
                linked.source = source
                return linked
            logger.warning(f"[yellow]Rejecting {source} {linked.id}: {linked.reason}[/yellow]")
        if provider == "imdb":
            candidate = await self.choose(provider, titles, year, reference.category)
            if candidate:
                return candidate
        for title in titles[:3] or [self.filename]:
            await self.search(title, year, reference.category, (provider,))
        return await self.choose(provider, titles, year, reference.category)


async def _resolve_pair(lookup: _MetadataLookup, explicit: dict[str, Candidate]) -> tuple[Candidate | None, Candidate | None]:
    tmdb, imdb = explicit.get("tmdb"), explicit.get("imdb")
    if imdb:
        await lookup.load(imdb)
        imdb.reason = imdb.rejection(_titles(imdb.info), None, imdb.category)
        if imdb.reason:
            imdb = None
    if tmdb:
        await lookup.load(tmdb)
        if tmdb.unavailable or not tmdb.info.get("title"):
            tmdb = None
    elif "imdb" not in explicit:
        tmdb = await lookup.choose("tmdb", lookup.titles, lookup.year, lookup.category)
    if imdb and "tmdb" not in explicit:
        tmdb = await lookup.related(imdb)
    elif tmdb and "imdb" not in explicit:
        imdb = await lookup.related(tmdb)
    elif not tmdb and "imdb" not in explicit and not lookup.no_imdb:
        imdb = await lookup.choose("imdb", lookup.titles, lookup.year, lookup.category)
        if imdb and "tmdb" not in explicit:
            tmdb = await lookup.related(imdb)
    if tmdb and imdb:
        imdb.reason = imdb.rejection(_titles(tmdb.info), _year(tmdb.info.get("year")), tmdb.category)
    return tmdb, imdb


async def _ask_match(lookup: _MetadataLookup, explicit: dict[str, Candidate], tmdb: Candidate | None, imdb: Candidate | None, conflict: bool) -> str | Candidate:
    if conflict:
        logger.warning("[yellow]The explicit TMDb and IMDb IDs describe different titles. Choose which ID to keep, or enter keep to confirm both.[/yellow]")
    logger.info("[bold yellow]Choose a metadata match:[/bold yellow]")
    choices = lookup.candidates
    chosen = tmdb or imdb
    if chosen and not conflict and bool(tmdb) != bool(imdb):
        logger.info(f"Keeping {chosen.provider.upper()} {chosen.id}: {chosen.info.get('title')}. Choose the missing ID, enter another ID, or skip.")
        choices = [candidate for candidate in choices if candidate.provider != chosen.provider]
    for index, candidate in enumerate(choices, 1):
        identifier = f"tt{candidate.id:07d}" if candidate.provider == "imdb" else f"{candidate.category.lower()}/{candidate.id}"
        source = "manual" if explicit.get(candidate.provider) is candidate else candidate.source
        logger.info(f"{index}. {candidate.info.get('title') or 'Unknown title'} ({candidate.info.get('year') or '?'}) — {candidate.provider.upper()} {identifier} (source: {source})")
        if candidate.reason:
            logger.info(f"[yellow]   {candidate.reason}[/yellow]")
    while True:
        answer = (await prompt_in_thread(cli_ui.ask_string, "Choose a number, enter tv/123, movie/123 or tt123, search <title>, or skip: ") or "").strip()
        if answer.lower() == "skip" or answer == "0":
            return "skip"
        if answer.lower() == "keep" and conflict:
            return "keep"
        if answer.lower().startswith("search ") and answer[7:].strip():
            return answer
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        match = re.fullmatch(r"(tt|tv/|movie/)(\d+)", answer.lower())
        if match:
            provider = "imdb" if match[1] == "tt" else "tmdb"
            category = "TV" if match[1] == "tv/" else "MOVIE" if match[1] == "movie/" else lookup.category
            candidate = lookup.add(provider, match[2], category, {}, "manual")
            if candidate:
                candidate.loaded = candidate.unavailable = False
                return candidate
        logger.info("[yellow]Enter a candidate number, a valid ID, search <title>, or skip.[/yellow]")


def _apply_metadata(lookup: _MetadataLookup, tmdb: Candidate | None, imdb: Candidate | None, explicit: dict[str, Candidate], skip: bool) -> None:
    meta = lookup.meta
    staged = meta.copy()
    key = (tmdb.category, tmdb.id) if tmdb else (imdb.category if imdb else lookup.category, 0)
    if not _same_title(meta, *key) or (not tmdb and meta.metadata_identity.get("imdb_id") != (imdb.id if imdb else 0)):
        _reset_title_metadata(staged)
    if tmdb:
        staged.update(tmdb.info)
    elif imdb:
        staged.title = imdb.info["title"]
        staged.year = _year(imdb.info.get("year"))
        staged.overview = imdb.info.get("plot", "")
        staged.original_title = imdb.info.get("aka") or staged.title
        genres = imdb.info.get("genres") or ""
        staged.genres = [genre.strip() for genre in genres.split(",") if genre.strip()] if isinstance(genres, str) else genres
        staged.artwork_url = imdb.info.get("cover") or ""
        staged.original_language = meta.manual_language or imdb.info.get("original_language")
    staged.category, staged.tmdb_id = key
    staged.tmdb_manual = tmdb.id if tmdb and explicit.get("tmdb") is tmdb else 0
    staged.imdb_id = imdb.id if imdb else 0
    staged.imdb_manual = imdb.id if imdb and explicit.get("imdb") is imdb else 0
    staged.imdb_info = dict(imdb.info) if imdb else {}
    staged.imdb_rating = str(imdb.info.get("rating") or "") if imdb else ""
    staged.imdb = str(staged.imdb_id)
    staged.imdb_tt = f"tt{staged.imdb_id:07d}" if staged.imdb_id else ""
    staged.no_imdb = lookup.no_imdb or bool(skip and not imdb)
    staged.automatic_imdb_rejected = bool(
        not imdb and not staged.no_imdb
        and any(candidate.provider == "imdb" and candidate.reason and not candidate.unavailable for candidate in lookup.candidates)
    )
    staged.quickie_search = staged.imdb_mismatch = False
    staged.mismatched_imdb_id = 0
    staged.tvdb_id = _id(meta.tvdb_manual) or _id(staged.tvdb_id)
    staged.tvmaze_id = meta.tvmaze_manual or staged.tvmaze_id or 0
    if _id(meta.imdb_id) and _id(meta.imdb_id) != staged.imdb_id and not staged.tvmaze_manual:
        _reset_provider(staged, "tvmaze")
        staged.tvmaze_id, staged.we_asked_tvmaze = 0, False
    if staged.original_language == "en" and not staged.tvdb_manual:
        staged.tvdb_series_name = None
    staged.metadata_id_sources = {candidate.provider: "manual" if explicit.get(candidate.provider) is candidate else candidate.source for candidate in (tmdb, imdb) if candidate}
    staged.metadata_identity = {"category": staged.category, "tmdb_id": staged.tmdb_id, "imdb_id": staged.imdb_id}
    staged.populate_cast()
    meta.update(staged)


async def resolve_metadata(meta: Meta, filename: str, manager: TmdbManager, config: dict[str, Any], *, untouched_filename: str = "") -> None:
    lookup = _MetadataLookup(meta, filename, manager, config, untouched_filename)
    explicit = await lookup.initialize()
    unattended = bool(meta.unattended and not meta.unattended_confirm)
    widened = skip = False
    while True:
        tmdb, imdb = await _resolve_pair(lookup, explicit)
        conflict = bool(tmdb and imdb and imdb.reason)
        if conflict and unattended:
            raise ValueError("Explicit TMDb and IMDb IDs describe different titles. Correct --tmdb or --imdb, or confirm the pair interactively.")
        if tmdb and (imdb or lookup.no_imdb) and not conflict:
            break
        if not widened and not conflict:
            missing_providers = []
            if not tmdb and "tmdb" not in explicit and "imdb" not in explicit:
                missing_providers.append("tmdb")
            if not imdb and "imdb" not in explicit:
                missing_providers.append("imdb")
            for title in dict.fromkeys([filename, meta.secondary_title]):
                if title:
                    await lookup.search(title, None, lookup.category, tuple(missing_providers))
            widened = True
            continue
        if unattended:
            if any(candidate is None and provider in explicit for provider, candidate in (("tmdb", tmdb), ("imdb", imdb))):
                raise ValueError("Unable to verify an explicit metadata ID. Retry the lookup or correct the ID.")
            missing = [name for name, unresolved in (("TMDb", not tmdb), ("IMDb", not imdb and not lookup.no_imdb)) if unresolved]
            logger.warning(f"[yellow]No reliable {' or '.join(missing)} match found; keeping the verified IDs.[/yellow]")
            break
        answer = await _ask_match(lookup, explicit, tmdb, imdb, conflict)
        if answer in ("skip", "keep"):
            if answer == "skip" and conflict:
                logger.info("[yellow]Choose which explicit ID to keep, or enter keep to confirm both.[/yellow]")
                continue
            skip = answer == "skip"
            break
        if isinstance(answer, str):
            query = answer[7:].strip()
            year_match = re.search(r"\s+\(?(?:18|19|20)\d{2}\)?$", query)
            lookup.year = _year(year_match[0]) if year_match else None
            title = query[:year_match.start()].strip() if year_match else query
            lookup.titles = [title]
            explicit = {provider: candidate for provider, candidate in explicit.items() if not conflict and not candidate.unavailable and candidate.info.get("title")}
            await lookup.search(title, lookup.year, lookup.category, tuple(provider for provider in ("tmdb", "imdb") if provider not in explicit))
            widened = True
        else:
            explicit[answer.provider] = answer
            if answer.provider == "tmdb" or conflict:
                explicit.pop("imdb" if answer.provider == "tmdb" else "tmdb", None)
            if answer.provider == "tmdb":
                lookup.category = answer.category
            widened = False
    _apply_metadata(lookup, tmdb, imdb, explicit, skip)
