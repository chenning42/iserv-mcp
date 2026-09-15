from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from iserv_mcp import server
from iserv_mcp.client import (
    CannotConnect,
    IServClient,
    _SecureTCPConnector,
    canonical_origin,
    validate_url,
)


class _Response:
    history: list[Any] = []

    def __init__(
        self,
        *,
        status: int,
        url: str,
        headers: dict[str, str] | None = None,
        body: str = "ok",
    ) -> None:
        self.status = status
        self.url = url
        self.headers = headers or {}
        self._body = body
        self.content_length = None

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def text(self, errors: str = "replace") -> str:
        return self._body

    def raise_for_status(self) -> None:
        return None


class _RedirectSession:
    cookie_jar: list[Any] = []

    def __init__(
        self,
        *,
        post_responses: list[_Response] | None = None,
        get_responses: list[_Response] | None = None,
    ) -> None:
        self.post_responses = list(post_responses or [])
        self.get_responses = list(get_responses or [])
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("POST", url, kwargs))
        return self.post_responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("GET", url, kwargs))
        return self.get_responses.pop(0)


@pytest.mark.parametrize(
    "url",
    [
        "http://school.example",
        "https://user:password@school.example",
        "https://school.example/path#fragment",
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://169.254.1.1",
        "https://[::1]",
        "https://192.168.1.10",
    ],
)
def test_validate_url_rejects_unsafe_base_urls(url: str) -> None:
    assert validate_url(url) is False


def test_validate_url_rejects_fragment_even_when_origin_is_valid() -> None:
    assert validate_url("HTTPS://School.Example:443/iserv/#fragment") is False


def test_canonical_origin_normalizes_scheme_host_and_default_port() -> None:
    assert canonical_origin("https://School.Example:443/iserv/") == "https://school.example"


@pytest.mark.parametrize(
    "url",
    [
        "https://school.example:0",
        "https://school.example:65536",
        "https://school..example",
        "https://-school.example",
        "https://school-.example",
        "https://school.example:443:444",
    ],
)
def test_validate_url_rejects_malformed_host_and_port_syntax(url: str) -> None:
    assert validate_url(url) is False


def test_validate_url_preserves_idn_and_explicit_https_port() -> None:
    assert validate_url("https://münchen.iserv.de:443/iserv") is True
    assert canonical_origin("https://münchen.iserv.de:8443") == (
        "https://xn--mnchen-3ya.iserv.de:8443"
    )


@pytest.mark.asyncio
async def test_aiohttp_connector_uses_each_validated_dns_answer_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    loop = asyncio.get_running_loop()
    answers = [
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))],
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.35", 443))],
    ]
    calls: list[tuple[str, int]] = []

    async def getaddrinfo(host: str, port: int, **_kwargs: object) -> list[object]:
        calls.append((host, port))
        return answers.pop(0)

    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)
    connector = _SecureTCPConnector()
    try:
        first = await connector._resolve_host("school.example", 443)
        second = await connector._resolve_host("school.example", 443)
    finally:
        await connector.close()

    assert [item["host"] for item in first] == ["93.184.216.34"]
    assert [item["host"] for item in second] == ["93.184.216.35"]
    assert calls == [("school.example", 443), ("school.example", 443)]


@pytest.mark.asyncio
async def test_aiohttp_connector_rejects_private_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    loop = asyncio.get_running_loop()

    async def getaddrinfo(*_args: object, **_kwargs: object) -> list[object]:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)
    connector = _SecureTCPConnector()
    try:
        with pytest.raises(ValueError, match="private"):
            await connector._resolve_host("school.example", 443)
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_client_does_not_close_or_modify_foreign_aiohttp_session() -> None:
    foreign = aiohttp.ClientSession()
    connector = foreign.connector
    client = IServClient(foreign, "https://school.example", "user", "secret")

    active = await client._ensure_session()
    assert active is not foreign
    assert foreign.connector is connector
    await client.close()

    assert foreign.closed is False
    assert foreign.connector is connector
    await foreign.close()


@pytest.mark.asyncio
async def test_client_closes_lazily_created_session_and_connector() -> None:
    client = IServClient(None, "https://school.example", "user", "secret")

    active = await client._ensure_session()
    assert isinstance(active, aiohttp.ClientSession)
    connector = active.connector
    assert getattr(connector, "_is_iserv_secure", False) is True
    await client.close()

    assert active.closed is True
    assert connector.closed is True


def test_client_rejects_unsafe_base_url_at_construction() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        IServClient(SimpleNamespace(), "http://school.example", "user", "secret")


def test_server_rejects_unsafe_configured_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISERV_URL", "https://192.168.1.10")
    monkeypatch.setenv("ISERV_USERNAME", "user")
    monkeypatch.setenv("ISERV_PASSWORD", "secret")

    with pytest.raises(ValueError, match="ISERV_URL"):
        server._get_credentials()


@pytest.mark.asyncio
async def test_login_cross_origin_redirect_never_sends_credentials() -> None:
    session = _RedirectSession(
        post_responses=[
            _Response(
                status=302,
                url="https://school.example/iserv/auth/login",
                headers={"Location": "https://evil.example/collect"},
            )
        ]
    )
    client = IServClient(session, "https://school.example", "user", "secret")

    with pytest.raises(ValueError, match="origin"):
        await client._authenticate_at(
            "https://school.example/iserv/auth/login",
            {"_username": "user", "_password": "secret"},
        )

    assert len(session.calls) == 1
    assert all("evil.example" not in url for _, url, _ in session.calls)


@pytest.mark.asyncio
async def test_login_same_origin_redirect_is_followed_without_reposting_credentials() -> None:
    session = _RedirectSession(
        post_responses=[
            _Response(
                status=302,
                url="https://school.example/iserv/auth/login",
                headers={"Location": "/iserv/welcome"},
            )
        ],
        get_responses=[
            _Response(
                status=200,
                url="https://school.example/iserv/welcome",
                body="Welcome",
            )
        ],
    )
    client = IServClient(session, "https://school.example", "user", "secret")

    assert await client._authenticate_at(
        "https://school.example/iserv/auth/login",
        {"_username": "user", "_password": "secret"},
    ) is True
    assert [call[0] for call in session.calls] == ["POST", "GET"]
    assert session.calls[0][2]["data"]["_password"] == "secret"
    assert "data" not in session.calls[1][2]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [307, 308])
async def test_cross_origin_307_and_308_never_forward_request_data(
    status: int,
) -> None:
    session = _RedirectSession(
        post_responses=[
            _Response(
                status=status,
                url="https://school.example/iserv/auth/login",
                headers={"Location": "https://evil.example/collect"},
            )
        ]
    )
    client = IServClient(session, "https://school.example", "user", "secret")

    with pytest.raises(ValueError, match="origin"):
        await client._authenticate_at(
            "https://school.example/iserv/auth/login",
            {"_username": "user", "_password": "secret"},
        )

    assert len(session.calls) == 1
    assert session.calls[0][2]["data"]["_password"] == "secret"
    assert "headers" not in session.calls[0][2]
    assert "cookies" not in session.calls[0][2]


@pytest.mark.asyncio
async def test_download_cross_origin_redirect_is_rejected_before_second_request() -> None:
    session = _RedirectSession(
        get_responses=[
            _Response(
                status=302,
                url="https://school.example/iserv/file/a",
                headers={"Location": "https://evil.example/file"},
            )
        ]
    )
    client = IServClient(session, "https://school.example", "user", "secret")

    with pytest.raises(ValueError, match="origin"):
        await client.fetch_authenticated_file("/iserv/file/a")

    assert len(session.calls) == 1
    assert all("evil.example" not in url for _, url, _ in session.calls)


@pytest.mark.asyncio
async def test_download_same_origin_redirect_is_allowed() -> None:
    class _Content:
        async def iter_chunked(self, _size: int):
            yield b"safe"

    first = _Response(
        status=302,
        url="https://school.example/iserv/file/a",
        headers={"Location": "/iserv/file/b"},
    )
    second = _Response(status=200, url="https://school.example/iserv/file/b")
    second.content = _Content()
    second.content_length = 4
    session = _RedirectSession(get_responses=[first, second])
    client = IServClient(session, "https://school.example", "user", "secret")

    result = await client.fetch_authenticated_file("/iserv/file/a")

    assert result.content == b"safe"
    assert [call[1] for call in session.calls] == [
        "https://school.example/iserv/file/a",
        "https://school.example/iserv/file/b",
    ]


@pytest.mark.asyncio
async def test_redirect_limit_is_enforced_before_an_extra_request() -> None:
    responses = [
        _Response(
            status=302,
            url=f"https://school.example/iserv/{index}",
            headers={"Location": f"/{index + 1}"},
        )
        for index in range(4)
    ]
    session = _RedirectSession(get_responses=responses)
    client = IServClient(session, "https://school.example", "user", "secret")

    with pytest.raises(CannotConnect, match="Too many redirects"):
        await client._request(
            "GET",
            "https://school.example/iserv/0",
            timeout=aiohttp.ClientTimeout(total=1),
            max_redirects=2,
        )

    assert len(session.calls) == 3


@pytest.mark.asyncio
async def test_download_size_limit_is_applied_after_same_origin_redirect() -> None:
    class _Content:
        async def iter_chunked(self, _size: int):
            yield b"four"

    first = _Response(
        status=307,
        url="https://school.example/iserv/file/a",
        headers={"Location": "/iserv/file/b"},
    )
    second = _Response(status=200, url="https://school.example/iserv/file/b")
    second.content = _Content()
    second.content_length = 4
    session = _RedirectSession(get_responses=[first, second])
    client = IServClient(session, "https://school.example", "user", "secret")

    with pytest.raises(ValueError, match="3-byte limit"):
        await client.fetch_authenticated_file("/iserv/file/a", max_bytes=3)

    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_authenticated_page_rejects_cross_origin_redirect() -> None:
    session = _RedirectSession(
        get_responses=[
            _Response(
                status=302,
                url="https://school.example/iserv/page",
                headers={"Location": "https://evil.example/page"},
            )
        ]
    )
    client = IServClient(session, "https://school.example", "user", "secret")

    with pytest.raises(ValueError, match="origin"):
        await client._do_fetch_page("https://school.example/iserv/page")
    assert len(session.calls) == 1
