"""Rich auth management menu adapted from esi-market-tool's setup wizard."""
import argparse
import os
from pathlib import Path
import sys

from dotenv import load_dotenv, set_key
from oauthlib.oauth2 import OAuth2Error
from requests import RequestException
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from mkts_backend.config.settings_service import SettingsService, get_all_characters
from mkts_backend.esi import esi_auth
from mkts_backend.utils.validation import _get_env_file_path

console = Console()


def save_env_values(path: Path, values: dict[str, str]) -> None:
    """Preserve unrelated dotenv entries and comments; never display secrets."""
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    for key, value in values.items():
        set_key(str(path), key, value)
        os.environ[key] = value


def configure_credentials(path: Path) -> bool:
    console.print("Register your application at https://developers.eveonline.com/")
    console.print("Callback: " + SettingsService().auth_callback_url, markup=False)
    console.print("Market data scope: " + " ".join(esi_auth.MARKET_SCOPES))
    console.print("Character scopes: " + " ".join(esi_auth.REQUIRED_SCOPES))
    console.print("Leave a field blank to keep its current value.")
    client_id = Prompt.ask("CLIENT_ID").strip() or os.getenv("CLIENT_ID", "")
    secret = Prompt.ask("SECRET_KEY", password=True).strip() or os.getenv("SECRET_KEY", "")
    if not client_id or not secret:
        console.print("Both CLIENT_ID and SECRET_KEY are required.")
        return False
    save_env_values(path, {"CLIENT_ID": client_id, "SECRET_KEY": secret})
    console.print("Credentials saved. Authorize again if you changed the application.")
    return True


def authorize(path: Path, char_key: str | None = None) -> bool:
    characters = {c.key: c for c in get_all_characters()}
    if char_key is not None and char_key not in characters:
        raise ValueError(f"Unknown configured character: {char_key}")
    if not os.getenv("CLIENT_ID") or not os.getenv("SECRET_KEY"):
        if not configure_credentials(path):
            return False
    if char_key:
        console.print(f"Log in as {characters[char_key].name} for '{char_key}'.", markup=False)
    else:
        console.print("Log in as the character with access to your market structure.")
    token = esi_auth.authorize_character(char_key)
    key = characters[char_key].token_env if char_key else "REFRESH_TOKEN"
    save_env_values(path, {key: token["refresh_token"]})
    console.print(f"Authorized. Saved {key} in {path} and cache in {esi_auth.token_path(char_key)}.", markup=False)
    return True


def show_status(path: Path) -> None:
    console.print(f"ESI authentication — credentials: {path}", markup=False)
    table = Table("Target", "Refresh token in environment", "Token cache")
    targets = [("Market data", None, "REFRESH_TOKEN")]
    targets.extend((c.name, c.key, c.token_env) for c in get_all_characters())
    for name, char_key, env in targets:
        table.add_row(name, "Configured" if os.getenv(env) else "Missing",
                      "Present" if esi_auth.load_cached_token(esi_auth.token_path(char_key)) else "Missing")
    console.print(table)
    console.print("Application credentials: " + (
        "Configured" if os.getenv("CLIENT_ID") and os.getenv("SECRET_KEY") else "Missing"))


def handle_esi_auth(args: list[str], market_alias: str = "primary") -> bool:
    parser = argparse.ArgumentParser(prog="mkts-backend esi-auth", description="Manage ESI credentials and browser authorization")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--market-data", action="store_true", help="Authorize market data access")
    target.add_argument("--char", help="Authorize a configured character key")
    target.add_argument("--status", action="store_true", help="Show credential presence without secrets")
    try:
        options = parser.parse_args(args)
    except SystemExit as exc:
        return exc.code == 0
    path = _get_env_file_path()
    load_dotenv(path, override=False)
    try:
        if options.status:
            show_status(path)
            return True
        if not sys.stdin.isatty():
            console.print("Authorization requires a terminal. Run: mkts-backend esi-auth")
            return False
        if options.market_data or options.char is not None:
            return authorize(path, options.char)
        while True:
            show_status(path)
            console.print("1. Configure application credentials\n2. Authorize market data access\n3. Authorize character\nq. Quit")
            choice = Prompt.ask("Choose", choices=["1", "2", "3", "q"], default="q")
            if choice == "q":
                return True
            if choice == "1":
                configure_credentials(path)
            elif choice == "2":
                authorize(path)
            else:
                characters = get_all_characters()
                if not characters:
                    console.print("Add your characters to settings.toml first.")
                    continue
                for char in characters:
                    console.print(f"{char.key}: {char.name}", markup=False)
                key = Prompt.ask("Character key", choices=[c.key for c in characters])
                authorize(path, key)
    except (KeyboardInterrupt, EOFError):
        console.print("Authorization cancelled.")
        return False
    except (OAuth2Error, RequestException):
        # Exception strings may include response bodies or authorization URLs.
        console.print("EVE authorization failed. Check application credentials and scopes, then retry esi-auth.")
        return False
    except (ValueError, OSError) as exc:
        console.print(f"ESI setup failed: {exc}", markup=False)
        return False
