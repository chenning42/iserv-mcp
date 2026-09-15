import json
from typing import Any, cast

import pytest

from iserv_mcp.client import IServClient
from iserv_mcp import server


class _Response:
    status = 200
    history = []
    url = "https://school.example/iserv/dieschulapp/api/1.0/sickNotes/"
    headers = {"Content-Type": "application/json"}

    def __init__(self, body):
        self._body = json.dumps(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def text(self, errors="replace"):
        return self._body

    def raise_for_status(self):
        return None


class _Session:
    cookie_jar = []

    def __init__(self):
        self.calls = []

    def request(self, method, url, json=None, **_kwargs):
        self.calls.append((method, url, json))
        body = [{"id": 42, "displayname": "Test Child", "mainCourse": {"name": "1a"}}] if method == "GET" else {"id": 99}
        return _Response(body)


@pytest.mark.asyncio
async def test_sick_note_client_uses_verified_endpoints():
    session = _Session()
    client = IServClient(cast(Any, session), "https://school.example/iserv/", "user", "secret")
    children = await client.fetch_sick_note_children()
    assert children[0]["id"] == 42
    payload = {"sickUser": 42, "sickFromDate": "2026-09-16"}
    result = await client.submit_sick_note(payload)
    assert result["id"] == 99
    assert session.calls[1][0] == "POST"
    assert session.calls[1][2] == payload


class _FakeClient:
    def __init__(self):
        self.submitted = []

    async def fetch_sick_note_children(self):
        return [{"id": 42, "displayname": "Test Child", "mainCourse": {"name": "1a"}}]

    async def submit_sick_note(self, payload):
        self.submitted.append(payload)
        return {"id": 99}


@pytest.mark.asyncio
async def test_sick_note_preview_does_not_write(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(server, "_get_client", lambda: _async_value(client))
    result = await server.submit_sick_note(42, "2026-09-16", "2026-09-16")
    assert result.startswith("PREVIEW ONLY")
    assert client.submitted == []


@pytest.mark.asyncio
async def test_sick_note_confirmed_writes(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(server, "_get_client", lambda: _async_value(client))
    result = await server.submit_sick_note(42, "2026-09-16", "2026-09-17", confirmed=True)
    assert result.startswith("Submitted.")
    assert client.submitted[0]["sickUser"] == 42


async def _async_value(value):
    return value