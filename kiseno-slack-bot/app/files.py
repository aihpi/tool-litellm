"""Files people attach in Slack: images and text files go to the agent, everything else gets a note.

Images travel as A2A file parts and reach the model as images. Text files (plain text, CSV, JSON, Markdown and
the like) become part of the prompt. Every attached file also gets a note on its message's line, so the model
knows that a file exists even when it cannot read it, and can say so instead of guessing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
TEXT_APPLICATION_TYPES = frozenset(
    {
        "application/json",
        "application/x-ndjson",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/csv",
        "application/javascript",
        "application/x-sh",
        "application/sql",
    }
)
# Larger or unusual images (HEIC, TIFF) fall back to the biggest Slack thumbnail, at most 1024 px on the long side.
THUMBNAIL_KEYS = ("thumb_1024", "thumb_960", "thumb_800", "thumb_720")
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5_000_000
MAX_IMAGE_TOTAL_BYTES = 10_000_000
MAX_TEXT_BYTES = 200_000
MAX_TEXT_CHARS = 30_000
MAX_TEXT_TOTAL_CHARS = 60_000


class FileFetchError(Exception):
    pass


@dataclass(frozen=True)
class Download:
    data: bytes
    content_type: str
    complete: bool
    """False when the file was longer than ``max_bytes`` and only its start was read."""


class Fetcher(Protocol):
    async def fetch(self, url: str, *, max_bytes: int) -> Download: ...


class SlackFileFetcher:
    """Downloads ``url_private`` files with the bot token; Slack requires the ``files:read`` scope for this."""

    def __init__(self, token: str, http: httpx.AsyncClient | None = None, timeout: float = 30.0):
        self._token = token
        self._http = http or httpx.AsyncClient(timeout=timeout)

    async def fetch(self, url: str, *, max_bytes: int) -> Download:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        # The bot token goes to Slack only. httpx drops it on redirects to another host.
        if parts.scheme != "https" or not (host == "slack.com" or host.endswith(".slack.com")):
            raise FileFetchError("not a Slack file URL")
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with self._http.stream("GET", url, headers=headers, follow_redirects=True) as response:
                if response.status_code != 200:
                    raise FileFetchError(f"HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        return Download(bytes(data[:max_bytes]), content_type, complete=False)
        except httpx.HTTPError as exc:
            raise FileFetchError(type(exc).__name__) from exc
        return Download(bytes(data), content_type, complete=True)


@dataclass(frozen=True)
class Image:
    name: str
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class TextFile:
    name: str
    author: str
    text: str
    truncated: bool


@dataclass
class Attachments:
    images: list[Image] = field(default_factory=list)
    """Newest first."""
    texts: list[TextFile] = field(default_factory=list)
    """Newest first."""
    notes: dict[str, str] = field(default_factory=dict)
    """File id -> what the model reads about that file on its message's line."""

    def notes_for(self, message: Mapping[str, Any]) -> list[str]:
        return [self.notes[f["id"]] for f in message_files(message) if f["id"] in self.notes]

    def text_block(self) -> str:
        """The text files, oldest first, to go before the conversation; empty without text files."""
        blocks = []
        for f in reversed(self.texts):
            cut = "\n[The file goes on; only its start is shown.]" if f.truncated else ""
            blocks.append(f"[File {f.name}, attached by {f.author}]\n{f.text}{cut}\n[End of {f.name}]")
        return "\n\n".join(blocks)


def message_files(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    files = message.get("files")
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, Mapping) and f.get("id")]


def is_text_type(mime_type: str) -> bool:
    return mime_type.startswith("text/") or mime_type in TEXT_APPLICATION_TYPES


def decode_text(data: bytes) -> str:
    """UTF-8 (a character cut off at the end is dropped), else Windows-1252, which Excel uses for CSV."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # Only a character cut off at the very end; anything else means the text is not UTF-8.
        if exc.reason == "unexpected end of data":
            try:
                return data[: exc.start].decode("utf-8-sig")
            except UnicodeDecodeError:
                pass
    return data.decode("cp1252", errors="replace")


def _thumbnail(f: Mapping[str, Any]) -> str | None:
    for key in THUMBNAIL_KEYS:
        if isinstance(f.get(key), str):
            return f[key]
    return None


def _original_url(f: Mapping[str, Any]) -> str | None:
    url = f.get("url_private_download") or f.get("url_private")
    return url if isinstance(url, str) else None


def _is_sign_in_page(file_type: str, download: Download) -> bool:
    # Without files:read, Slack answers a file URL with its HTML sign-in page and status 200.
    if download.content_type == "text/html" and file_type != "text/html":
        logger.warning("Slack sent a web page instead of a file; does the app have the files:read scope?")
        return True
    return False


async def collect(messages: Iterable[tuple[Mapping[str, Any], str]], fetcher: Fetcher | None) -> Attachments:
    """Read the files of ``(message, author name)`` pairs, given newest message first.

    Newer files win when a limit is reached, because the newest request usually refers to them.
    """
    result = Attachments()
    image_budget = MAX_IMAGE_TOTAL_BYTES
    text_budget = MAX_TEXT_TOTAL_CHARS
    for message, author in messages:
        for f in message_files(message):
            name = str(f.get("name") or f.get("title") or "file")
            mime_type = str(f.get("mimetype") or "").lower()
            if f.get("mode") in ("tombstone", "hidden_by_limit") or f.get("file_access") == "check_file_info":
                note = f"[file {name}: not available to you]"
            elif mime_type.startswith("image/"):
                if len(result.images) >= MAX_IMAGES:
                    note = f"[image {name}: left out, only the newest {MAX_IMAGES} images are attached]"
                else:
                    image = await _read_image(f, name, mime_type, fetcher, image_budget)
                    if isinstance(image, Image):
                        result.images.append(image)
                        image_budget -= len(image.data)
                        note = f"[image {name}: attached after the conversation]"
                    else:
                        note = f"[image {name}: {image}]"
            elif is_text_type(mime_type):
                if text_budget <= 0:
                    note = f"[file {name}: left out, the other files fill the space]"
                else:
                    text = await _read_text(f, mime_type, fetcher, min(MAX_TEXT_CHARS, text_budget), name, author)
                    if text is None:
                        note = f"[file {name}: could not be downloaded]"
                    else:
                        result.texts.append(text)
                        text_budget -= len(text.text)
                        note = f"[file {name}: its text is shown before the conversation]"
            else:
                kind = str(f.get("pretty_type") or f.get("filetype") or "this type of")
                note = f"[file {name}: {kind} files cannot be read here]"
            result.notes[f["id"]] = note
    return result


async def _read_image(
    f: Mapping[str, Any], name: str, mime_type: str, fetcher: Fetcher | None, budget: int
) -> Image | str:
    """The image, or why it is missing."""
    limit = min(MAX_IMAGE_BYTES, budget)
    size = f.get("size") if isinstance(f.get("size"), int) else None
    original = _original_url(f)
    use_original = bool(original) and mime_type in IMAGE_TYPES and (size is None or size <= limit)
    url = original if use_original else _thumbnail(f)
    if fetcher is None:
        return "could not be downloaded"
    if not url or limit <= 0:
        return "too large to read"
    try:
        download = await fetcher.fetch(url, max_bytes=limit)
    except FileFetchError as exc:
        logger.warning("Could not download image %s: %s", f.get("id"), exc)
        return "could not be downloaded"
    if _is_sign_in_page(mime_type, download):
        return "could not be downloaded"
    if not download.complete:
        return "too large to read"
    actual = download.content_type if download.content_type in IMAGE_TYPES else (mime_type if use_original else "")
    if actual not in IMAGE_TYPES:
        return "this image format cannot be read"
    return Image(name=name, mime_type=actual, data=download.data)


async def _read_text(
    f: Mapping[str, Any], mime_type: str, fetcher: Fetcher | None, limit: int, name: str, author: str
) -> TextFile | None:
    url = _original_url(f)
    if fetcher is None or not url:
        return None
    try:
        download = await fetcher.fetch(url, max_bytes=MAX_TEXT_BYTES)
    except FileFetchError as exc:
        logger.warning("Could not download file %s: %s", f.get("id"), exc)
        return None
    if _is_sign_in_page(mime_type, download):
        return None
    text = decode_text(download.data)
    truncated = not download.complete or len(text) > limit
    return TextFile(name=name, author=author, text=text[:limit], truncated=truncated)
