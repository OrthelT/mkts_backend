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
