import json
import os

import pytest

from iserv_mcp.cli import load_credentials


def test_load_credentials_requires_private_permissions(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(SystemExit, match="group/others"):
        load_credentials(path)


def test_load_credentials_sets_environment(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "url": "https://school.example/iserv/",
                "username": "test-user",
                "password": "test-password",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    for name in ("ISERV_URL", "ISERV_USERNAME", "ISERV_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    load_credentials(path)
    assert os.environ["ISERV_URL"] == "https://school.example/iserv/"
    assert os.environ["ISERV_USERNAME"] == "test-user"
    assert os.environ["ISERV_PASSWORD"] == "test-password"
