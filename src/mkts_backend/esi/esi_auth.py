"""ESI tokens and browser authorization, shared by collection and the auth TUI."""
import functools
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv
from requests_oauthlib import OAuth2Session


load_dotenv()
AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
REQUIRED_SCOPES = [
    "esi-universe.read_structures.v1",
    "esi-assets.read_assets.v1",
    "esi-markets.structure_markets.v1",
    "esi-assets.read_corporation_assets.v1",
]
MARKET_SCOPES = ["esi-markets.structure_markets.v1"]
# How long one callback connection may take to send its request line.
CALLBACK_READ_TIMEOUT = 5


def _settings():
    # config.__init__ imports ESIConfig, which imports this module.
    from mkts_backend.config.settings_service import SettingsService
    return SettingsService()


def token_path(char_key: str | None = None) -> Path:
    if char_key is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", char_key):
            raise ValueError("Character keys must contain only letters, numbers, underscores or hyphens")
        return Path(f"token_{char_key}.json")
    return Path(_settings().auth_token_file.removeprefix("file:"))


def load_cached_token(path: Path | None = None) -> dict | None:
    try:
        token = json.loads((path or token_path()).read_text())
        return token if isinstance(token, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def save_token(token: dict, path: Path | None = None):
    """Replace the cache atomically, with owner-only permissions."""
    path = path or token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".esi-token-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(token, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _credentials() -> tuple[str, str]:
    client_id, secret = os.getenv("CLIENT_ID"), os.getenv("SECRET_KEY")
    if not client_id or not secret:
        raise ValueError("ESI credentials missing. Run: mkts-backend esi-auth")
    return client_id, secret


def get_oauth_session(token: dict | None, scope, path: Path | None = None):
    client_id, secret = _credentials()
    session = OAuth2Session(
        client_id, token=token, scope=scope,
        redirect_uri=_settings().auth_callback_url,
        auto_refresh_url=TOKEN_URL,
        auto_refresh_kwargs={"client_id": client_id, "client_secret": secret},
        # Bind the cache file, or an auto-refresh would write a character token
        # over the market token.
        token_updater=functools.partial(save_token, path=path or token_path()),
    )
    session.headers["User-Agent"] = _settings().esi_user_agent
    return session


def _get_token(path: Path, refresh_token: str | None, scope, command: str):
    client_id, secret = _credentials()
    token = load_cached_token(path)
    if token and token.get("access_token") and token.get("expires_at", 0) > time.time():
        return token
    refresh_token = (token or {}).get("refresh_token") or refresh_token
    if not refresh_token:
        raise ValueError(f"No ESI refresh token. Run: {command}")
    session = get_oauth_session(token, scope, path)
    token = session.refresh_token(
        TOKEN_URL, refresh_token=refresh_token,
        client_id=client_id, client_secret=secret, timeout=30,
    )
    save_token(token, path)
    return token


def get_token(requested_scope):
    # A cached token can bootstrap collection without an environment refresh token.
    return _get_token(token_path(), os.getenv("REFRESH_TOKEN"), requested_scope,
                      "mkts-backend esi-auth --market-data")


def get_token_for_character(char_key: str, refresh_token: str, scope):
    return _get_token(token_path(char_key), refresh_token, scope,
                      f"mkts-backend esi-auth --char={char_key}")


def _callback_code(url: str, callback: str, state: str) -> str:
    actual, expected = urlsplit(url), urlsplit(callback)
    if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
        raise ValueError("The redirect URL does not match the configured callback")
    params = parse_qs(actual.query)
    # Check the denial before the state: EVE omits state on some error redirects,
    # and a mismatch message would send the user looking for the wrong problem.
    if "error" in params:
        raise ValueError("EVE authorization was denied; restart authorization")
    states = params.get("state", [])
    if len(states) != 1 or not secrets.compare_digest(states[0], state):
        raise ValueError("OAuth state mismatch; restart authorization")
    codes = params.get("code", [])
    if len(codes) != 1:
        raise ValueError("The callback did not contain an authorization code")
    return codes[0]


def _capture_callback(auth_url: str, callback: str, state: str, timeout: float = 120) -> str:
    """Bind before opening the browser; ignore unrelated requests and bad state."""
    parsed = urlsplit(callback)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Automatic authorization requires an http loopback callback URL")
    result = []

    class Handler(BaseHTTPRequestHandler):
        # Without this, a browser preconnect that sends no request line makes
        # handle_request() block past the deadline.
        timeout = CALLBACK_READ_TIMEOUT

        def do_GET(self):
            url = f"{parsed.scheme}://{parsed.netloc}{self.path}"
            try:
                _callback_code(url, callback, state)
            except ValueError:
                # A genuine denial ends the wait; other requests must not consume it.
                query = parse_qs(urlsplit(url).query)
                if urlsplit(url).path == parsed.path and "error" in query:
                    result.append(url)
                self.send_response(400)
                body = b"Authorization not completed. Return to the terminal."
            else:
                result.append(url)
                self.send_response(200)
                body = b"Authorization received. Return to the terminal to finish."
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass  # Callback URLs contain authorization codes.

    try:
        server = HTTPServer((parsed.hostname, parsed.port or 80), Handler)
    except OSError:
        server = None
    print(f"Open this URL if the browser does not open:\n{auth_url}")
    try:
        try:
            webbrowser.open(auth_url)
        except webbrowser.Error:
            # The printed URL still reaches the listener when opened by hand.
            pass
        if server:
            print(f"Waiting for browser authorization (up to {timeout:.0f} seconds)...")
            deadline = time.monotonic() + timeout
            while not result and time.monotonic() < deadline:
                server.timeout = min(0.5, max(0, deadline - time.monotonic()))
                server.handle_request()
    finally:
        if server:
            server.server_close()
    if result:
        return result[0]
    print("Automatic capture unavailable. Complete login using the URL above.")
    return input("Paste the full redirect URL: ").strip()


def authorize_character(char_key: str | None = None, scopes: list[str] | None = None):
    """Authorize market data access (None) or a configured character. Return the token."""
    from mkts_backend.config.settings_service import get_all_characters

    if char_key is not None and char_key not in {c.key for c in get_all_characters()}:
        raise ValueError(f"Unknown configured character: {char_key}")
    path = token_path(char_key)
    _, secret = _credentials()
    scopes = scopes or (REQUIRED_SCOPES if char_key else MARKET_SCOPES)
    oauth = get_oauth_session(None, scopes)
    callback = _settings().auth_callback_url
    auth_url, state = oauth.authorization_url(AUTH_URL)
    redirect = _capture_callback(auth_url, callback, state)
    # Validate locally then pass only the code to the HTTPS token endpoint.
    # No process-wide OAUTHLIB_INSECURE_TRANSPORT override is necessary.
    code = _callback_code(redirect, callback, state)
    token = oauth.fetch_token(TOKEN_URL, code=code, client_secret=secret, timeout=30)
    if not token.get("refresh_token"):
        raise ValueError("EVE did not return a refresh token; authorize again")
    save_token(token, path)
    return token
