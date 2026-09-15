#!/usr/bin/env python3
"""Standalone MCP server exposing iServ parent letters and timetable data.

The server speaks stdio (JSON-RPC over stdin/stdout) and is compatible with any
MCP 1.x client such as Claude Desktop, Cursor, or the MCP Inspector.

IMPORTANT: All diagnostic output must go to stderr — stdout is the MCP wire.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Route all logging to stderr so it never pollutes the MCP stdio stream.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    stream=sys.stderr,
    format="[iserv-mcp] %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("iserv_mcp")

# ---------------------------------------------------------------------------
# MCP server bootstrap
# ---------------------------------------------------------------------------
from mcp.server.fastmcp import FastMCP  # type: ignore[import-untyped]


@asynccontextmanager
async def _lifespan(_server: FastMCP):
    """Close the shared authenticated HTTP session on MCP shutdown."""
    global _client, _session
    try:
        yield {}
    finally:
        if _session is not None:
            await _session.close()
        _session = None
        _client = None

mcp = FastMCP(
    "iserv",
    instructions=(
        "Access an iServ school portal. "
        "Tools: get_full_schedule (whole timetable), "
        "get_schedule_for_day (single day), "
        "get_parentletters (overview list), "
        "get_parentletter_detail (full text and attachment list), "
        "download_parentletter_attachments (save attachments locally), "
        "mark_parentletter_read (explicit write action), "
        "get_sick_note_children (read available children), "
        "submit_sick_note (preview by default; explicit confirmed write)."
    ),
    lifespan=_lifespan,
)

# ---------------------------------------------------------------------------
# Shared session — one authenticated IServClient for the server's lifetime.
# Created lazily on the first tool call and reused for all subsequent ones.
# ---------------------------------------------------------------------------
_client = None  # IServClient | None
_session = None  # aiohttp.ClientSession | None


def _get_credentials() -> tuple[str, str, str]:
    """Read connection credentials from environment variables.

    Returns:
        Tuple of (url, username, password).

    Raises:
        ValueError: If any required variable is missing or empty.
    """
    url = os.environ.get("ISERV_URL", "").strip()
    username = os.environ.get("ISERV_USERNAME", "").strip()
    password = os.environ.get("ISERV_PASSWORD", "").strip()

    missing = [
        name
        for name, val in (
            ("ISERV_URL", url),
            ("ISERV_USERNAME", username),
            ("ISERV_PASSWORD", password),
        )
        if not val
    ]
    if missing:
        raise ValueError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set ISERV_URL, ISERV_USERNAME, and ISERV_PASSWORD before starting the server."
        )

    from .client import validate_url

    if not validate_url(url):
        raise ValueError(
            "ISERV_URL must be a safe HTTPS URL without userinfo, fragments, "
            "local/private IPs, or local hostnames."
        )

    return url, username, password


async def _get_client():
    """Return the shared IServClient, creating and authenticating it if needed.

    The session and client are module-level singletons so that the cookie jar
    survives across tool calls. IServClient already handles transparent
    re-authentication when a session expires.

    Returns:
        An authenticated IServClient instance.

    Raises:
        ValueError: When credentials are missing.
        AuthenticationError: When login fails.
        CannotConnect: When the server is unreachable.
    """
    global _client, _session

    from .client import IServClient, create_secure_session

    if _client is not None and _client.is_authenticated:
        return _client

    # Close any stale session before opening a new one.
    if _session is not None:
        await _session.close()

    url, username, password = _get_credentials()
    _session = create_secure_session()
    _client = IServClient(_session, url, username, password)
    try:
        await _client.authenticate()
    except BaseException:
        await _session.close()
        _session = None
        _client = None
        raise

    return _client


# ---------------------------------------------------------------------------
# Tool: get_full_schedule
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_full_schedule(week: str = "current") -> str:
    """Return the complete timetable for the requested week as a Markdown table.

    Args:
        week: Which week to fetch. Accepted values:
              "current" — the current ISO calendar week (default),
              "next"    — the following ISO calendar week.

    Returns:
        A Markdown table with columns Day, Time, Subject, Room, Canceled.
        Returns a plain-text message if no lessons were found.
    """
    from .timetable_parser import format_markdown_table, parse_timetable, sort_lessons

    if week not in ("current", "next"):
        return f"Invalid week value '{week}'. Use 'current' or 'next'."

    week_offset = 0 if week == "current" else 1
    target_iso_week = (datetime.now() + timedelta(weeks=week_offset)).isocalendar()[1]

    client = await _get_client()
    raw = await client.fetch_timetable(week=target_iso_week)
    lessons = sort_lessons(parse_timetable(str(raw), locale="en"))

    if not lessons:
        return f"No lessons found for the {week} week (ISO week {target_iso_week})."

    table = format_markdown_table(lessons)
    return f"## Timetable — {week.capitalize()} week (ISO week {target_iso_week})\n\n{table}"


# ---------------------------------------------------------------------------
# Tool: get_schedule_for_day
# ---------------------------------------------------------------------------

_VALID_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


@mcp.tool()
async def get_schedule_for_day(day: str, week: str = "current") -> str:
    """Return the timetable filtered to a single weekday as a Markdown table.

    Args:
        day: Weekday name in English, e.g. "Monday", "Tuesday", …, "Friday".
             Case-insensitive.
        week: Which week to fetch — "current" (default) or "next".

    Returns:
        A Markdown table for the requested day, or a message if no lessons exist.
    """
    from .timetable_parser import format_markdown_table, parse_timetable, sort_lessons

    normalised_day = day.strip().capitalize()
    if normalised_day not in _VALID_DAYS:
        return (
            f"Unknown day '{day}'. "
            f"Please use one of: {', '.join(_VALID_DAYS)}."
        )

    if week not in ("current", "next"):
        return f"Invalid week value '{week}'. Use 'current' or 'next'."

    week_offset = 0 if week == "current" else 1
    target_iso_week = (datetime.now() + timedelta(weeks=week_offset)).isocalendar()[1]

    client = await _get_client()
    raw = await client.fetch_timetable(week=target_iso_week)
    all_lessons = sort_lessons(parse_timetable(str(raw), locale="en"))

    day_lessons = [lesson for lesson in all_lessons if lesson.day == normalised_day]

    if not day_lessons:
        return (
            f"No lessons found for {normalised_day} "
            f"in the {week} week (ISO week {target_iso_week})."
        )

    table = format_markdown_table(day_lessons)
    return (
        f"## Timetable — {normalised_day}, "
        f"{week.capitalize()} week (ISO week {target_iso_week})\n\n{table}"
    )


# ---------------------------------------------------------------------------
# Tool: get_parentletters
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_parentletters() -> str:
    """Return an overview of all Elternbrief (parent letters) from iServ.

    Lists every available parent letter with its index, read status, date,
    sender, child name, and subject. Use get_parentletter_detail to fetch the
    full text of a specific letter.

    Returns:
        A Markdown table of all letters, followed by a summary line.
        Returns a plain-text message if no letters were found.
    """
    from .parentletter_parser import parse_parentletter_list

    client = await _get_client()
    html = await client.fetch_parentletter_list()
    letters = parse_parentletter_list(html)

    if not letters:
        return "No parent letters (Elternbrief) found."

    lines: list[str] = [
        "## Parent Letters (Elternbrief) Overview",
        "",
        "| # | Unread | Date | Sender | Child | Subject | letter_uuid | child_uuid |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for idx, letter in enumerate(letters, start=1):
        date_str = (
            letter.created_at.strftime("%d.%m.%Y %H:%M") if letter.created_at else "—"
        )
        unread_flag = "●" if letter.is_unread else " "
        sender = (letter.sender or "").replace("|", "\\|")
        child = (letter.child or "").replace("|", "\\|")
        subject = (letter.subject or "").replace("|", "\\|")
        lines.append(
            f"| {idx} | {unread_flag} | {date_str} | {sender} | {child} | {subject} "
            f"| `{letter.letter_uuid}` | `{letter.child_uuid}` |"
        )

    unread_count = sum(1 for letter in letters if letter.is_unread)
    lines.append("")
    lines.append(f"**Total:** {len(letters)}  |  **Unread:** {unread_count}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_parentletter_detail
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_parentletter_detail(letter_uuid: str, child_uuid: str) -> str:
    """Return the full text and metadata of a single parent letter (Elternbrief).

    Use get_parentletters first to obtain the letter_uuid and child_uuid values.

    Args:
        letter_uuid: UUID of the letter (visible in the overview table).
        child_uuid:  UUID of the child the letter is addressed to.

    Returns:
        Formatted Markdown with letter metadata (subject, sender, date, child)
        followed by the full letter body as plain text.
    """
    from .parentletter_parser import (
        ParentLetter,
        parse_parentletter_detail,
    )

    letter_uuid = letter_uuid.strip()
    child_uuid = child_uuid.strip()

    if not letter_uuid or not child_uuid:
        return "Both letter_uuid and child_uuid are required."

    stub = ParentLetter(
        letter_uuid=letter_uuid,
        child_uuid=child_uuid,
        subject="",
        sender="",
    )

    client = await _get_client()
    html = await client.fetch_parentletter_detail(letter_uuid, child_uuid)
    enriched = parse_parentletter_detail(html, stub)
    from .attachment_parser import parse_parentletter_attachments
    attachments = parse_parentletter_attachments(html)

    body_plain = _html_to_text(enriched.body_html or html)

    date_str = (
        enriched.created_at.strftime("%d.%m.%Y %H:%M") if enriched.created_at else "—"
    )
    additional = (
        (", ".join(enriched.additional_senders)) if enriched.additional_senders else "—"
    )

    lines: list[str] = [
        f"## {enriched.subject or '(no subject)'}",
        "",
        f"**Sender:** {enriched.sender or '—'}",
        f"**Additional senders:** {additional}",
        f"**Child:** {enriched.child or '—'}",
        f"**Recipient:** {enriched.recipient or '—'}",
        f"**Date:** {date_str}",
        f"**Read:** {'No' if enriched.is_unread else 'Yes'}",
        f"**Attachments:** {len(attachments)}",
        "",
        "---",
        "",
        body_plain.strip() or "(no body text)",
    ]

    if attachments:
        lines.extend(["", "### Attachments"])
        lines.extend(f"- {item.filename}" for item in attachments)

    return "\n".join(lines)


@mcp.tool()
async def download_parentletter_attachments(letter_uuid: str, child_uuid: str) -> str:
    """Download all attachments of one parent letter to the server's local data directory."""
    from .attachment_parser import parse_parentletter_attachments

    letter_uuid = letter_uuid.strip()
    child_uuid = child_uuid.strip()
    if not letter_uuid or not child_uuid:
        return "Both letter_uuid and child_uuid are required."

    client = await _get_client()
    html = await client.fetch_parentletter_detail(letter_uuid, child_uuid)
    attachments = parse_parentletter_attachments(html)
    if not attachments:
        return "No attachments found for this parent letter."

    root = Path(
        os.environ.get(
            "ISERV_DOWNLOAD_DIR", "~/.local/share/iserv-mcp/attachments"
        )
    ).expanduser()
    saved: list[str] = []
    root_fd, root_path = _open_secure_directory(root)
    destination_fd: int | None = None
    try:
        destination_name = _safe_path_component(letter_uuid) or "letter"
        destination_fd, destination = _open_secure_child_directory(
            root_fd, destination_name, root_path
        )
        for number, attachment in enumerate(attachments, start=1):
            downloaded = await client.fetch_authenticated_file(attachment.href)
            filename = _safe_path_component(attachment.filename) or f"attachment-{number}"
            path = _write_attachment(
                destination_fd, filename, downloaded.content, destination
            )
            saved.append(str(path))
    finally:
        try:
            if destination_fd is not None:
                os.close(destination_fd)
        finally:
            os.close(root_fd)

    return "Downloaded attachments:\n" + "\n".join(f"- {path}" for path in saved)


@mcp.tool()
async def mark_parentletter_read(
    letter_uuid: str, child_uuid: str, confirmed: bool = False
) -> str:
    """Mark one parent letter as read; confirmed must explicitly be true."""
    from .parentletter_parser import extract_csrf_token

    letter_uuid = letter_uuid.strip()
    child_uuid = child_uuid.strip()
    if not letter_uuid or not child_uuid:
        return "Both letter_uuid and child_uuid are required."
    if not confirmed:
        return "Not changed. Call again with confirmed=true to mark this letter as read."

    client = await _get_client()
    html = await client.fetch_parentletter_detail(letter_uuid, child_uuid)
    token = extract_csrf_token(html)
    if not token:
        return "Not changed: no mark-as-read CSRF token was found on the detail page."
    await client.mark_parentletter_read(letter_uuid, child_uuid, token)
    return "Parent letter was marked as read."


@mcp.tool()
async def get_sick_note_children() -> str:
    """List children for whom the logged-in guardian may submit a sick note."""
    client = await _get_client()
    children = await client.fetch_sick_note_children()
    if not children:
        return "No children are available for sick notes."
    lines = ["## Children available for sick notes", "", "| ID | Child | Class |", "| --- | --- | --- |"]
    for child in children:
        course = child.get("mainCourse")
        class_name = course.get("name", "—") if isinstance(course, dict) else "—"
        lines.append(f"| `{child.get('id', '')}` | {child.get('displayname', '—')} | {class_name} |")
    return "\n".join(lines)


@mcp.tool()
async def submit_sick_note(
    child_id: int,
    sick_from: str,
    sick_until: str,
    from_lesson: int = 1,
    until_lesson: int = 7,
    duty_to_report: bool = False,
    comment: str = "",
    confirmed: bool = False,
) -> str:
    """Preview or submit a sick note for one child; confirmed must explicitly be true.

    Dates must use YYYY-MM-DD. Use get_sick_note_children first. The selected
    child is verified against the guardian's live child list before submission.
    """
    try:
        start = date.fromisoformat(sick_from)
        end = date.fromisoformat(sick_until)
    except ValueError:
        return "Not submitted: sick_from and sick_until must use YYYY-MM-DD."
    if end < start:
        return "Not submitted: sick_until must not be before sick_from."
    if not 1 <= from_lesson <= until_lesson <= 20:
        return "Not submitted: lesson numbers must satisfy 1 <= from_lesson <= until_lesson <= 20."

    client = await _get_client()
    children = await client.fetch_sick_note_children()
    child = next((item for item in children if item.get("id") == child_id), None)
    if child is None:
        return "Not submitted: child_id is not available to this guardian account."

    name = str(child.get("displayname", child_id))
    preview = (
        f"Sick note for {name}: {start.isoformat()} lesson {from_lesson} through "
        f"{end.isoformat()} lesson {until_lesson}; duty_to_report={duty_to_report}; "
        f"comment={'yes' if comment.strip() else 'no'}."
    )
    if not confirmed:
        return f"PREVIEW ONLY — nothing submitted. {preview} Call again with confirmed=true to submit."

    payload: dict[str, object] = {
        "sickUser": child_id,
        "sickFromDate": start.isoformat(),
        "sickTillDate": end.isoformat(),
        "isDutyToReport": duty_to_report,
        "sickFromLessonNumber": from_lesson,
        "sickTillLessonNumber": until_lesson,
    }
    if comment.strip():
        payload["note"] = comment.strip()
    await client.submit_sick_note(payload)
    return f"Submitted. {preview}"


def _safe_path_component(value: str) -> str:
    """Make an untrusted display name safe as one local path component."""
    return re.sub(r"[^\w.() -]+", "_", value, flags=re.UNICODE).strip(" .")[:180]


def _directory_flags() -> int:
    """Return flags for opening a directory without following symlinks."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise OSError("Atomic no-follow directory opens are not supported")
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow


def _open_directory_at(parent_fd: int, name: str) -> int:
    """Open an existing child directory with a symlink-safe dirfd."""
    try:
        fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as err:
        if err.errno in (
            getattr(os, "ELOOP", 40),
            getattr(os, "ENOTDIR", 20),
        ):
            raise ValueError(
                "Attachment directory must not be a symlink or non-directory"
            ) from err
        raise
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError("Attachment destination must be a directory")
        return fd
    except Exception:
        os.close(fd)
        raise


def _make_private_directory(fd: int) -> None:
    """Ensure an attachment directory is private to its owner."""
    os.fchmod(fd, 0o700)


def _open_secure_directory(path: Path) -> tuple[int, Path]:
    """Create/open every path component without traversing symlinks."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    fd = os.open(absolute.anchor or os.sep, _directory_flags())
    try:
        for component in absolute.parts:
            if component in (absolute.anchor, ""):
                continue
            try:
                child_fd = _open_directory_at(fd, component)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=fd)
                child_fd = _open_directory_at(fd, component)
            previous_fd = fd
            try:
                os.close(previous_fd)
            except BaseException:
                try:
                    os.close(child_fd)
                finally:
                    raise
            fd = child_fd
        _make_private_directory(fd)
        result = (fd, absolute)
        fd = -1
        return result
    except BaseException:
        if fd >= 0:
            os.close(fd)
        raise


def _open_secure_child_directory(
    parent_fd: int, name: str, parent_path: Path
) -> tuple[int, Path]:
    """Create/open one attachment letter directory using the parent dirfd."""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Attachment directory name is unsafe")
    fd = -1
    try:
        fd = _open_directory_at(parent_fd, name)
    except FileNotFoundError:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        fd = _open_directory_at(parent_fd, name)
    try:
        _make_private_directory(fd)
        result = (fd, parent_path / name)
        fd = -1
        return result
    finally:
        if fd >= 0:
            os.close(fd)


def _write_attachment(
    destination_fd: int, filename: str, content: bytes, destination: Path
) -> Path:
    """Create one attachment atomically, never following or replacing a file."""
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise ValueError("Attachment filename is unsafe")

    source = Path(filename)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise OSError("Atomic no-follow file opens are not supported")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
    for index in range(1, 10_000):
        candidate = filename if index == 1 else f"{source.stem}-{index}{source.suffix}"
        fd: int | None = None
        output = None
        try:
            fd = os.open(candidate, flags, 0o600, dir_fd=destination_fd)
        except FileExistsError:
            continue
        except OSError as err:
            if err.errno == getattr(os, "ELOOP", 40):
                raise ValueError("Attachment filename must not be a symlink") from err
            raise

        try:
            os.fchmod(fd, 0o600)
            output = os.fdopen(fd, "wb")
            fd = None
            with output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            return destination / candidate
        except Exception:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if output is not None:
                try:
                    output.close()
                except OSError:
                    pass
            try:
                os.unlink(candidate, dir_fd=destination_fd)
            except FileNotFoundError:
                pass
            raise
    raise RuntimeError("Could not allocate a unique attachment filename")


# ---------------------------------------------------------------------------
# HTML → plain-text helper
# ---------------------------------------------------------------------------


def _html_to_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace into readable plain text.

    Args:
        html: Raw HTML string.

    Returns:
        Clean plain-text representation of the HTML content.
    """
    import re
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._parts: list[str] = []
            self._skip = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in ("script", "style"):
                self._skip = True
            elif tag in ("p", "br", "li", "div", "tr", "h1", "h2", "h3", "h4"):
                self._parts.append("\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style"):
                self._skip = False

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self._parts.append(data)

        def get_text(self) -> str:
            raw = "".join(self._parts)
            raw = re.sub(r"[ \t]+", " ", raw)
            raw = re.sub(r"\n{3,}", "\n\n", raw)
            return raw.strip()

    stripper = _Stripper()
    stripper.feed(html)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()  # defaults to stdio transport
