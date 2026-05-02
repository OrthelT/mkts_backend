"""Centralized market argument parsing for CLI commands."""

import sys

MARKET_DB_MAP: dict[str, str] = {
    "primary": "wcmkt",
    "deployment": "wcmktnorth",
}

MARKET_SYNONYMS: dict[str, str] = {
    "north": "deployment",
}

VALID_MARKET_ALIASES: set[str] = {"primary", "deployment", "both"}

_UNSPECIFIED = "__unspecified__"


def expand_market_alias(alias: str) -> list[str]:
    """Expand a market alias into the list of concrete markets to act on.

    ``"both"`` → ``["primary", "deployment"]``; anything else → ``[alias]``.
    """
    if alias == "both":
        return ["primary", "deployment"]
    return [alias]


def resolve_market_alias(args: list[str]) -> str | None:
    """Return the explicit market alias if the user specified one, else ``None``.

    Distinguishes "user gave no flag" from "user explicitly picked --primary",
    which the subcommand-default logic in ``parse_args`` needs.
    """
    resolved = parse_market_args(args, default=_UNSPECIFIED)
    return None if resolved == _UNSPECIFIED else resolved


def resolve_market_alias_interactive(default: str = "primary") -> str:
    """Prompt the user to pick a market alias when the current choice is ambiguous.

    Returns one of ``primary`` / ``deployment`` / ``both``. In non-TTY
    sessions returns ``default`` without prompting so scripts keep working.
    """
    if not sys.stdin.isatty():
        return default
    from rich.console import Console
    from rich.prompt import Prompt

    console = Console()
    console.print("\n[yellow]This command needs a specific market — pick one:[/yellow]")
    console.print("  1) primary")
    console.print("  2) deployment")
    console.print("  3) both")
    default_choice = {"primary": "1", "deployment": "2", "both": "3"}.get(default, "1")
    choice = Prompt.ask("Choice", choices=["1", "2", "3"], default=default_choice)
    return {"1": "primary", "2": "deployment", "3": "both"}[choice]


def parse_market_args(args: list[str], default: str = "primary") -> str:
    """Scan args for market flags and return a normalized market alias.

    Recognizes --market=<value>, --deployment, --north, --primary, --both,
    and bare positional aliases (e.g. ``deployment`` without ``--``).
    Returns 'primary', 'deployment', or 'both'.
    """
    # First pass: explicit --flags take priority
    for arg in args:
        if arg.startswith("--market="):
            val = arg.split("=", 1)[1].lower()
            resolved = MARKET_SYNONYMS.get(val, val)
            if resolved not in VALID_MARKET_ALIASES:
                print(f"Error: unknown market '{val}'. Valid options: {', '.join(sorted(VALID_MARKET_ALIASES))}")
                sys.exit(1)
            return resolved
        if arg in ("--deployment", "--north"):
            return "deployment"
        if arg == "--primary":
            return "primary"
        if arg == "--both":
            return "both"
    # Second pass: bare positional aliases (e.g. ``mkts-backend deployment ...``)
    _POSITIONAL_ALIASES = VALID_MARKET_ALIASES | set(MARKET_SYNONYMS)
    for arg in args:
        if arg.startswith("-"):
            continue
        resolved = MARKET_SYNONYMS.get(arg, arg)
        if resolved in VALID_MARKET_ALIASES:
            return resolved
    return default
