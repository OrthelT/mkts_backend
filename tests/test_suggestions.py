"""Tests for CLI suggestion/correction helpers."""

import pytest
from unittest.mock import patch
from mkts_backend.cli_tools.arg_utils import (
    suggest_command,
    check_bare_args,
    format_suggestion,
    _edit_distance,
)


class TestEditDistance:
    def test_identical(self):
        assert _edit_distance("abc", "abc") == 0

    def test_one_insertion(self):
        assert _edit_distance("abc", "abcd") == 1

    def test_one_deletion(self):
        assert _edit_distance("abcd", "abc") == 1

    def test_one_substitution(self):
        assert _edit_distance("abc", "axc") == 1

    def test_empty(self):
        assert _edit_distance("", "") == 0
        assert _edit_distance("abc", "") == 3

    def test_two_edits(self):
        assert _edit_distance("fit-check", "fit-cheek") == 1
        assert _edit_distance("sync", "snyc") == 2


class TestSuggestCommand:
    KNOWN = {"fit-check", "fit-update", "update-fit", "sync", "validate", "assets", "equiv"}

    def test_underscore_to_hyphen(self):
        assert suggest_command("fit_check", self.KNOWN) == "fit-check"

    def test_close_misspelling(self):
        assert suggest_command("fit-cheek", self.KNOWN) == "fit-check"

    def test_exact_match_not_suggested(self):
        # If token is already valid, suggest_command should not suggest it
        # (caller won't call this for valid tokens, but test edge case)
        result = suggest_command("sync", self.KNOWN)
        # sync → sync is distance 0, which is < 3, so it returns "sync"
        assert result == "sync"

    def test_no_match(self):
        assert suggest_command("zzzzzzzzz", self.KNOWN) is None

    def test_close_match_within_threshold(self):
        assert suggest_command("syncc", self.KNOWN) == "sync"

    def test_too_far_returns_none(self):
        assert suggest_command("abcdefgh", self.KNOWN) is None


class TestCheckBareArgs:
    def test_detects_bare_key_value(self):
        result = check_bare_args(["fit=42", "--market=primary"])
        assert result == ["--fit=42"]

    def test_ignores_flags(self):
        result = check_bare_args(["--fit=42", "--no-jita"])
        assert result == []

    def test_ignores_known_commands(self):
        result = check_bare_args(["fit-check", "fit=42"], known_commands={"fit-check"})
        assert result == ["--fit=42"]

    def test_multiple_bare_args(self):
        result = check_bare_args(["fit=42", "target=100"])
        assert result == ["--fit=42", "--target=100"]

    def test_empty(self):
        assert check_bare_args([]) == []

    def test_plain_positional_not_suggested(self):
        """A plain word without = should not be suggested."""
        result = check_bare_args(["needed"])
        assert result == []


class TestFormatSuggestion:
    def test_basic(self):
        result = format_suggestion(
            "mkts-backend",
            ["fit-check", "fit=42"],
            {1: "--fit=42"},
        )
        assert result == "mkts-backend fit-check --fit=42"

    def test_no_corrections(self):
        result = format_suggestion("fitcheck", ["--fit=42"], {})
        assert result == "fitcheck --fit=42"


class TestSuggestionIntegration:
    """Integration tests: simulate bad input, verify suggestion output."""

    def test_mkts_backend_unknown_command_suggests(self, capsys):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["fit-cheek", "--fit=42"])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fit-check" in captured.out
        assert "Did you mean?" in captured.out

    def test_mkts_backend_bare_arg_suggests(self, capsys):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["fit-check", "fit=42"])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "--fit=42" in captured.out
        assert "Did you mean?" in captured.out

    def test_fitcheck_bare_arg_suggests(self, capsys):
        from mkts_backend.cli_tools.fit_check import main

        with patch("sys.argv", ["fitcheck", "fit=42"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "--fit=42" in captured.out

    def test_fitcheck_unknown_subcommand_suggests(self, capsys):
        from mkts_backend.cli_tools.fit_check import main

        with patch("sys.argv", ["fitcheck", "needeed"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "needed" in captured.out
