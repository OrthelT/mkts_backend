"""Unit tests for ParsedArgs — zero DB/ESI dependencies."""

import pytest
from mkts_backend.cli_tools.arg_utils import ParsedArgs, ArgError


# ── get_string ──────────────────────────────────────────────────


class TestGetString:
    def test_basic(self):
        p = ParsedArgs(["--file=path/to/fit.txt"])
        assert p.get_string("file") == "path/to/fit.txt"

    def test_missing_returns_default(self):
        p = ParsedArgs(["--other=x"])
        assert p.get_string("file") is None
        assert p.get_string("file", default="nope") == "nope"

    def test_multi_key_alias(self):
        p = ParsedArgs(["--fit-file=abc.txt"])
        assert p.get_string("file", "fit-file") == "abc.txt"

    def test_first_match_wins(self):
        p = ParsedArgs(["--file=first", "--file=second"])
        assert p.get_string("file") == "first"

    def test_value_with_equals(self):
        p = ParsedArgs(["--expr=a=b=c"])
        assert p.get_string("expr") == "a=b=c"

    def test_empty_value(self):
        p = ParsedArgs(["--file="])
        assert p.get_string("file") == ""


# ── get_int ─────────────────────────────────────────────────────


class TestGetInt:
    def test_basic(self):
        p = ParsedArgs(["--fit=42"])
        assert p.get_int("fit") == 42

    def test_missing_returns_default(self):
        p = ParsedArgs([])
        assert p.get_int("fit") is None
        assert p.get_int("fit", default=0) == 0

    def test_multi_key_alias(self):
        p = ParsedArgs(["--fit_id=99"])
        assert p.get_int("fit-id", "fit_id", "fit", "id") == 99

    def test_invalid_raises(self):
        p = ParsedArgs(["--fit=abc"])
        with pytest.raises(ArgError, match="must be an integer"):
            p.get_int("fit")

    def test_negative(self):
        p = ParsedArgs(["--offset=-5"])
        assert p.get_int("offset") == -5


# ── get_float ───────────────────────────────────────────────────


class TestGetFloat:
    def test_basic(self):
        p = ParsedArgs(["--target=0.5"])
        assert p.get_float("target") == 0.5

    def test_integer_string(self):
        p = ParsedArgs(["--target=3"])
        assert p.get_float("target") == 3.0

    def test_missing_returns_default(self):
        p = ParsedArgs([])
        assert p.get_float("target") is None

    def test_invalid_raises(self):
        p = ParsedArgs(["--target=abc"])
        with pytest.raises(ArgError, match="must be a number"):
            p.get_float("target")


# ── get_int_list ────────────────────────────────────────────────


class TestGetIntList:
    def test_single(self):
        p = ParsedArgs(["--fit=42"])
        assert p.get_int_list("fit") == [42]

    def test_multiple(self):
        p = ParsedArgs(["--fit=1,2,3"])
        assert p.get_int_list("fit") == [1, 2, 3]

    def test_spaces(self):
        p = ParsedArgs(["--fit=1, 2, 3"])
        assert p.get_int_list("fit") == [1, 2, 3]

    def test_missing_returns_empty(self):
        p = ParsedArgs([])
        assert p.get_int_list("fit") == []

    def test_invalid_raises(self):
        p = ParsedArgs(["--fit=1,abc,3"])
        with pytest.raises(ArgError, match="comma-separated integers"):
            p.get_int_list("fit")


# ── get_string_list ─────────────────────────────────────────────


class TestGetStringList:
    def test_basic(self):
        p = ParsedArgs(["--ship=Drake,Hurricane"])
        assert p.get_string_list("ship") == ["Drake", "Hurricane"]

    def test_single_item(self):
        p = ParsedArgs(["--ship=Drake"])
        assert p.get_string_list("ship") == ["Drake"]

    def test_missing_returns_empty(self):
        p = ParsedArgs([])
        assert p.get_string_list("ship") == []

    def test_strips_whitespace(self):
        p = ParsedArgs(["--ship= Drake , Hurricane "])
        assert p.get_string_list("ship") == ["Drake", "Hurricane"]


# ── get_choice ──────────────────────────────────────────────────


class TestGetChoice:
    def test_valid(self):
        p = ParsedArgs(["--output=csv"])
        assert p.get_choice("output", choices={"csv", "multibuy", "markdown"}) == "csv"

    def test_case_insensitive(self):
        p = ParsedArgs(["--output=CSV"])
        assert p.get_choice("output", choices={"csv", "multibuy"}) == "csv"

    def test_invalid_raises(self):
        p = ParsedArgs(["--output=json"])
        with pytest.raises(ArgError, match="must be one of"):
            p.get_choice("output", choices={"csv", "multibuy"})

    def test_missing_returns_default(self):
        p = ParsedArgs([])
        assert p.get_choice("output", choices={"csv"}) is None
        assert p.get_choice("output", choices={"csv"}, default="csv") == "csv"


# ── has_flag ────────────────────────────────────────────────────


class TestHasFlag:
    def test_present(self):
        p = ParsedArgs(["--no-jita", "--paste"])
        assert p.has_flag("no-jita") is True
        assert p.has_flag("paste") is True

    def test_absent(self):
        p = ParsedArgs(["--no-jita"])
        assert p.has_flag("verbose") is False

    def test_multi_name(self):
        p = ParsedArgs(["--remote"])
        assert p.has_flag("remote", "r") is True

    def test_valued_arg_not_matched_as_flag(self):
        """--key=value should NOT match has_flag('key') — it's valued, not bare."""
        p = ParsedArgs(["--market=primary"])
        assert p.has_flag("market") is False


# ── has_help ────────────────────────────────────────────────────


class TestHasHelp:
    def test_long_form(self):
        assert ParsedArgs(["--help"]).has_help() is True

    def test_short_form(self):
        assert ParsedArgs(["-h"]).has_help() is True

    def test_absent(self):
        assert ParsedArgs(["--fit=42"]).has_help() is False


# ── positionals ─────────────────────────────────────────────────


class TestPositionals:
    def test_mixed(self):
        p = ParsedArgs(["fit-check", "--fit=42", "extra"])
        assert p.positionals() == ["fit-check", "extra"]

    def test_empty(self):
        assert ParsedArgs([]).positionals() == []


# ── edge cases ──────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_args(self):
        p = ParsedArgs([])
        assert p.get_string("x") is None
        assert p.get_int("x") is None
        assert p.has_flag("x") is False
        assert p.has_help() is False
        assert p.positionals() == []
        assert p.raw == []

    def test_raw_preserves_order(self):
        args = ["--a=1", "pos", "--b=2"]
        p = ParsedArgs(args)
        assert p.raw == args
        # raw is a copy, not the same list
        assert p.raw is not args
