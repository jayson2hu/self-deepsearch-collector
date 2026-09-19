from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

SOURCE_ID = "javdb_reference"
CONNECTOR_VERSION = "javdb-actors-html@2026-09-18.1"


def _space(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _source_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "javdb.com":
        raise ValueError("JavDB actor source URL must use https://javdb.com")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("JavDB actor source URL must not contain credentials, query or fragment")
    if not re.fullmatch(r"/actors(?:/(?:censored|uncensored|western))?/?", parsed.path):
        raise ValueError("JavDB actor source URL must be a public actor listing")
    return f"https://javdb.com{parsed.path or '/actors'}"


def _avatar_url(value: str, source_url: str) -> str | None:
    absolute = urljoin(source_url, unescape(value))
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or parsed.hostname != "c0.jdbstatic.com":
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if not re.fullmatch(r"/avatars/[A-Za-z0-9]{2}/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)", parsed.path, re.I):
        return None
    return absolute


class _ActorListParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.actors: dict[str, dict[str, object]] = {}
        self.active: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "a" and values.get("href"):
            absolute = urljoin(self.source_url, values["href"])
            match = re.fullmatch(r"/actors/([A-Za-z0-9_-]+)", urlparse(absolute).path)
            if match and values.get("title"):
                aliases = [_space(item) for item in values["title"].split(",") if _space(item)]
                self.active = {
                    "external_id": match.group(1),
                    "profile_url": f"https://javdb.com/actors/{match.group(1)}",
                    "aliases": aliases,
                    "avatar_url": None,
                }
        if tag == "img" and self.active and "avatar" in values.get("class", "").lower():
            avatar = _avatar_url(values.get("src", "") or values.get("data-src", ""), self.source_url)
            if avatar:
                self.active["avatar_url"] = avatar

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self.active:
            return
        actor = self.active
        self.active = None
        if actor["aliases"] and actor["avatar_url"]:
            self.actors[str(actor["external_id"])] = actor


def parse_javdb_actor_list(html_text: str, *, source_url: str, checked_at: str) -> list[dict[str, object]]:
    canonical_url = _source_url(source_url)
    lowered = html_text.lower()
    if any(marker in lowered for marker in ("cf-chl-", "just a moment", "cloudflare ray id", "captcha")):
        raise ValueError("JavDB actor sample is a challenge page")
    if len(html_text.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("JavDB actor sample exceeds 2 MiB")
    parser = _ActorListParser(canonical_url)
    parser.feed(html_text)
    candidates = []
    for external_id, actor in sorted(parser.actors.items()):
        aliases = list(actor["aliases"])
        payload = {"name": aliases[0], "aliases": aliases, "profile_url": actor["profile_url"]}
        avatar_url = str(actor["avatar_url"])
        media_id = hashlib.sha256(f"{SOURCE_ID}\0{external_id}\0avatar\0{avatar_url}".encode()).hexdigest()
        media = [{
            "media_candidate_id": media_id,
            "candidate_url": avatar_url,
            "source_page_url": actor["profile_url"],
            "purpose": "avatar",
            "display_position": 0,
            "rights_status": "needs_review",
            "download_status": "pending",
            "review_status": "pending",
            "checked_at": checked_at,
            "connector_version": CONNECTOR_VERSION,
        }]
        stable = {"source_id": SOURCE_ID, "external_id": external_id, "payload": payload, "media": media}
        digest = hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        candidates.append({
            **stable,
            "entity_type": "performer",
            "content_hash": f"sha256:{digest}",
            "provenance": {"source_url": canonical_url, "checked_at": checked_at, "connector_version": CONNECTOR_VERSION},
        })
    if not candidates:
        raise ValueError("JavDB actor sample contains no avatar records")
    return candidates
