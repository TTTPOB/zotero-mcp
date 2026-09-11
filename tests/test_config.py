import json
import os
from unittest.mock import Mock

import pytest

from zotero_mcp import cli, client, config


def test_local_is_unconditional(monkeypatch):
    monkeypatch.setenv("ZOTERO_LOCAL", "false")
    monkeypatch.setenv("ZOTERO_API_KEY", "unused-cloud-key")
    monkeypatch.delenv("ZOTERO_LIBRARY_ID", raising=False)
    monkeypatch.delenv("ZOTERO_LIBRARY_TYPE", raising=False)
    monkeypatch.setattr(client, "load_auth", lambda: config.LocalAuth("server", "local-key"))
    zot = client.new_client()
    try:
        assert zot.local is True
        assert zot.endpoint == "http://localhost:23119/api"
        assert zot.api_key is None
        assert zot.local_api_key == "local-key"
        assert zot.server_id == "server"
        assert zot.library_id == "0"
    finally:
        zot.client.close()


@pytest.mark.parametrize(
    "library_id,library_type", [("../1", "user"), ("１２", "user"), ("0", "group"), ("1", "users")]
)
def test_invalid_settings(library_id, library_type):
    with pytest.raises(ValueError):
        config.Settings(library_id, library_type)


def test_group_settings(monkeypatch):
    monkeypatch.setenv("ZOTERO_LIBRARY_ID", "12345")
    monkeypatch.setenv("ZOTERO_LIBRARY_TYPE", "group")
    assert config.Settings.from_env() == config.Settings("12345", "group")


def test_auth_roundtrip_and_redaction(tmp_path):
    path = tmp_path / "nested" / "auth.json"
    auth = config.LocalAuth("server", "secret-local-key")
    assert "secret-local-key" not in repr(auth)
    assert config.load_auth(path) is None
    config.save_auth(auth, path)
    assert config.load_auth(path) == auth
    assert list(path.parent.iterdir()) == [path]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "content", ["{", "null", "[]", "{}", '{"key":"k","server_id":""}', '{"key":0,"server_id":"s"}']
)
def test_bad_auth_fails_with_actionable_error(tmp_path, content):
    path = tmp_path / "auth.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="authorize"):
        config.load_auth(path)


def test_failed_atomic_save_keeps_existing_key(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    config.save_auth(config.LocalAuth("old-server", "old-key"), path)

    def fail(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(config.os, "replace", fail)
    with pytest.raises(OSError):
        config.save_auth(config.LocalAuth("new-server", "new-key"), path)
    assert config.load_auth(path).key == "old-key"
    assert list(tmp_path.iterdir()) == [path]


def test_authorize_saves_pair_without_printing_key(tmp_path, monkeypatch, capsys):
    import pyzotero

    path = tmp_path / "auth.json"
    zot = Mock(server_id="server")
    zot.authorize_local.return_value = {"key": "do-not-print-this", "remember": True}
    factory = Mock(return_value=zot)
    monkeypatch.setattr(pyzotero, "Zotero", factory)
    monkeypatch.setattr(config, "auth_path", lambda: path)
    assert cli.main(["authorize"]) == 0
    assert json.loads(path.read_text())["server_id"] == "server"
    assert "do-not-print-this" not in capsys.readouterr().out
    assert factory.call_args.kwargs == {"local": True}
    zot.authorize_local.assert_called_once_with("Zotero MCP (local)")
    zot.client.close.assert_called_once()


@pytest.mark.parametrize("remember", [False, None])
def test_authorize_does_not_save_one_time_permission(tmp_path, monkeypatch, remember):
    import pyzotero

    path = tmp_path / "auth.json"
    zot = Mock(server_id="server")
    zot.authorize_local.return_value = {"key": "once", "remember": remember}
    monkeypatch.setattr(pyzotero, "Zotero", lambda *a, **k: zot)
    monkeypatch.setattr(config, "auth_path", lambda: path)
    assert cli.main(["authorize"]) == 1
    assert not path.exists()
    zot.authorize_local.assert_called_once()
    zot.client.close.assert_called_once()


def test_authorization_denial_is_not_retried(tmp_path, monkeypatch):
    import pyzotero

    zot = Mock()
    zot.authorize_local.side_effect = pyzotero.LocalAPIDeniedError("denied")
    monkeypatch.setattr(pyzotero, "Zotero", lambda *a, **k: zot)
    monkeypatch.setattr(config, "auth_path", lambda: tmp_path / "auth.json")
    assert cli.main(["authorize"]) == 1
    zot.authorize_local.assert_called_once()


def test_auth_path_is_not_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    expected_base = tmp_path / "user-config"
    monkeypatch.setenv("LOCALAPPDATA" if os.name == "nt" else "XDG_CONFIG_HOME", str(expected_base))
    assert config.auth_path() == expected_base / "zotero-mcp" / "local-auth.json"


def test_module_cli_help_without_optional_features(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "authorize" in output and "serve" in output
    assert "update-db" not in output


def test_local_client_without_auth(monkeypatch):
    monkeypatch.setattr(client, "load_auth", lambda: None)
    zot = client.new_client(config.Settings())
    try:
        assert zot.local and zot.local_api_key is None
    finally:
        zot.client.close()
