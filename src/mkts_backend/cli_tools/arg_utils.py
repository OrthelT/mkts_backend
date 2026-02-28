"""Reusable argument parsing utilities for CLI commands.

Provides ParsedArgs — a thin wrapper around a list of CLI arguments with
typed extractors that replace the repetitive ``arg.startswith("--key="): val
= arg.split("=", 1)[1]`` pattern used throughout the codebase.

Usage::

    p = ParsedArgs(["--fit=42", "--market=primary", "--no-jita"])
    fit_id = p.get_int("fit-id", "fit_id", "fit", "id")      # 42
    market = p.get_string("market", default="primary")         # "primary"
    no_jita = p.has_flag("no-jita")                            # True
"""

from __future__ import annotations

import re


class ArgError(Exception):
    """Raised when an argument value fails validation."""


class ParsedArgs:
    """Typed extractors over a raw ``list[str]`` of CLI arguments.

    Every ``get_*`` method accepts one or more key names (without the ``--``
    prefix).  The first matching ``--key=value`` wins.  Flags (bare ``--name``
    tokens with no ``=``) are detected by :meth:`has_flag`.
    """

    def __init__(self, args: list[str]) -> None:
        self._args = list(args)

    # ── raw access ──────────────────────────────────────────────

    @property
    def raw(self) -> list[str]:
        """Return the original argument list."""
        return self._args

    def positionals(self) -> list[str]:
        """Return non-flag arguments (don't start with ``--``)."""
        return [a for a in self._args if not a.startswith("--")]

    # ── typed extractors ────────────────────────────────────────

    def _find_value(self, *keys: str) -> str | None:
        """Scan args for the first ``--key=value`` matching any *keys*."""
        for arg in self._args:
            for key in keys:
                prefix = f"--{key}="
                if arg.startswith(prefix):
                    return arg.split("=", 1)[1]
        return None

    def get_string(self, *keys: str, default: str | None = None) -> str | None:
        """Extract a string value for the first matching key."""
        val = self._find_value(*keys)
        return val if val is not None else default

    def get_int(self, *keys: str, default: int | None = None) -> int | None:
        """Extract an integer value.  Raises :class:`ArgError` on bad input."""
        val = self._find_value(*keys)
        if val is None:
            return default
        try:
            return int(val)
        except ValueError:
            raise ArgError(f"--{keys[0]} must be an integer, got '{val}'")

    def get_float(self, *keys: str, default: float | None = None) -> float | None:
        """Extract a float value.  Raises :class:`ArgError` on bad input."""
        val = self._find_value(*keys)
        if val is None:
            return default
        try:
            return float(val)
        except ValueError:
            raise ArgError(f"--{keys[0]} must be a number, got '{val}'")

    def get_int_list(self, *keys: str) -> list[int]:
        """Extract a comma-separated list of integers (e.g. ``--fit=1,2,3``)."""
        val = self._find_value(*keys)
        if val is None:
            return []
        parts = [p.strip() for p in val.split(",") if p.strip()]
        try:
            return [int(p) for p in parts]
        except ValueError as exc:
            raise ArgError(
                f"--{keys[0]} must be comma-separated integers, got '{val}'"
            ) from exc

    def get_string_list(self, *keys: str) -> list[str]:
        """Extract a comma-separated list of strings."""
        val = self._find_value(*keys)
        if val is None:
            return []
        return [p.strip() for p in val.split(",") if p.strip()]

    def get_choice(
        self, *keys: str, choices: set[str] | list[str], default: str | None = None
    ) -> str | None:
        """Extract a string value and validate it against *choices*."""
        val = self._find_value(*keys)
        if val is None:
            return default
        val_lower = val.lower()
        choices_set = set(choices)
        if val_lower not in choices_set:
            raise ArgError(
                f"--{keys[0]} must be one of: {', '.join(sorted(choices_set))}, got '{val}'"
            )
        return val_lower

    def has_flag(self, *names: str) -> bool:
        """Return ``True`` if any bare ``--name`` flag is present.

        This matches both ``--name`` (bare flag) and ``--name=...`` (valued).
        For flags that are purely boolean, only bare ``--name`` is typical.
        """
        for arg in self._args:
            for name in names:
                if arg == f"--{name}":
                    return True
        return False

    def has_help(self) -> bool:
        """Return ``True`` if ``--help`` or ``-h`` is present."""
        return "--help" in self._args or "-h" in self._args


# ── Suggestion helpers ──────────────────────────────────────────

# Simple Levenshtein distance (no external deps)
def _edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between *a* and *b*."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


_BARE_KV_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*)=(.+)$")


def suggest_command(token: str, known_names: set[str]) -> str | None:
    """Suggest a correction for a mistyped command token.

    Checks (in order):
    1. Underscore/hyphen normalization (``fit_check`` → ``fit-check``)
    2. Levenshtein distance ≤ 2
    """
    # Normalize underscores to hyphens
    normalized = token.replace("_", "-")
    if normalized in known_names and normalized != token:
        return normalized

    # Fuzzy match
    best, best_dist = None, 3  # threshold = 2
    for name in known_names:
        d = _edit_distance(token, name)
        if d < best_dist:
            best, best_dist = name, d
    return best


def check_bare_args(args: list[str], known_commands: set[str] | None = None) -> list[str]:
    """Detect ``key=value`` args missing the ``--`` prefix.

    Returns a list of suggestions like ``"--fit=42"`` for each bare
    ``"fit=42"`` found.  Positional command names (e.g. ``"fit-check"``)
    are excluded from suggestions.
    """
    suggestions: list[str] = []
    skip = known_commands or set()
    for arg in args:
        if arg.startswith("--") or arg.startswith("-"):
            continue
        if arg in skip:
            continue
        m = _BARE_KV_RE.match(arg)
        if m:
            suggestions.append(f"--{arg}")
    return suggestions


def format_suggestion(entry_point: str, original_args: list[str], corrections: dict[int, str]) -> str:
    """Build a copy-pasteable corrected command string.

    *corrections* maps arg index → corrected value.
    """
    fixed = list(original_args)
    for idx, replacement in corrections.items():
        fixed[idx] = replacement
    return f"{entry_point} {' '.join(fixed)}"
