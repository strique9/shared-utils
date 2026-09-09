"""Tests for shared_utils.binnacle_auth. No network, no real tokens."""

import asyncio
import os
import stat
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared_utils.binnacle_auth import (
    APPS,
    CredentialError,
    OnePasswordClient,
    TokenStore,
    main,
)


def run(coro):
    return asyncio.run(coro)


# -- TokenStore --------------------------------------------------------------

def test_write_creates_private_file_and_directory(tmp_path):
    store = TokenStore(tmp_path / "tokens")
    path = store.write("purser", "ops_abc123\n")
    assert path.read_text() == "ops_abc123\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert store.read("purser") == "ops_abc123"
    assert store.available() == ["purser"]


def test_write_rejects_non_service_account_token(tmp_path):
    store = TokenStore(tmp_path)
    with pytest.raises(CredentialError):
        store.write("purser", "hunter2")
    assert not (tmp_path / "purser").exists()


def test_read_missing_token_names_the_fix(tmp_path):
    store = TokenStore(tmp_path)
    with pytest.raises(CredentialError) as exc:
        store.read("bearing")
    assert "binnacle-auth store --app bearing" in str(exc.value)


def test_read_refuses_group_or_world_readable_file(tmp_path):
    store = TokenStore(tmp_path)
    path = store.write("meridian", "ops_x")
    os.chmod(path, 0o644)
    with pytest.raises(CredentialError) as exc:
        store.read("meridian")
    assert "0600" in str(exc.value)


def test_read_refuses_open_directory(tmp_path):
    store = TokenStore(tmp_path / "t")
    store.write("meridian", "ops_x")
    os.chmod(store.directory, 0o755)
    with pytest.raises(CredentialError) as exc:
        store.read("meridian")
    assert "0700" in str(exc.value)


def test_read_refuses_empty_token(tmp_path):
    store = TokenStore(tmp_path)
    path = store.write("binnacle", "ops_x")
    path.write_text("\n")
    with pytest.raises(CredentialError):
        store.read("binnacle")


def test_path_rejects_traversal(tmp_path):
    store = TokenStore(tmp_path)
    with pytest.raises(CredentialError):
        store.path("../etc")


# -- OnePasswordClient with a mocked SDK --------------------------------------

def _field(title, value):
    f = MagicMock()
    f.title = title
    f.value = value
    return f


def _overview(title, item_id):
    o = MagicMock()
    o.title = title
    o.id = item_id
    return o


def _client_with(items: dict[str, dict[str, str]]):
    """items: title -> {field title: value}"""
    op = AsyncMock()
    overviews = [_overview(title, f"id-{i}") for i, title in enumerate(items)]
    by_id = {
        f"id-{i}": MagicMock(fields=[_field(k, v) for k, v in fields.items()])
        for i, fields in enumerate(items.values())
    }
    op.vaults.list = AsyncMock(return_value=[_overview("The Binnacle", "vault-1")])
    op.items.list = AsyncMock(return_value=overviews)
    op.items.get = AsyncMock(side_effect=lambda _vid, item_id: by_id[item_id])
    client = OnePasswordClient("purser")
    client._op_client = op
    return client


def test_login_credential_username():
    client = _client_with({"Meevo": {"username": "u", "password": "p"}})
    assert run(client.get_login_credential("Meevo")) == {"username": "u", "password": "p"}


def test_login_credential_accepts_email_as_username():
    client = _client_with({"Soci": {"email": "e@x", "password": "p"}})
    assert run(client.get_login_credential("Soci"))["username"] == "e@x"


def test_login_credential_missing_password():
    client = _client_with({"Bad": {"username": "u"}})
    with pytest.raises(CredentialError) as exc:
        run(client.get_login_credential("Bad"))
    assert "missing required fields" in str(exc.value)


def test_item_not_found_is_clear():
    client = _client_with({"Other": {"username": "u", "password": "p"}})
    with pytest.raises(CredentialError) as exc:
        run(client.get_login_credential("Nope"))
    assert "not found" in str(exc.value)


def test_api_credential_mapping():
    client = _client_with({"Paylocity API — Prod NextGen": {
        "username": "cid", "credential": "sec", "hostname": "https://t"}})
    assert run(client.get_api_credential("Paylocity API — Prod NextGen")) == {
        "client_id": "cid", "client_secret": "sec", "token_url": "https://t"}


def test_get_field_is_case_insensitive_and_strict():
    client = _client_with({"ME SFTP": {"Zip password - daily csv extracts": "z"}})
    assert run(client.get_field("ME SFTP", "zip password - daily csv extracts")) == "z"
    with pytest.raises(CredentialError):
        run(client.get_field("ME SFTP", "private key"))


def test_vault_not_visible():
    op = AsyncMock()
    op.vaults.list = AsyncMock(return_value=[_overview("Other", "v")])
    client = OnePasswordClient("bearing")
    client._op_client = op
    with pytest.raises(CredentialError) as exc:
        run(client.list_item_titles())
    assert "not visible" in str(exc.value)


def test_check_reports_missing_expected_items():
    client = _client_with({"A": {"username": "u", "password": "p"}})
    report = run(client.check(expected_items=["A", "B"]))
    assert report["ok"] is False
    assert report["missing_items"] == ["B"]
    assert report["item_count"] == 1


# -- CLI ----------------------------------------------------------------------

def test_cli_apps_lists_known_apps(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("shared_utils.binnacle_auth.TokenStore.__init__",
                        lambda self, directory=tmp_path: setattr(self, "directory", tmp_path))
    assert main(["apps"]) == 0
    out = capsys.readouterr().out
    for app in APPS:
        assert app in out
    assert "NO TOKEN" in out


def test_cli_doctor_fails_without_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("shared_utils.binnacle_auth.TokenStore.__init__",
                        lambda self, directory=tmp_path: setattr(self, "directory", tmp_path))
    assert main(["doctor", "--app", "purser"]) == 1
    assert "binnacle-auth store --app purser" in capsys.readouterr().out


def test_cli_store_refuses_bad_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("shared_utils.binnacle_auth.TokenStore.__init__",
                        lambda self, directory=tmp_path: setattr(self, "directory", tmp_path))
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("not-a-token\n"))
    assert main(["store", "--app", "purser"]) == 1
    assert not (tmp_path / "purser").exists()


def test_cli_bootstrap_provisions_siblings(tmp_path, monkeypatch, capsys):
    """bootstrap reads 'Service Account Auth Token: <App>' items via one stored token."""
    from shared_utils import binnacle_auth as ba

    monkeypatch.setattr("shared_utils.binnacle_auth.TokenStore.__init__",
                        lambda self, directory=tmp_path: setattr(self, "directory", tmp_path))
    TokenStore().write("purser", "ops_purser")

    vault = {"Service Account Auth Token: Bearing": {"credential": "ops_bearing"},
             "Service Account Auth Token: Meridian": {"credential": "ops_meridian"},
             "Service Account Auth Token: Binnacle": {"credential": "ops_binnacle"},
             "Service Account Auth Token: Tally": {"credential": "ops_tally"}}

    async def fake_get_field(self, title, field_title):
        return vault[title][field_title]

    async def fake_check(self, expected_items=None):
        return {"ok": True, "item_count": 4, "app": self.app, "vault": self.vault,
                "token_path": str(self.token_store.path(self.app))}

    monkeypatch.setattr(ba.OnePasswordClient, "get_field", fake_get_field)
    monkeypatch.setattr(ba.OnePasswordClient, "check", fake_check)
    assert main(["bootstrap", "--via", "purser"]) == 0
    assert sorted(TokenStore().available()) == ["bearing", "binnacle", "meridian", "purser", "tally"]
    assert (tmp_path / "bearing").read_text().strip() == "ops_bearing"
    assert "[OK]" in capsys.readouterr().out
