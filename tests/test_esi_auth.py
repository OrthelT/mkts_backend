"""Hermetic auth tests: no EVE calls, browser, real credentials or replicas."""
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dotenv import dotenv_values

from mkts_backend.esi import esi_auth as auth
from mkts_backend.cli_tools import esi_auth_cli as cli


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLIENT_ID", "test-client")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.delenv("REFRESH_TOKEN", raising=False)
    monkeypatch.setattr(cli, "_get_env_file_path", lambda: tmp_path / ".env")
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(auth.webbrowser, "open", Mock(return_value=False))
    # Fail closed if a test accidentally reaches the network.
    monkeypatch.setattr(auth, "OAuth2Session", Mock(side_effect=AssertionError("Unexpected OAuth session")))


def test_cached_token_needs_no_environment_refresh_token():
    token = {"access_token": "cached", "expires_at": time.time() + 300}
    auth.save_token(token)
    assert auth.get_token("scope") == token


@pytest.mark.parametrize("cached", [False, True])
def test_refresh_bootstraps_from_cache_or_environment(monkeypatch, cached):
    if cached:
        auth.save_token({"refresh_token": "old", "expires_at": 0})
    else:
        monkeypatch.setenv("REFRESH_TOKEN", "old")
    session = Mock()
    session.refresh_token.return_value = {"access_token": "new", "refresh_token": "rotated"}
    monkeypatch.setattr(auth, "get_oauth_session", lambda *a: session)
    assert auth.get_token("scope")["refresh_token"] == "rotated"
    assert session.refresh_token.call_args.kwargs["refresh_token"] == "old"
    assert auth.load_cached_token()["refresh_token"] == "rotated"
    assert auth.token_path().stat().st_mode & 0o777 == 0o600


def test_missing_token_gives_setup_command():
    with pytest.raises(ValueError, match="esi-auth --market-data"):
        auth.get_token("scope")


def test_corrupt_cache_can_be_recovered(monkeypatch):
    auth.token_path().write_text("{broken")
    test_refresh_bootstraps_from_cache_or_environment(monkeypatch, False)


@pytest.mark.parametrize("suffix", ["?code=c&state=wrong", "?code=c", "?error=access_denied&state=s", "?state=s", "?code=c&code=d&state=s"])
def test_invalid_callback_rejected(suffix):
    with pytest.raises(ValueError):
        auth._callback_code("http://localhost:8000/callback" + suffix,
                            "http://localhost:8000/callback", "s")


def test_wrong_callback_destination_rejected():
    with pytest.raises(ValueError):
        auth._callback_code("http://evil.test/callback?code=c&state=s",
                            "http://localhost:8000/callback", "s")


def test_authorize_without_refresh_token(monkeypatch):
    session = Mock()
    session.authorization_url.return_value = ("https://example.test/login", "state")
    session.fetch_token.return_value = {"access_token": "new", "refresh_token": "refresh"}
    monkeypatch.setattr(auth, "get_oauth_session", lambda *a: session)
    monkeypatch.setattr(auth, "_capture_callback", lambda *a: "http://localhost:8000/callback?code=code&state=state")
    assert auth.authorize_character()["refresh_token"] == "refresh"
    assert session.fetch_token.call_args.kwargs["code"] == "code"
    assert "authorization_response" not in session.fetch_token.call_args.kwargs
    assert auth.load_cached_token()["access_token"] == "new"


def test_callback_listener_bound_before_browser_and_closed(monkeypatch):
    events = []
    server = Mock()
    monkeypatch.setattr(auth, "HTTPServer", lambda *a: events.append("bound") or server)
    monkeypatch.setattr(auth.webbrowser, "open", lambda *a: events.append("browser") or True)
    monkeypatch.setattr("builtins.input", lambda *a: "manual")
    assert auth._capture_callback("url", "http://localhost:8000/callback", "s", timeout=0) == "manual"
    assert events == ["bound", "browser"]
    server.server_close.assert_called_once()


def test_busy_port_manual_fallback(monkeypatch):
    monkeypatch.setattr(auth, "HTTPServer", Mock(side_effect=OSError("busy")))
    monkeypatch.setattr("builtins.input", lambda *a: "manual")
    assert auth._capture_callback("url", "http://localhost:8000/callback", "s") == "manual"


def test_env_preserves_other_settings(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# keep me\nDATABASE_SECRET='other'\nREFRESH_TOKEN='old'\n")
    cli.save_env_values(path, {"REFRESH_TOKEN": "new'quoted"})
    assert dotenv_values(path) == {"DATABASE_SECRET": "other", "REFRESH_TOKEN": "new'quoted"}
    assert "# keep me" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


def test_first_time_market_data_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIENT_ID")
    monkeypatch.delenv("SECRET_KEY")
    answers = iter(["client", "secret"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *a, **kw: next(answers))
    authorize = Mock(return_value={"refresh_token": "fresh"})
    monkeypatch.setattr(auth, "authorize_character", authorize)
    assert cli.handle_esi_auth(["--market-data"])
    authorize.assert_called_once_with(None)
    assert dotenv_values(tmp_path / ".env") == {"CLIENT_ID": "client", "SECRET_KEY": "secret", "REFRESH_TOKEN": "fresh"}


def test_character_uses_configured_env_name(tmp_path, monkeypatch):
    char = SimpleNamespace(key="pilot", name="Pilot", token_env="CUSTOM_TOKEN")
    monkeypatch.setattr(cli, "get_all_characters", lambda: [char])
    monkeypatch.setattr(auth, "authorize_character", Mock(return_value={"refresh_token": "fresh"}))
    assert cli.handle_esi_auth(["--char=pilot"])
    assert dotenv_values(tmp_path / ".env") == {"CUSTOM_TOKEN": "fresh"}
    assert not os.getenv("REFRESH_TOKEN")


def test_invalid_character_does_not_start_oauth():
    assert not cli.handle_esi_auth(["--char=../bad"])


def test_noninteractive_status_and_auth(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert cli.handle_esi_auth(["--status"])
    assert not cli.handle_esi_auth(["--market-data"])


def test_cancel_returns_failure(monkeypatch):
    monkeypatch.setattr(auth, "authorize_character", Mock(side_effect=KeyboardInterrupt))
    assert not cli.handle_esi_auth(["--market-data"])


def test_registry_help_routing():
    from mkts_backend.cli_tools.args_parser import parse_args
    with pytest.raises(SystemExit) as exc:
        parse_args(["esi-auth", "--help"])
    assert exc.value.code == 0


def test_callback_ignores_unrelated_requests_and_wrong_state(monkeypatch):
    from io import BytesIO

    paths = iter(["/favicon.ico", "/callback?code=bad&state=wrong", "/callback?code=ok&state=s"])
    statuses = []
    server = Mock()

    def make_server(address, handler):
        def handle():
            request = object.__new__(handler)
            request.path = next(paths)
            request.wfile = BytesIO()
            request.send_response = statuses.append
            request.send_header = Mock()
            request.end_headers = Mock()
            request.do_GET()
        server.handle_request.side_effect = handle
        return server

    monkeypatch.setattr(auth, "HTTPServer", make_server)
    monkeypatch.setattr(auth.webbrowser, "open", lambda *a: True)
    assert auth._capture_callback("url", "http://localhost:8000/callback", "s").endswith("code=ok&state=s")
    assert statuses == [400, 400, 200]
    server.server_close.assert_called_once()


def test_bad_state_never_exchanges_or_overwrites_token(monkeypatch):
    auth.save_token({"refresh_token": "existing"})
    session = Mock()
    session.authorization_url.return_value = ("url", "expected")
    monkeypatch.setattr(auth, "get_oauth_session", lambda *a: session)
    monkeypatch.setattr(auth, "_capture_callback", lambda *a: "http://localhost:8000/callback?code=c&state=bad")
    with pytest.raises(ValueError, match="state mismatch"):
        auth.authorize_character()
    session.fetch_token.assert_not_called()
    assert auth.load_cached_token() == {"refresh_token": "existing"}


def test_settings_control_callback_and_cache(monkeypatch, tmp_path):
    settings = SimpleNamespace(auth_callback_url="http://127.0.0.1:8765/esi",
                               auth_token_file="file:custom.json", esi_user_agent="test-agent")
    monkeypatch.setattr(auth, "_settings", lambda: settings)
    session = Mock()
    session.headers = {}
    factory = Mock(return_value=session)
    monkeypatch.setattr(auth, "OAuth2Session", factory)
    auth.get_oauth_session(None, ["scope"])
    assert factory.call_args.kwargs["redirect_uri"] == settings.auth_callback_url
    assert session.headers["User-Agent"] == "test-agent"
    auth.save_token({"refresh_token": "cached"})
    assert (tmp_path / "custom.json").exists()
