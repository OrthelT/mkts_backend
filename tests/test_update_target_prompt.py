"""Regression locks for the interactive `fit-manager update-target` flow.

Bug: `update-target --fit-id=139` (no `--target`) silently defaulted the
target to 100 and wrote it with no prompt and no confirmation. The
dispatcher injected `default=100`, which made its missing-target guard
dead code.

Expected behavior:
- Missing ``--target`` in a non-TTY session: error, no write.
- Missing ``--target`` in a TTY: prompt for the quantity (IntPrompt).
- Before writing (TTY): confirm via Confirm.ask; decline => no write.
"""
from unittest.mock import patch

from mkts_backend.cli_tools.command_registry import get_registry


def _fit_update_handler():
    entry = get_registry().resolve("fit-manager")
    assert entry is not None
    return entry.handler


class TestUpdateTargetMissingTarget:
    def test_non_tty_missing_target_does_not_default_to_100(self):
        """The core regression: no --target in automation must NOT write 100."""
        handler = _fit_update_handler()
        with patch("sys.stdin.isatty", return_value=False), \
             patch("mkts_backend.cli_tools.fit_update.update_target_command") as spy:
            result = handler(
                ["update-target", "--fit-id=139", "--market=primary"],
                "primary",
            )
        spy.assert_not_called()
        assert result is False

    def test_tty_missing_target_prompts_quantity_then_confirms_then_writes(self):
        handler = _fit_update_handler()
        with patch("sys.stdin.isatty", return_value=True), \
             patch("mkts_backend.cli_tools.fit_update.IntPrompt.ask",
                   return_value=42) as ask, \
             patch("mkts_backend.cli_tools.fit_update.Confirm.ask",
                   return_value=True) as confirm, \
             patch("mkts_backend.cli_tools.fit_update.update_target_command",
                   return_value=True) as spy:
            result = handler(
                ["update-target", "--fit-id=139", "--market=primary"],
                "primary",
            )
        ask.assert_called_once()
        confirm.assert_called_once()
        spy.assert_called_once()
        assert spy.call_args.args[0] == 139
        assert spy.call_args.args[1] == 42   # prompted value, not 100
        assert result is True


class TestUpdateTargetConfirmation:
    def test_tty_declining_confirmation_aborts_write(self):
        handler = _fit_update_handler()
        with patch("sys.stdin.isatty", return_value=True), \
             patch("mkts_backend.cli_tools.fit_update.Confirm.ask",
                   return_value=False) as confirm, \
             patch("mkts_backend.cli_tools.fit_update.update_target_command") as spy:
            result = handler(
                ["update-target", "--fit-id=139", "--target=50",
                 "--market=primary"],
                "primary",
            )
        confirm.assert_called_once()
        spy.assert_not_called()
        assert result is False

    def test_non_tty_explicit_target_writes_without_confirmation(self):
        """Automation path: explicit --target proceeds, no Confirm prompt."""
        handler = _fit_update_handler()
        with patch("sys.stdin.isatty", return_value=False), \
             patch("mkts_backend.cli_tools.fit_update.Confirm.ask") as confirm, \
             patch("mkts_backend.cli_tools.fit_update.update_target_command",
                   return_value=True) as spy:
            result = handler(
                ["update-target", "--fit-id=139", "--target=50",
                 "--market=primary"],
                "primary",
            )
        confirm.assert_not_called()
        spy.assert_called_once()
        assert spy.call_args.args[1] == 50
        assert result is True
