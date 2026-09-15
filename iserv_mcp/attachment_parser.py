"""Extract attachment links from an iServ Elternbrief detail page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import PurePosixPath


@dataclass(frozen=True)
class ParentLetterAttachment:
    """One authenticated attachment linked from a parent letter."""

    href: str
    filename: str


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, bool]] = []
        self._active: tuple[str, bool] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = dict(attrs)
        href = values.get("href")
        if href:
            self._active = (unescape(href), "download" in values)
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._active:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active:
            href, has_download = self._active
            self.links.append((href, " ".join(self._text).strip(), has_download))
            self._active = None
            self._text = []


def parse_parentletter_attachments(html: str) -> list[ParentLetterAttachment]:
    """Return likely attachment links, preserving their document order."""
    collector = _LinkCollector()
    collector.feed(html or "")
    result: list[ParentLetterAttachment] = []
    seen: set[str] = set()
    for href, label, has_download in collector.links:
        path = href.split("?", 1)[0].casefold()
        looks_like_file = has_download or bool(
            re.search(r"/(?:attachment|attachments|download|file|files)(?:/|$)", path)
        )
        if not looks_like_file or href in seen:
            continue
        seen.add(href)
        url_name = PurePosixPath(path).name
        filename = _safe_filename(label) or _safe_filename(url_name) or "attachment"
        result.append(ParentLetterAttachment(href=href, filename=filename))
    return result


def _safe_filename(value: str) -> str:
    value = unescape(re.sub(r"\s+", " ", value)).strip().replace("/", "_").replace("\\", "_")
    value = re.sub(r"[\x00-\x1f\x7f]", "", value)
    return value[:180].strip(" .")
