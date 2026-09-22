from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

SOURCE_ID = "jable_reference"
CONNECTOR_VERSION = "jable-html@2026-09-22.1"


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
    if parsed.port not in (None, 443) or not re.fullmatch(r"/(?:contents/)?models/[^?#]+\.(?:jpe?g|png|webp)", parsed.path, re.I):
        return None
    if any(part in {".", ".."} for part in unquote(parsed.path).split("/")) or "%" in parsed.path or "\\" in parsed.path:
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


def _observed_name(value: str) -> str | None:
    value = _clean_name(value)
    if not value or len(value) > 120:
        return None
    if re.search(r"jable|\b(?:models?|actresses?|performers?|videos?|porn|free|hd|forbidden|search)\b|not found|access denied|最新|熱門|热门|高清|免費|免费|影片|所有女優|所有女优", value, re.I):
        return None
    return None if value.isdecimal() else value


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: _Node | None = field(default=None, repr=False)
    children: list[_Node | str] = field(default_factory=list)

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.nodes()

    def text(self) -> str:
        if self.tag in {"script", "style", "noscript"}:
            return ""
        return _space(" ".join(child.text() if isinstance(child, _Node) else child for child in self.children))

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _JableHTMLParser(HTMLParser):
    """Small DOM used to keep model headers separate from recommendation links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {key.lower(): value or "" for key, value in attrs}, self.current)
        self.current.children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self.current.children.append(data)

    def handle_endtag(self, tag: str) -> None:
        for node in (self.current, *self.current.ancestors()):
            if node.tag == tag and node.parent is not None:
                self.current = node.parent
                break


def _image_url(node: _Node, source_url: str) -> str | None:
    for key in ("data-src", "data-lazy-src", "data-original", "src"):
        if url := _media_url(node.attrs.get(key, ""), source_url):
            return url
    return None


def _listing_models(root: _Node, source_url: str) -> dict[str, dict[str, object]]:
    models: dict[str, dict[str, object]] = {}
    for link in root.nodes():
        if link.tag != "a" or not link.attrs.get("href"):
            continue
        absolute = urljoin(source_url, link.attrs["href"])
        external_id = _model_id(absolute)
        if not external_id:
            continue
        try:
            profile_url = _canonical_page_url(absolute)
        except ValueError:
            continue
        descendants = list(link.nodes())
        headings = [node.text() for node in descendants if node.tag in {"h3", "h4", "h5", "h6"} or "title" in node.attrs.get("class", "").split()]
        portraits = [(node, url) for node in descendants if node.tag == "img" and (url := _image_url(node, source_url))]
        name = next((name for raw in [*headings, *[node.attrs.get("alt", "") for node, _ in portraits], link.text()] if (name := _observed_name(raw))), None)
        if name is None:
            continue
        model = models.setdefault(external_id, {"profile_url": profile_url})
        model["name"] = name
        if portraits:
            model["avatar_url"] = portraits[0][1]
        if count := re.search(r"([\d,]+)\s*部", link.text()):
            model["work_count"] = int(count.group(1).replace(",", ""))
    return models


_PROFILE_FIELDS = {
    "name": "name", "aliases": "aliases", "alternateName": "aliases",
    "biography": "biography", "description": "biography", "birthDate": "birth_date", "birth_date": "birth_date",
    "birthPlace": "birth_place", "birth_place": "birth_place", "nationality": "nationality",
    "height": "height", "weight": "weight", "blood_type": "blood_type", "measurements": "measurements", "debut_date": "debut_date",
    "work_count": "work_count",
}
_PROFILE_SCOPES = {"model-info", "model-header", "model-profile", "performer-profile", "profile-header"}


def _profile_scope(node: _Node) -> bool:
    return ("data-performer-profile" in node.attrs
            or bool(_PROFILE_SCOPES.intersection(node.attrs.get("class", "").split()))
            or node.attrs.get("itemtype", "").rstrip("/").endswith("/Person"))


def _outside_other_models(node: _Node, source_url: str) -> bool:
    for ancestor in (node, *node.ancestors()):
        if ancestor.tag == "a" and ancestor.attrs.get("href"):
            target = urljoin(source_url, ancestor.attrs["href"])
            if target.rstrip("/") != source_url.rstrip("/"):
                return False
        marker = " ".join((ancestor.attrs.get("class", ""), ancestor.attrs.get("id", "")))
        if re.search(r"(?:related|recommend|video-card|video-item)", marker, re.I):
            return False
        if ancestor.attrs.get("itemtype", "").rstrip("/").endswith(("/VideoObject", "/Movie", "/Product")):
            return False
    return True


def _profile_model(root: _Node, source_url: str) -> dict[str, object]:
    model: dict[str, object] = {"profile_url": source_url}
    nodes = list(root.nodes())
    for node in nodes:
        if not _outside_other_models(node, source_url):
            continue
        explicit_field = node.attrs.get("data-performer-field", "")
        scoped = any(_profile_scope(ancestor) for ancestor in (node, *node.ancestors()))
        property_name = explicit_field or (node.attrs.get("itemprop", "") if scoped else "")
        field_name = _PROFILE_FIELDS.get(property_name)
        if field_name:
            # Explicit projections and Person microdata preserve observed text;
            # general description/OG marketing metadata is not a biography.
            raw = node.attrs.get("content") or node.attrs.get("datetime") or node.text()
            value = _space(raw)
            if field_name == "name":
                if name := _observed_name(value):
                    model["name"] = name
            elif field_name == "aliases":
                aliases = [part for raw in re.split(r"[,，、;；\n]", value) if (part := _space(raw))]
                model["aliases"] = sorted(set([*model.get("aliases", []), *aliases]))
            elif field_name == "work_count":
                if re.fullmatch(r"[\d,]+(?:\s*部(?:影片)?)?", value):
                    model["work_count"] = int(re.match(r"[\d,]+", value).group().replace(",", ""))
            elif value:
                model[field_name] = value
        marker = set(node.attrs.get("class", "").split())
        if "name" not in model and field_name in {None, "name"} and (
            node.tag == "h1" or (scoped and node.tag in {"h2", "h3"})
            or marker.intersection({"model-name", "performer-name", "profile-name", "actress-name"})
        ):
            if name := _observed_name(node.text()):
                model["name"] = name
        if node.tag == "img" and "avatar_url" not in model:
            image_marker = " ".join((node.attrs.get("class", ""), node.attrs.get("id", ""))).lower()
            if scoped or any(token in image_marker for token in ("avatar", "model", "profile", "performer", "actress")):
                if url := _image_url(node, source_url):
                    model["avatar_url"] = url
    if "avatar_url" not in model:
        for node in nodes:
            if node.tag == "meta" and node.attrs.get("property", "").lower() == "og:image":
                if url := _media_url(node.attrs.get("content", ""), source_url):
                    model["avatar_url"] = url
                    break
    if not model.get("name"):
        # An exact self-link supplies an observed name; never derive one from a slug.
        self_card = _listing_models(root, source_url).get(_model_id(source_url), {})
        if self_card.get("name"):
            model["name"] = self_card["name"]
    if len(model) == 1:
        raise ValueError("Jable profile contains no observed performer fields")
    return model


def parse_jable_html(html_text: str, *, source_url: str, checked_at: str) -> list[dict[str, object]]:
    canonical_url = _canonical_page_url(source_url)
    lowered = html_text.lower()
    if any(marker in lowered for marker in ("cf-chl-", "just a moment", "cloudflare ray id", "/captcha/")):
        raise ValueError("Jable sample is a Cloudflare challenge page")
    if len(html_text.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Jable sample exceeds 2 MiB")
    parser = _JableHTMLParser()
    parser.feed(html_text)
    parser.close()

    profile_id = _model_id(canonical_url)
    models = ({profile_id: _profile_model(parser.root, canonical_url)} if profile_id
              else _listing_models(parser.root, canonical_url))
    candidates: list[dict[str, object]] = []
    for external_id, model in sorted(models.items()):
        media: list[dict[str, object]] = []
        avatar_url = model.get("avatar_url")
        if avatar_url:
            media.append(_media_candidate(external_id, str(avatar_url), canonical_url, "avatar", 0, checked_at))
        payload = {key: value for key, value in model.items() if key != "avatar_url"}
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
