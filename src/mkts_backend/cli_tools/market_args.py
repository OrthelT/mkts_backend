"""Centralized market argument parsing for CLI commands."""

MARKET_DB_MAP: dict[str, str] = {
    "primary": "wcmkt",
    "deployment": "wcmktnorth",
}

MARKET_SYNONYMS: dict[str, str] = {
    "north": "deployment",
}


def parse_market_args(args: list[str], default: str = "primary") -> str:
    """Scan args for market flags and return a normalized market alias.

    Recognizes --market=<value>, --deployment, --north, --primary, --both.
    Returns 'primary', 'deployment', or 'both'.
    """
    for arg in args:
        if arg.startswith("--market="):
            val = arg.split("=", 1)[1].lower()
            return MARKET_SYNONYMS.get(val, val)
        if arg in ("--deployment", "--north"):
            return "deployment"
        if arg == "--primary":
            return "primary"
        if arg == "--both":
            return "both"
    return default
