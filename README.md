# iServ MCP

Unofficial, standalone [Model Context Protocol](https://modelcontextprotocol.io/)
server for iServ school portals. It retrieves parent letters, their full text
and attachments, and can explicitly mark letters as read. Timetable tools from
the HAiServ client are included as well.

This project is not affiliated with or endorsed by IServ GmbH. iServ instances
can differ; test against your school installation before relying on it.

## MCP tools

| Tool | Purpose | Writes to iServ? |
| --- | --- | --- |
| `get_parentletters` | List parent letters and unread state | No |
| `get_parentletter_detail` | Read full text, metadata and attachment names | No |
| `download_parentletter_attachments` | Save all attachments locally | No |
| `mark_parentletter_read` | Mark a letter as read; requires `confirmed=true` | **Yes** |
| `get_full_schedule` | Read the current or next timetable week | No |
| `get_schedule_for_day` | Read one timetable day | No |

A single attachment is limited to 25 MiB. Downloads are accepted only from the
configured HTTPS origin and below `/iserv/`. Directories use mode `0700`; files
use `0600`.

## Install

Python 3.11 or newer is required.

```bash
git clone <repository-url> iserv-mcp
cd iserv-mcp
python -m venv .venv
.venv/bin/pip install .
```

For development and tests:

```bash
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

## Credentials

Copy the included example to your private configuration directory:

```bash
mkdir -p ~/.config/iserv-mcp
cp credentials.example.json ~/.config/iserv-mcp/credentials.json
chmod 600 ~/.config/iserv-mcp/credentials.json
```

Then edit `~/.config/iserv-mcp/credentials.json`:

```json
{
  "url": "https://school.example/iserv/",
  "username": "your-username",
  "password": "your-password"
}
```

The launcher rejects credential files readable by group or others. Passwords,
session cookies and response bodies are not intentionally logged. Never commit
the credential file.

## Run

After installation:

```bash
iserv-mcp
```

Or directly from a checkout:

```bash
.venv/bin/python -m iserv_mcp
```

Optional flags:

```text
--credentials PATH   use another mode-0600 credential file
--download-dir PATH  choose where attachments are stored
```

The default download directory is
`~/.local/share/iserv-mcp/attachments`.

## Hermes configuration

Hermes can launch the installed stdio server directly:

```yaml
mcp_servers:
  iserv:
    command: "/absolute/path/to/iserv-mcp/.venv/bin/iserv-mcp"
    args: []
    timeout: 120
    connect_timeout: 30
    sampling:
      enabled: false
```

Restart Hermes after changing `config.yaml`. Tools are registered with names
such as `mcp_iserv_get_parentletters`.

For Hermi and James, install the same package on each host and create a separate
local credential file there. This avoids exposing the MCP server or credentials
over the LAN. A shared HTTP deployment would need its own authentication and is
therefore deliberately not the default.

## Design notes

- Authentication uses a normal iServ web session and automatically renews an
  expired session for supported requests.
- Reading a letter does not automatically mark it as read.
- `mark_parentletter_read` is separated from read tools and requires an explicit
  confirmation argument.
- Attachments remain on the MCP host and are never uploaded automatically.
- The absence/illness module is not implemented yet because its endpoints and
  form fields must be verified against the concrete school installation first.

## Attribution and license

Parts of the iServ client and parsers are derived from
[HAiServ](https://github.com/jjoswig/haiserv) by jjoswig. See `NOTICE`.
The project is licensed under the MIT License; see `LICENSE`.
