from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

SOURCE_ID = "jable_reference"
CONNECTOR_VERSION = "jable-html@2026-09-19.1"


def _space(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _canonical_page_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"jable.tv", "www.jable.tv"}:
        raise ValueError("Jable source URL must use public HTTPS jable.tv")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Jable source URL must not contain credentials, query or fragment")
    if parsed.port not in (None, 443) or not re.fullmatch(r"/models/(?:[^/]+/?)?", parsed.path):
        raise ValueError("Jable source URL must be a public model listing or profile")
    path = re.sub(r"/+", "/", parsed.path or "/")
    return f"https://jable.tv{path}"


def _media_url(value: str, source_url: str) -> str | None:
    if not value or value.startswith(("data:", "blob:")):
        return None
    absolute = urljoin(source_url, unescape(value))
    parsed = urlparse(absolute)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "jable.tv" or host.endswith(".jable.tv")):
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if parsed.port not in (None, 443) or not re.match(r"/(?:contents/)?models/", parsed.path):
        return None
    if re.search(r"(?:logo|favicon|icon|sprite|banner)", parsed.path, re.I):
        return None
    return absolute


def _model_id(value: str) -> str | None:
    path = urlparse(value).path
    match = re.fullmatch(r"/models/([^/]+)/?", path)
    if not match:
        return None
    external_id = _space(unquote(match.group(1)))
    if external_id.isdecimal():
        return None
    return external_id or None


def _clean_name(value: str) -> str:
    value = _space(value)
    value = re.sub(r"\s*[-|·]\s*Jable(?:\.TV)?(?:\s*[-|·].*)?$", "", value, flags=re.I)
    value = re.sub(r"^(?:Jable(?:\.TV)?\s*[-|·]\s*)", "", value, flags=re.I)
    return value.strip(" -|·")


class _JableHTMLParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.models: dict[str, dict[str, object]] = {}
        self.avatar_images: list[str] = []
        self._capture_title = False
        self._capture_heading = False
        self._active_link: dict[str, object] | None = None
        self._capture_model_name = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            if key and values.get("content"):
                self.meta[key] = values["content"]
            return
        if tag == "title":
            self._capture_title = True
            return
        if tag in {"h1", "h2"} and not self.heading_parts:
            self._capture_heading = True
        if self._active_link and (tag in {"h3", "h4", "h5", "h6"} or "title" in values.get("class", "").split()):
            self._capture_model_name = True
        if tag == "a" and values.get("href"):
            absolute = urljoin(self.source_url, values["href"])
            model_id = _model_id(absolute)
            if model_id:
                try:
                    absolute = _canonical_page_url(absolute)
                except ValueError:
                    return
                self._active_link = {"url": absolute, "id": model_id, "text": [], "name": [], "image": None, "alt": ""}
        if tag != "img":
            return
        image_value = next((values.get(key) for key in ("data-src", "data-lazy-src", "data-original", "src") if values.get(key)), "")
        image_url = _media_url(image_value, self.source_url)
        if not image_url:
            return
        image_alt = _space(values.get("alt", ""))
        if self._active_link:
            self._active_link["image"] = image_url
            self._active_link["alt"] = image_alt
            return
        marker = " ".join((values.get("class", ""), values.get("id", ""), image_alt)).lower()
        if any(token in marker for token in ("avatar", "model", "profile", "performer", "actress")):
            self.avatar_images.append(image_url)

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self.title_parts.append(data)
        if self._capture_heading:
            self.heading_parts.append(data)
        if self._active_link:
            self._active_link["text"].append(data)  # type: ignore[union-attr]
            if self._capture_model_name:
                self._active_link["name"].append(data)  # type: ignore[union-attr]

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._capture_title = False
        if tag in {"h1", "h2"}:
            self._capture_heading = False
        if tag in {"h3", "h4", "h5", "h6", "div", "span"}:
            self._capture_model_name = False
        if tag != "a" or not self._active_link:
            return
        link = self._active_link
        self._active_link = None
        image = link.get("image")
        external_id = str(link["id"])
        name = _clean_name(" ".join(link["name"]) or str(link.get("alt") or "") or " ".join(link["text"]))
        existing = self.models.get(external_id, {})
        self.models[external_id] = {
            "name": name or existing.get("name") or external_id,
            "profile_url": _canonical_page_url(str(link["url"])),
            "avatar_url": str(image) if image else existing.get("avatar_url"),
            "work_count": (int(count.group(1).replace(",", "")) if (count := re.search(r"([\d,]+)\s*部", " ".join(link["text"]))) else None),
        }


def parse_jable_html(html_text: str, *, source_url: str, checked_at: str) -> list[dict[str, object]]:
    canonical_url = _canonical_page_url(source_url)
    lowered = html_text.lower()
    if any(marker in lowered for marker in ("cf-chl-", "just a moment", "cloudflare ray id", "/captcha/")):
        raise ValueError("Jable sample is a Cloudflare challenge page")
    if len(html_text.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Jable sample exceeds 2 MiB")
    parser = _JableHTMLParser(canonical_url)
    parser.feed(html_text)

    profile_id = _model_id(canonical_url)
    if profile_id:
        name = _clean_name(
            parser.meta.get("og:title", "")
            or " ".join(parser.heading_parts)
            or " ".join(parser.title_parts)
            or profile_id
        )
        avatar_url = _media_url(parser.meta.get("og:image", ""), canonical_url)
        if not avatar_url and parser.avatar_images:
            avatar_url = parser.avatar_images[0]
        parser.models[profile_id] = {
            "name": name or profile_id,
            "profile_url": canonical_url,
            "avatar_url": avatar_url,
        }

    candidates: list[dict[str, object]] = []
    for external_id, model in sorted(parser.models.items()):
        profile_url = str(model["profile_url"])
        media: list[dict[str, object]] = []
        avatar_url = model.get("avatar_url")
        if avatar_url:
            media.append(_media_candidate(external_id, str(avatar_url), canonical_url, "avatar", 0, checked_at))
        payload = {"name": str(model["name"]), "profile_url": profile_url}
        if model.get("work_count") is not None:
            payload["work_count"] = model["work_count"]
        stable = {"source_id": SOURCE_ID, "external_id": external_id, "payload": payload, "media": media}
        candidates.append({
            **stable,
            "entity_type": "performer",
            "content_hash": performer_content_hash(stable),
            "provenance": {"source_url": canonical_url, "checked_at": checked_at, "connector_version": CONNECTOR_VERSION},
        })
    if not candidates:
        raise ValueError("Jable sample contains no public model records")
    return candidates


def performer_content_hash(candidate: dict[str, object]) -> str:
    """Hash source content, not collection time or the local download/review state."""
    stable = {key: candidate[key] for key in ("source_id", "external_id", "payload")}
    stable["media"] = sorted([
        {key: item[key] for key in ("candidate_url", "purpose", "display_position")}
        for item in candidate["media"]
    ], key=lambda item: (item["purpose"], item["display_position"], item["candidate_url"]))
    digest = hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"


def _media_candidate(
    external_id: str,
    candidate_url: str,
    source_page_url: str,
    purpose: str,
    position: int,
    checked_at: str,
) -> dict[str, object]:
    digest = hashlib.sha256(f"{SOURCE_ID}\0{external_id}\0{purpose}\0{candidate_url}".encode()).hexdigest()
    return {
        "media_candidate_id": digest,
        "candidate_url": candidate_url,
        "source_page_url": source_page_url,
        "purpose": purpose,
        "display_position": position,
        "rights_status": "needs_review",
        "download_status": "pending",
        "review_status": "pending",
        "checked_at": checked_at,
        "connector_version": CONNECTOR_VERSION,
    }
