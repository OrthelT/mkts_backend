import os
import json
import time
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from requests_oauthlib import OAuth2Session
from mkts_backend.config.logging_config import configure_logging

load_dotenv()
logger = configure_logging(__name__)

CLIENT_ID = os.getenv("CLIENT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
CALLBACK_URI = "http://localhost:8000/callback"
TOKEN_FILE = "token.json"


def load_cached_token() -> dict | None:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None


def save_token(token: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f)


def get_oauth_session(token: dict | None, scope):
    extra = {"client_id": CLIENT_ID, "client_secret": SECRET_KEY}
    return OAuth2Session(
        CLIENT_ID,
        token=token,
        redirect_uri=CALLBACK_URI,
        scope=scope,
        auto_refresh_url=TOKEN_URL,
        auto_refresh_kwargs=extra,
        token_updater=save_token,
    )


def get_token(requested_scope):
    if not CLIENT_ID:
        raise ValueError("CLIENT_ID environment variable is not set")
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is not set")
    if not REFRESH_TOKEN:
        raise ValueError("REFRESH_TOKEN environment variable is not set")

    token = load_cached_token()
    if not token:
        logger.info("No token.json → refreshing from GitHub secret")
        try:
            logger.info(f"Attempting to refresh token with CLIENT_ID: {CLIENT_ID[:8]}...")
            logger.info(f"Refresh token length: {len(REFRESH_TOKEN) if REFRESH_TOKEN else 'None'}")
            logger.info(f"Requested scope: {requested_scope}")

            token = OAuth2Session(CLIENT_ID, scope=requested_scope).refresh_token(
                TOKEN_URL,
                refresh_token=REFRESH_TOKEN,
                client_id=CLIENT_ID,
                client_secret=SECRET_KEY,
            )
            save_token(token)
            logger.info("Token refreshed successfully")
            return token
        except Exception as e:
            logger.error(f"Failed to refresh token: {e}")
            logger.error(f"CLIENT_ID: {CLIENT_ID}")
            logger.error(
                f"REFRESH_TOKEN length: {len(REFRESH_TOKEN) if REFRESH_TOKEN else 'None'}"
            )
            raise
    else:
        oauth = get_oauth_session(token, requested_scope)

        if token["expires_at"] < time.time():
            logger.info("Token expired → refreshing")
            try:
                oauth.refresh_token(TOKEN_URL, refresh_token=token["refresh_token"])
                new_token = oauth.token
                save_token(new_token)
                return new_token
            except Exception as e:
                logger.error(f"Failed to refresh cached token: {e}")
                raise
        else:
            return token


def get_token_for_character(char_key: str, refresh_token: str, scope):
    """
    Get an OAuth token for a specific character.

    Uses a per-character token cache file (token_<char_key>.json) and the
    shared CLIENT_ID / SECRET_KEY credentials.

    Args:
        char_key: Character key (e.g. "dennis") — used for cache filename
        refresh_token: The character's ESI refresh token
        scope: OAuth scope(s) to request

    Returns:
        OAuth token dict

    Raises:
        ValueError: If CLIENT_ID or SECRET_KEY is missing
        Exception: If token refresh fails
    """
    if not CLIENT_ID:
        raise ValueError("CLIENT_ID environment variable is not set")
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is not set")

    token_file = f"token_{char_key}.json"

    # Try loading cached token
    token = None
    if os.path.exists(token_file):
        with open(token_file, "r") as f:
            token = json.load(f)

    def _save(t):
        with open(token_file, "w") as f:
            json.dump(t, f)

    if token and token.get("expires_at", 0) > time.time():
        return token

    # Refresh using the character's refresh token
    logger.info(f"Refreshing token for character '{char_key}'")
    rt = token.get("refresh_token", refresh_token) if token else refresh_token
    token = OAuth2Session(CLIENT_ID, scope=scope).refresh_token(
        TOKEN_URL,
        refresh_token=rt,
        client_id=CLIENT_ID,
        client_secret=SECRET_KEY,
    )
    _save(token)
    return token


REQUIRED_SCOPES = [
    "esi-universe.read_structures.v1",
    "esi-assets.read_assets.v1",
    "esi-markets.structure_markets.v1",
    "esi-assets.read_corporation_assets.v1",
]


SUCCESS_HTML = b"""<!DOCTYPE html>
<html><head><title>mkts-backend</title>
<style>
  body { font-family: system-ui, sans-serif; display: flex; justify-content: center;
         align-items: center; height: 100vh; margin: 0; background: #1a1a2e; color: #eee; }
  .card { text-align: center; padding: 3em; border-radius: 12px;
          background: #16213e; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
  h1 { color: #4ecca3; margin-bottom: 0.5em; }
  p { color: #aaa; }
</style></head>
<body><div class="card">
  <h1>Authorization Successful!</h1>
  <p>You can close this tab and return to the terminal.</p>
</div></body></html>"""


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures a single OAuth redirect and serves a success page."""

    redirect_url: str | None = None

    def do_GET(self):
        _OAuthCallbackHandler.redirect_url = f"http://localhost:8000{self.path}"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(SUCCESS_HTML)

    def log_message(self, format, *args):
        logger.debug(f"OAuth callback: {format % args}")


def _wait_for_callback(port: int = 8000, timeout: int = 120) -> str | None:
    """Start a one-shot HTTP server and wait for the OAuth callback.

    Returns the full redirect URL, or None on timeout.
    """
    _OAuthCallbackHandler.redirect_url = None

    try:
        server = HTTPServer(("localhost", port), _OAuthCallbackHandler)
    except OSError as e:
        logger.warning(f"Could not start callback server: {e}")
        return None

    server.timeout = timeout

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    server.server_close()
    return _OAuthCallbackHandler.redirect_url


def authorize_character(char_key: str, scopes: list[str] | None = None):
    """
    Run an interactive OAuth authorization flow for a character.

    Opens the ESI authorize URL in the user's browser, runs a threaded
    callback server on localhost:8000 to capture the redirect (with 120s
    timeout), and falls back to manual URL pasting if the server fails.

    Saves tokens to token_<char_key>.json.

    Args:
        char_key: Character key (e.g. "dennis") — used for cache filename
        scopes: OAuth scopes to request (defaults to REQUIRED_SCOPES)
    """
    if not CLIENT_ID:
        raise ValueError("CLIENT_ID environment variable is not set")
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is not set")

    scopes = scopes or REQUIRED_SCOPES

    oauth = OAuth2Session(CLIENT_ID, redirect_uri=CALLBACK_URI, scope=scopes)
    auth_url, state = oauth.authorization_url(AUTH_URL)

    print(f"\nAuthorizing character '{char_key}' with scopes:")
    for s in scopes:
        print(f"  - {s}")
    print(f"\nOpening browser to authorize...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Try automatic callback capture via threaded server
    print("Waiting for authorization (timeout: 120s)...")
    redirect_url = _wait_for_callback(port=8000, timeout=120)

    if not redirect_url:
        print("\nAutomatic capture failed or timed out.")
        redirect_url = input("Paste the full redirect URL here: ").strip()
        if not redirect_url:
            print("No URL provided. Aborting.")
            return

    # Exchange via authorization_response (lets OAuth2Session handle code + state)
    token = oauth.fetch_token(
        TOKEN_URL,
        authorization_response=redirect_url,
        client_secret=SECRET_KEY,
    )

    token_file = f"token_{char_key}.json"
    with open(token_file, "w") as f:
        json.dump(token, f)

    print(f"\nToken saved to {token_file}")
    print(f"Scopes granted: {token.get('scope', 'unknown')}")
    print(f"Character '{char_key}' is now authorized.")


if __name__ == "__main__":
    pass

