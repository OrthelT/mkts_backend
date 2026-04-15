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

# Sentinel returned by resolve_market_alias when the user gave no market flag.
_UNSPECIFIED = "__unspecified__"


def expand_market_alias(alias: str) -> list[str]:
    """Expand a market alias into the list of concrete markets to act on.

    ``"both"`` → ``["primary", "deployment"]``; anything else → ``[alias]``.
    """
    if alias == "both":
        return ["primary", "deployment"]
    return [alias]


def resolve_market_alias(args: list[str], default: str) -> str:
    """Like ``parse_market_args`` but returns ``default`` only when the user
    gave no market flag at all. Lets callers distinguish "unspecified" from
    an explicit ``--primary``.
    """
    resolved = parse_market_args(args, default=_UNSPECIFIED)
    return default if resolved == _UNSPECIFIED else resolved


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
