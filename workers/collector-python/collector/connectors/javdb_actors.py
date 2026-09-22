from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from collector.connectors.javdb import Element, TreeParser

SOURCE_ID = "javdb_reference"
CONNECTOR_VERSION = "javdb-actors-html@2026-09-22.2"
MAX_ACTOR_LIST_PAGE = 1000
_LIST_PATH = re.compile(r"/actors(?:/(?:censored|uncensored|western))?/?")
_ACTOR_PATH = re.compile(r"/actors/([A-Za-z0-9_-]+)")
_AVATAR_PATH = re.compile(r"/avatars/[A-Za-z0-9]{2}/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)", re.I)


def _space(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def validate_javdb_actor_list_url(value: str) -> str:
    """Return a canonical public list URL, allowing only bounded page numbers."""
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc.lower() != "javdb.com":
        raise ValueError("JavDB actor source URL must use https://javdb.com")
    if parsed.fragment or any(character.isspace() for character in value):
        raise ValueError("JavDB actor source URL must not contain credentials, whitespace or fragment")
    if not _LIST_PATH.fullmatch(parsed.path):
        raise ValueError("JavDB actor source URL must be a public actor listing")
    query = ""
    if parsed.query:
        match = re.fullmatch(r"page=([1-9][0-9]{0,3})", parsed.query)
        if not match or int(match.group(1)) > MAX_ACTOR_LIST_PAGE:
            raise ValueError(f"JavDB actor listing supports only page=1..{MAX_ACTOR_LIST_PAGE}")
        query = f"?page={int(match.group(1))}"
    return f"https://javdb.com{parsed.path}{query}"


def javdb_actor_list_page_url(source_url: str, page: int) -> str:
    """Build a page URL without copying arbitrary query parameters."""
    canonical = validate_javdb_actor_list_url(source_url)
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_ACTOR_LIST_PAGE:
        raise ValueError(f"JavDB actor listing page must be an integer in 1..{MAX_ACTOR_LIST_PAGE}")
    return f"https://javdb.com{urlsplit(canonical).path}?page={page}"


def _actor_url(value: str, source_url: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(urljoin(source_url, value))
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.netloc.lower() != "javdb.com"
            or parsed.query or parsed.fragment or any(character.isspace() for character in value)):
        return None
    match = _ACTOR_PATH.fullmatch(parsed.path)
    if not match or _LIST_PATH.fullmatch(parsed.path):
        return None
    return match.group(1), f"https://javdb.com{parsed.path}"


def validate_javdb_actor_profile_url(value: str) -> str:
    resolved = _actor_url(value, "https://javdb.com/actors")
    if not resolved or not value.startswith("https://"):
        raise ValueError("JavDB actor profile URL must be a canonical https://javdb.com/actors/<id> URL")
    return resolved[1]


def _avatar_url(value: str, source_url: str) -> str | None:
    try:
        absolute = urljoin(source_url, unescape(value))
        parsed = urlsplit(absolute)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.netloc.lower() != "c0.jdbstatic.com":
        return None
    if parsed.query or parsed.fragment or any(character.isspace() for character in value):
        return None
    if not _AVATAR_PATH.fullmatch(parsed.path):
        return None
    return f"https://c0.jdbstatic.com{parsed.path}"


class _ActorListParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.actors: dict[str, dict[str, object]] = {}
        self.active: dict[str, object] | None = None
        self.invisible_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.invisible_depth += 1
            return
        if self.invisible_depth:
            return
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "a":
            # An anchor implicitly closes the preceding anchor in browser HTML.
            self._finish_actor()
            resolved = _actor_url(values.get("href", ""), self.source_url)
            if resolved:
                external_id, profile_url = resolved
                aliases = list(dict.fromkeys(_space(item) for item in values.get("title", "").split(",") if _space(item)))
                self.active = {
                    "external_id": external_id,
                    "profile_url": profile_url,
                    "aliases": aliases,
                    "avatar_url": None,
                    "text": [],
                    "image_alt": "",
                }
        if tag == "img" and self.active and "avatar" in values.get("class", "").lower().split():
            self.active["image_alt"] = _space(values.get("alt", ""))
            for attribute in ("src", "data-src"):
                avatar = _avatar_url(values.get(attribute, ""), self.source_url)
                if avatar:
                    self.active["avatar_url"] = avatar
                    break

    def handle_data(self, data: str) -> None:
        if self.active and not self.invisible_depth:
            self.active["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.invisible_depth = max(0, self.invisible_depth - 1)
            return
        if self.invisible_depth:
            return
        if tag == "a":
            self._finish_actor()

    def _finish_actor(self) -> None:
        if not self.active:
            return
        actor = self.active
        self.active = None
        if not actor["aliases"]:
            name = _space(" ".join(actor["text"])) or actor["image_alt"]
            actor["aliases"] = [name] if name else []
        external_id = str(actor["external_id"])
        previous = self.actors.get(external_id)
        if previous:
            previous["aliases"] = list(dict.fromkeys([*previous["aliases"], *actor["aliases"]]))
            if not previous["avatar_url"]:
                previous["avatar_url"] = actor["avatar_url"]
        else:
            self.actors[external_id] = actor

    def close(self) -> None:
        super().close()
        self._finish_actor()


def _check_html(html_text: str) -> None:
    lowered = html_text.lower()
    if any(marker in lowered for marker in ("cf-chl-", "just a moment", "cloudflare ray id", "captcha")):
        raise ValueError("JavDB actor sample is a challenge page")
    if len(html_text.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("JavDB actor sample exceeds 2 MiB")


def _actor_candidate(
    external_id: str, payload: dict[str, object], avatar_url: str | None,
    *, source_url: str, checked_at: str,
) -> dict[str, object]:
    media = []
    if avatar_url:
        media_id = hashlib.sha256(f"{SOURCE_ID}\0{external_id}\0avatar\0{avatar_url}".encode()).hexdigest()
        media.append({
            "media_candidate_id": media_id,
            "candidate_url": avatar_url,
            "source_page_url": payload["profile_url"],
            "purpose": "avatar",
            "display_position": 0,
            "rights_status": "needs_review",
            "download_status": "pending",
            "review_status": "pending",
            "checked_at": checked_at,
            "connector_version": CONNECTOR_VERSION,
        })
    stable = {"source_id": SOURCE_ID, "external_id": external_id, "payload": payload, "media": media}
    # Observation timestamps and pipeline state are metadata, not source content.
    hashable = {**stable, "media": [
        {key: item[key] for key in ("candidate_url", "source_page_url", "purpose", "display_position")}
        for item in media
    ]}
    digest = hashlib.sha256(json.dumps(hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        **stable,
        "entity_type": "performer",
        "content_hash": f"sha256:{digest}",
        "provenance": {"source_url": source_url, "checked_at": checked_at, "connector_version": CONNECTOR_VERSION},
    }


def parse_javdb_actor_list(html_text: str, *, source_url: str, checked_at: str) -> list[dict[str, object]]:
    canonical_url = validate_javdb_actor_list_url(source_url)
    _check_html(html_text)
    parser = _ActorListParser(canonical_url)
    parser.feed(html_text)
    parser.close()
    candidates = []
    for external_id, actor in sorted(parser.actors.items()):
        aliases = list(actor["aliases"])
        if not aliases:
            continue
        payload = {"name": aliases[0], "aliases": aliases, "profile_url": actor["profile_url"]}
        candidates.append(_actor_candidate(external_id, payload, actor["avatar_url"], source_url=canonical_url, checked_at=checked_at))
    if not candidates:
        raise ValueError("JavDB actor sample contains no actor records")
    return candidates


_PROFILE_FIELDS = {
    "birth_date": "birthDate", "birth_place": "birthPlace", "nationality": "nationality",
    "height": "height", "weight": "weight", "blood_type": "bloodType",
    "measurements": "bodyMeasurements", "debut_date": "debutDate",
}
_EXCLUDED_PROFILE_CLASSES = {
    "actor-box", "actor-list", "related", "related-actors", "recommendations",
    "recommended-actors", "video-detail", "movie-list", "video-list",
}


def _profile_node_allowed(node: Element, source_url: str) -> bool:
    current: Element | None = node
    while current is not None:
        if current.tag in {"aside", "nav", "footer", "script", "style"} or current.classes & _EXCLUDED_PROFILE_CLASSES:
            return False
        if current.attrs.get("data-actor-id") not in (None, source_url.rsplit("/", 1)[-1]):
            return False
        if _is_person(current) and current.attrs.get("itemid") not in (None, source_url):
            return False
        if current.tag == "a" and current.attrs.get("href"):
            linked = _actor_url(current.attrs["href"], source_url)
            if linked and linked[1] != source_url:
                return False
        current = current.parent
    return True


def _field_text(node: Element) -> str:
    explicit = node.attrs.get("content") or node.attrs.get("datetime")
    if explicit:
        return _space(explicit)
    parts: list[str] = []

    def visible_text(element: Element) -> None:
        if element.tag in {"script", "style", "template", "noscript"}:
            return
        for child in element.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                visible_text(child)

    visible_text(node)
    return _space(" ".join(parts))


def _is_person(node: Element) -> bool:
    return node.attrs.get("itemtype", "").rstrip("/") in {"https://schema.org/Person", "http://schema.org/Person"}


def _profile_properties(node: Element, scope: Element | None) -> list[str]:
    if scope is None:
        return []
    for parent in _ancestors(node):
        if parent is scope:
            return node.attrs.get("itemprop", "").split()
        if "itemscope" in parent.attrs or parent.attrs.get("itemtype"):
            return []
    return []


def parse_javdb_actor_profile(html_text: str, *, source_url: str, checked_at: str) -> dict[str, object]:
    """Read explicit actor fields only; unsupported/ambiguous layouts fail closed.

    Person microdata or actor-profile/actor-info containers bind optional fields
    to the current actor. Explicit data-performer-field attributes and a single
    h1 can also provide a name; document titles and Open Graph are never names.
    """
    canonical = validate_javdb_actor_profile_url(source_url)
    _check_html(html_text)
    parser = TreeParser()
    parser.feed(html_text)
    parser.close()
    nodes = [node for node in parser.root.descendants() if _profile_node_allowed(node, canonical)]
    scopes = [node for node in nodes if (
        _is_person(node)
        or node.classes & {"actor-profile", "actor-info", "performer-profile"}
        or node.attrs.get("id") in {"actor-profile", "actor-info", "performer-profile"}
    )]
    # Nested containers describe one subject. Distinct unnamed Person containers
    # cannot safely establish which subject belongs to the requested profile.
    persons = [node for node in scopes if _is_person(node)]
    roots = persons or [node for node in scopes if not any(parent is other for parent in _ancestors(node) for other in scopes)]
    if len(roots) > 1:
        identified = [node for node in roots if node.attrs.get("itemid") == canonical or node.attrs.get("data-actor-id") == canonical.rsplit("/", 1)[-1]]
        if len(identified) != 1:
            raise ValueError("unsupported JavDB actor profile: ambiguous actor containers")
        scope = identified[0]
    else:
        scope = roots[0] if roots else None
    scoped = [node for node in nodes if scope is None or node is scope or any(parent is scope for parent in _ancestors(node))]
    explicit_names = [node for node in scoped if (
        node.attrs.get("data-performer-field") == "name"
        or node.classes & {"actor-name", "performer-name"}
        or "name" in _profile_properties(node, scope)
    )]
    name_nodes = explicit_names or [node for node in scoped if node.tag == "h1"]
    names = list(dict.fromkeys(_field_text(node) for node in name_nodes if _field_text(node)))
    if len(names) != 1 or len(names[0]) > 160 or names[0].lower() in {"not found", "404", "403", "forbidden", "access denied", "actors", "javdb"}:
        raise ValueError("unsupported JavDB actor profile: missing or ambiguous explicit actor name")
    aliases = [names[0]]
    payload: dict[str, object] = {"name": names[0], "aliases": aliases, "profile_url": canonical}
    avatar = None
    for node in scoped:
        field = node.attrs.get("data-performer-field")
        props = _profile_properties(node, scope)
        if field in {"aliases", "alias"} or "alternateName" in props:
            aliases.extend(value for part in re.split(r"[,，;；]", _field_text(node)) if (value := _space(part)))
        for key, itemprop in _PROFILE_FIELDS.items():
            if field == key or itemprop in props:
                value = _field_text(node)
                if value and len(value) <= 200:
                    payload.setdefault(key, value)
        is_avatar = field in {"avatar", "avatar_url"} or "actor-avatar" in node.classes or (scope is not None and ("avatar" in node.classes or "image" in props))
        if avatar is None and is_avatar:
            for attribute in ("src", "data-src", "content", "href"):
                avatar = _avatar_url(node.attrs.get(attribute, ""), canonical)
                if avatar:
                    break
    payload["aliases"] = list(dict.fromkeys(aliases))
    return _actor_candidate(canonical.rsplit("/", 1)[-1], payload, avatar, source_url=canonical, checked_at=checked_at)


def _ancestors(node: Element) -> Iterator[Element]:
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def parse_javdb_actors(html_text: str, *, source_url: str, checked_at: str) -> list[dict[str, object]]:
    """Dispatch public actor lists and profiles through their strict parsers."""
    if _LIST_PATH.fullmatch(urlsplit(source_url).path):
        return parse_javdb_actor_list(html_text, source_url=source_url, checked_at=checked_at)
    return [parse_javdb_actor_profile(html_text, source_url=source_url, checked_at=checked_at)]
