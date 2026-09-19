from __future__ import annotations

import re
import hashlib
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urlparse

from collector.contracts import ContractError, content_hash, validate_candidate

SOURCE_ID = "javdb_reference"
CONNECTOR_VERSION = "javdb-html@2026-09-16.1"
ALLOWED_HOSTS = {"javdb.com", "www.javdb.com"}
MEDIA_HOST = "c0.jdbstatic.com"
DETAIL_PATH = re.compile(r"^/v/([A-Za-z0-9_-]+)$")
MEDIA_PATH = re.compile(r"^/(covers|samples)/[A-Za-z0-9_-]+/([A-Za-z0-9_-]+?)(?:_l_(\d+))?\.(jpe?g|png|webp)$", re.I)
CODE_PATTERN = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*")
VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class ParseError(ContractError):
    """The supplied page is not a supported JavDB detail page."""


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    parent: Element | None = None
    children: list[Element | str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def text(self) -> str:
        parts: list[str] = []

        def visit(node: Element | str) -> None:
            if isinstance(node, str):
                parts.append(node)
                return
            for child in node.children:
                visit(child)

        visit(self)
        return " ".join("".join(parts).split())

    def descendants(self, tag: str | None = None, class_name: str | None = None) -> Iterable[Element]:
        for child in self.children:
            if isinstance(child, str):
                continue
            if (tag is None or child.tag == tag) and (class_name is None or class_name in child.classes):
                yield child
            yield from child.descendants(tag=tag, class_name=class_name)

    def first(self, tag: str | None = None, class_name: str | None = None) -> Element | None:
        return next(self.descendants(tag=tag, class_name=class_name), None)


class TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag.lower(), {key.lower(): value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(element)
        if element.tag not in VOID_ELEMENTS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        target = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == target:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def normalize_code(raw: str) -> str:
    value = unicodedata.normalize("NFKC", raw).strip().upper()
    value = re.sub(r"[.\s_‐‑–—−]+", "-", value)
    return re.sub(r"-+", "-", value)


def _parse_url(source_url: str) -> tuple[str, str]:
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise ParseError("source URL must be an unauthenticated HTTPS JavDB URL")
    match = DETAIL_PATH.fullmatch(parsed.path)
    if not match or parsed.query or parsed.fragment:
        raise ParseError("source URL must be a canonical JavDB /v/<id> detail URL")
    return match.group(1), f"https://javdb.com{parsed.path}"


def validate_detail_url(source_url: str) -> tuple[str, str]:
    return _parse_url(source_url)


def _label_values(detail: Element) -> dict[str, Element]:
    values: dict[str, Element] = {}
    for block in detail.descendants("div", "panel-block"):
        label_element = block.first("strong")
        value_element = block.first("span", "value")
        if label_element is None or value_element is None:
            continue
        label = label_element.text().strip().rstrip(":：")
        if label:
            values[label] = value_element
    return values


def _page_title(root: Element) -> str:
    head_title = root.first("title")
    if head_title is None:
        return "JavDB detail page"
    value = head_title.text()
    return re.sub(r"\s*\|\s*JavDB.*$", "", value).strip() or "JavDB detail page"


def parse_detail(html: str, *, source_url: str, checked_at: str) -> dict[str, object]:
    external_id, canonical_url = _parse_url(source_url)
    parser = TreeParser()
    parser.feed(html)
    parser.close()

    detail = parser.root.first("div", "video-detail")
    if detail is None:
        raise ParseError("missing video-detail anchor; page may be a challenge or unsupported layout")
    title_element = detail.first("strong", "current-title")
    title = title_element.text().strip() if title_element else ""
    values = _label_values(detail)

    code_element = values.get("番號") or values.get("番号")
    raw_code = code_element.text().strip() if code_element else ""
    canonical_code = normalize_code(raw_code)
    if not CODE_PATTERN.fullmatch(canonical_code):
        raise ParseError("missing or invalid canonical code")
    if not title:
        raise ParseError("missing work title")

    date_element = values.get("日期") or values.get("發行日期") or values.get("发行日期")
    release_date = date_element.text().strip() if date_element else None
    if release_date:
        try:
            date.fromisoformat(release_date)
        except ValueError as exc:
            raise ParseError("release date is not an ISO calendar date") from exc

    studio_element = values.get("片商") or values.get("廠牌") or values.get("厂牌")
    studio_name = studio_element.text().strip() if studio_element else None

    actor_element = values.get("演員") or values.get("演员")
    performer_aliases: list[str] = []
    if actor_element is not None:
        for anchor in actor_element.descendants("a"):
            if not anchor.attrs.get("href", "").startswith("/actors/"):
                continue
            alias = anchor.text().strip()
            if alias and alias not in performer_aliases:
                performer_aliases.append(alias)

    payload = {
        "canonical_code": canonical_code,
        "title": title,
        "release_date": release_date,
        "studio_name": studio_name,
        "performer_aliases": performer_aliases,
    }
    digest = content_hash(payload)
    candidate: dict[str, object] = {
        "source_id": SOURCE_ID,
        "external_id": external_id,
        "entity_type": "work",
        "operation": "upsert",
        "source_updated_at": None,
        "idempotency_key": f"{SOURCE_ID}:work:{external_id}:{digest}",
        "content_hash": digest,
        "payload": payload,
        "provenance": {
            "source_type": "public_web",
            "source_url": canonical_url,
            "source_title": _page_title(parser.root),
            "checked_at": checked_at,
            "confidence": 0.85,
            "rights_status": "needs_review",
            "connector_version": CONNECTOR_VERSION,
            "field_evidence": {
                "canonical_code": {"path": ".movie-panel-info .panel-block[番號] .value", "raw": raw_code},
                "title": {"path": ".video-detail .current-title", "raw": title},
                "release_date": {"path": ".movie-panel-info .panel-block[日期] .value", "raw": release_date},
                "studio_name": {"path": ".movie-panel-info .panel-block[片商] .value", "raw": studio_name},
                "performer_aliases": {"path": ".movie-panel-info .panel-block[演員] a[href^='/actors/']", "raw": performer_aliases},
            },
        },
    }
    validate_candidate(candidate)
    return candidate


def _media_url(value: str, *, external_id: str, expected_kind: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != MEDIA_HOST or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ParseError("media URL is outside the approved JavDB static host")
    match = MEDIA_PATH.fullmatch(parsed.path)
    if not match or match.group(1) != expected_kind or match.group(2) != external_id:
        raise ParseError("media URL does not match the source object")
    return f"https://{MEDIA_HOST}{parsed.path}"


def extract_media_candidates(html: str, *, source_url: str, checked_at: str) -> list[dict[str, object]]:
    external_id, canonical_url = _parse_url(source_url)
    parser = TreeParser()
    parser.feed(html)
    parser.close()
    detail = parser.root.first("div", "video-detail")
    if detail is None:
        raise ParseError("missing video-detail anchor; page may be a challenge or unsupported layout")

    candidates: list[dict[str, object]] = []
    cover_column = detail.first("div", "column-video-cover")
    cover_image = cover_column.first("img", "video-cover") if cover_column else None
    if cover_image and cover_image.attrs.get("src"):
        cover_url = _media_url(cover_image.attrs["src"], external_id=external_id, expected_kind="covers")
        candidates.append({
            "media_candidate_id": hashlib.sha256(cover_url.encode("utf-8")).hexdigest(),
            "source_id": SOURCE_ID,
            "external_id": external_id,
            "entity_type": "work",
            "candidate_url": cover_url,
            "source_page_url": canonical_url,
            "source_type": "web_page",
            "purpose": "cover",
            "source_position": 0,
            "display_position": 0,
            "is_primary": True,
            "display_eligible": True,
            "rights_status": "needs_review",
            "download_status": "pending",
            "review_status": "pending",
            "checked_at": checked_at,
            "connector_version": CONNECTOR_VERSION,
        })

    seen = {str(item["candidate_url"]) for item in candidates}
    source_position = 0
    for anchor in detail.descendants("a", "tile-item"):
        href = anchor.attrs.get("href")
        if not href:
            continue
        try:
            image_url = _media_url(href, external_id=external_id, expected_kind="samples")
        except ParseError:
            continue
        if image_url in seen:
            continue
        seen.add(image_url)
        source_position += 1
        candidates.append({
            "media_candidate_id": hashlib.sha256(image_url.encode("utf-8")).hexdigest(),
            "source_id": SOURCE_ID,
            "external_id": external_id,
            "entity_type": "work",
            "candidate_url": image_url,
            "source_page_url": canonical_url,
            "source_type": "web_page",
            "purpose": "gallery",
            "source_position": source_position,
            "display_position": source_position if source_position <= 3 else None,
            "is_primary": False,
            "display_eligible": source_position <= 3,
            "rights_status": "needs_review",
            "download_status": "pending",
            "review_status": "pending",
            "checked_at": checked_at,
            "connector_version": CONNECTOR_VERSION,
        })
    return candidates
