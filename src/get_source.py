# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import importlib
import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.console import logger
from src.exceptions import WeirdSystemError
from src.meta import Meta
from src.tags import get_tag

GuessitFn = Callable[[str, dict[str, Any] | None], dict[str, Any]]
_guessit_module = importlib.import_module("guessit")
_guessit_fn: GuessitFn = _guessit_module.guessit


def guessit_fn(value: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    return _guessit_fn(value, options)


def _default_source(resolution: str) -> str:
    height = resolution.strip().lower().rstrip("pi")
    return "" if height.isdecimal() and 0 < int(height) < 720 else "BluRay"


async def _without_release_group(value: str, meta: Meta) -> str:
    value_path = Path(value)
    tag = await get_tag(value, meta, season_pack_check=True)
    if not tag:
        return value_path.name

    anime_group = f"[{tag[1:]}]"
    name = value_path.name
    if meta.anime and name.lstrip().startswith(anime_group):
        return name.lstrip()[len(anime_group):].lstrip()
    if name.casefold().endswith(tag.casefold()):
        return name[:-len(tag)]
    if value_path.stem.casefold().endswith(tag.casefold()):
        return f"{value_path.stem[:-len(tag)]}{value_path.suffix}"
    return name


async def detect_source(video: str, meta: Meta, path: str = "") -> str:
    if meta.manual_source:
        return meta.manual_source
    for value in (video, path):
        if not value:
            continue
        try:
            source_input = await _without_release_group(value, meta)
            source = guessit_fn(source_input, {"advanced": True}).get("source")
            if isinstance(source, list):
                source = source[-1] if source else None
            if source:
                return "SDTV" if source.value == "TV" and source.raw.casefold().startswith("sd") else source.value
        except Exception as e:
            logger.debug(f"Unable to detect source from {value}: {e}")
    return _default_source(meta.resolution)


async def get_source(type: str, video: str, path: str, is_disc: str, meta: Meta, folder_id: str, base_dir: str) -> tuple[str, str]:
    system = ""
    mi: dict[str, Any] = {}
    if meta.is_disc != "BDMV":
        try:
            mi_text = await asyncio.to_thread((Path(base_dir) / "tmp" / folder_id / "MediaInfo.json").read_text, encoding="utf-8")
            mi = json.loads(mi_text)
        except Exception:
            logger.debug("No mediainfo.json")
    try:
        source = await detect_source(video, meta, path)
        if not source and type in ("DVDRIP", "REMUX"):
            source = "DVD"
        if source in ("Blu-ray", "Ultra HD Blu-ray", "BluRay", "BR") or is_disc == "BDMV":
            if type == "DISC":
                source = "Blu-ray"
            elif type in ("ENCODE", "REMUX"):
                source = "BluRay"
        if is_disc == "DVD" or source in ("DVD", "dvd"):
            try:
                mediainfo = mi
                for track in mediainfo["media"]["track"]:
                    if track["@type"] == "Video":
                        system = str(track.get("Standard", ""))
                if system not in ("PAL", "NTSC"):
                    raise WeirdSystemError
            except Exception:
                try:
                    other = guessit_fn(video).get("other", [])
                    if isinstance(other, list):
                        if "PAL" in other:
                            system = "PAL"
                        elif "NTSC" in other:
                            system = "NTSC"
                except Exception:
                    system = ""
                if system == "" or system not in ("PAL", "NTSC"):
                    try:
                        framerate = str(mi["media"]["track"][1].get("FrameRate", ""))
                        if "25" in framerate or "50" in framerate:
                            system = "PAL"
                        elif framerate:
                            system = "NTSC"
                        else:
                            system = ""
                    except Exception:
                        system = ""
            finally:
                if type == "REMUX":
                    system = f"{system} DVD".strip()
                source = system or "DVD"
        if source in ("Web", "WEB") and type == "ENCODE":
            type = "WEBRIP"
        if source in ("HD-DVD", "HD DVD", "HDDVD"):
            if is_disc == "HDDVD":
                source = "HD DVD"
            if type in ("ENCODE", "REMUX"):
                source = "HDDVD"
        if type in ("WEBDL", "WEBRIP"):
            source = "Web"
        if source == "Ultra HDTV":
            source = "UHDTV"
        if type == "OTHER":
            source = {"TV": "TVRip", "VHS": "VHSRip"}.get(source, source)
    except Exception:
        logger.info(traceback.format_exc())
        source = _default_source(meta.resolution)

    return source, type
