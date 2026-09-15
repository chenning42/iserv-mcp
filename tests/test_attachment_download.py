from types import SimpleNamespace
from typing import Any, cast

import pytest

from iserv_mcp.client import IServClient


class _Content:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self._chunks:
            yield chunk


class _Response:
    status = 200
    history = []
    url = "https://school.example/iserv/file/test"
    headers = {"Content-Type": "application/pdf"}
    content_length = 7

    def __init__(self):
        self.content = _Content([b"abc", b"defg"])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None


class _Session:
    cookie_jar = []

    def get(self, *_args, **_kwargs):
        return _Response()


@pytest.mark.asyncio
async def test_authenticated_file_download():
    client = IServClient(cast(Any, _Session()), "https://school.example/iserv/", "user", "secret")
    result = await client.fetch_authenticated_file("/iserv/file/test")
    assert result.content == b"abcdefg"
    assert result.content_type == "application/pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize("href", ["https://evil.example/iserv/file/x", "/outside/file/x"])
async def test_authenticated_file_rejects_unsafe_url(href):
    client = IServClient(cast(Any, SimpleNamespace()), "https://school.example/iserv/", "user", "secret")
    with pytest.raises(ValueError):
        await client.fetch_authenticated_file(href)
