"""Command-line launcher for the standalone iServ MCP server."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

DEFAULT_CREDENTIAL_FILE = Path("~/.config/iserv-mcp/credentials.json").expanduser()


def load_credentials(path: Path) -> None:
    """Load credentials from a private JSON file into the process environment."""
    if not path.is_file():
        raise SystemExit(f"Credential file not found: {path}")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit(f"Credential file must not be accessible by group/others: {path}")
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise SystemExit(f"Could not read credential file {path}: {err}") from err
    required = {
        "url": "ISERV_URL",
        "username": "ISERV_USERNAME",
        "password": "ISERV_PASSWORD",
    }
    for key, environment_name in required.items():
        value = values.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"Credential file is missing a non-empty '{key}' value")
        os.environ[environment_name] = value.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the standalone iServ MCP server")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path(os.environ.get("ISERV_CREDENTIAL_FILE", DEFAULT_CREDENTIAL_FILE)).expanduser(),
        help="mode-0600 JSON credential file",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        help="directory for downloaded parent-letter attachments",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_credentials(args.credentials.expanduser())
    if args.download_dir:
        os.environ["ISERV_DOWNLOAD_DIR"] = str(args.download_dir.expanduser())
    from .server import mcp

    mcp.run(transport="stdio")
