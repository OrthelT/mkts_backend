"""Routing tests for ``build-watchlist [add|remove|sync]``.

Mocks the watchlist_sync helpers — these tests verify dispatch and arg
parsing only, not DB or SDE behavior (covered in test_watchlist_sync.py).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _run_cli(argv: list[str]):
    from mkts_backend.cli_tools.args_parser import parse_args

    with pytest.raises(SystemExit) as exc_info:
        parse_args(argv)
    return exc_info.value.code


class TestBuildWatchlistRouting:
    @patch("mkts_backend.cli_tools.build_watchlist_cli.add_to_build_watchlist")
    @patch("mkts_backend.cli_tools.build_watchlist_cli.DatabaseConfig")
    def test_add_routes_with_type_id(self, mock_db, mock_add):
        from mkts_backend.builder_costs.watchlist_sync import AddResult

        mock_add.return_value = AddResult(added=2)
        code = _run_cli(["build-watchlist", "add", "--type_id=34,35"])

        assert code == 0
        mock_add.assert_called_once()
        _, kwargs = mock_add.call_args
        # third positional arg is type_ids; force kwarg defaults to False.
        assert mock_add.call_args.args[2] == [34, 35]
        assert kwargs.get("force") is False

    @patch("mkts_backend.cli_tools.build_watchlist_cli.add_to_build_watchlist")
    @patch("mkts_backend.cli_tools.build_watchlist_cli.DatabaseConfig")
    def test_add_with_force_passes_through(self, mock_db, mock_add):
        from mkts_backend.builder_costs.watchlist_sync import AddResult

        mock_add.return_value = AddResult(added=1, skipped=[36])
        _run_cli(["build-watchlist", "add", "--type_id=36", "--force"])

        _, kwargs = mock_add.call_args
        assert kwargs.get("force") is True

    @patch("mkts_backend.cli_tools.build_watchlist_cli.remove_from_build_watchlist")
    @patch("mkts_backend.cli_tools.build_watchlist_cli.DatabaseConfig")
    def test_remove_routes(self, mock_db, mock_remove):
        from mkts_backend.builder_costs.watchlist_sync import RemoveResult

        mock_remove.return_value = RemoveResult(removed=1)
        code = _run_cli(["build-watchlist", "remove", "--type_id=34"])

        assert code == 0
        mock_remove.assert_called_once()
        assert mock_remove.call_args.args[1] == [34]

    @patch("mkts_backend.cli_tools.build_watchlist_cli.sync_from_market")
    @patch("mkts_backend.cli_tools.build_watchlist_cli.DatabaseConfig")
    def test_mirror_routes(self, mock_db, mock_sync):
        from mkts_backend.builder_costs.watchlist_sync import SyncResult

        mock_db.return_value.sync.return_value = None
        mock_sync.return_value = SyncResult(market_size=10, already_present=8, added=2)
        code = _run_cli(["build-watchlist", "mirror"])

        assert code == 0
        mock_sync.assert_called_once()

    @patch("mkts_backend.cli_tools.build_watchlist_cli.DatabaseConfig")
    def test_sync_routes_to_db_sync_only(self, mock_db):
        """'build-watchlist sync' just calls db.sync(), no reconciliation."""
        mock_db.return_value.sync.return_value = None
        mock_db.return_value.path = "/tmp/buildcost.db"
        code = _run_cli(["build-watchlist", "sync"])

        assert code == 0
        mock_db.return_value.sync.assert_called_once()

    def test_missing_subcommand_errors(self, capsys):
        code = _run_cli(["build-watchlist"])
        assert code != 0
        captured = capsys.readouterr()
        assert "requires a subcommand" in captured.out

    def test_unknown_subcommand_errors_with_suggestion(self, capsys):
        code = _run_cli(["build-watchlist", "addd"])
        assert code != 0
        captured = capsys.readouterr()
        assert "unknown subcommand" in captured.out
        assert "Did you mean: add" in captured.out

    def test_add_help_reaches_subcommand(self, capsys):
        code = _run_cli(["build-watchlist", "add", "--help"])
        assert code == 0
        captured = capsys.readouterr()
        assert "build-watchlist add" in captured.out
        assert "--force" in captured.out

    def test_top_level_help(self, capsys):
        code = _run_cli(["build-watchlist", "--help"])
        assert code == 0
        captured = capsys.readouterr()
        assert "USAGE:" in captured.out
        assert "add" in captured.out
        assert "remove" in captured.out
        assert "mirror" in captured.out
        assert "sync" in captured.out

    def test_add_with_no_input_errors(self, capsys):
        code = _run_cli(["build-watchlist", "add"])
        assert code != 0
        captured = capsys.readouterr()
        assert "provide --type_id" in captured.out

    def test_add_mutually_exclusive_inputs_error(self, capsys):
        code = _run_cli(
            ["build-watchlist", "add", "--type_id=34", "--file=foo.csv"]
        )
        assert code != 0
        captured = capsys.readouterr()
        assert "mutually exclusive" in captured.out

    def test_mirror_with_extra_flags_errors(self, capsys):
        code = _run_cli(["build-watchlist", "mirror", "--type_id=34"])
        assert code != 0
        captured = capsys.readouterr()
        assert "no item flags" in captured.out


class TestBuildWatchlistPush:
    """Verify build-watchlist add|remove pushes the local write up to Turso."""

    @patch("mkts_backend.cli_tools.build_watchlist_cli.add_to_build_watchlist")
    @patch("mkts_backend.cli_tools.build_watchlist_cli.DatabaseConfig")
    def test_add_pushes_when_rows_written(self, mock_db, mock_add, fake_db_factory, tmp_path):
        from mkts_backend.builder_costs.watchlist_sync import AddResult

        db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
        mock_db.return_value = db
        mock_add.return_value = AddResult(added=2)
        _run_cli(["build-watchlist", "add", "--type_id=34,35"])

        assert db.pushes == 1

    @patch("mkts_backend.cli_tools.build_watchlist_cli.add_to_build_watchlist")
    @patch("mkts_backend.cli_tools.build_watchlist_cli.DatabaseConfig")
    def test_add_skips_push_when_nothing_added(self, mock_db, mock_add, fake_db_factory, tmp_path):
        from mkts_backend.builder_costs.watchlist_sync import AddResult

        db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
        mock_db.return_value = db
        mock_add.return_value = AddResult(added=0, skipped=[36])
        _run_cli(["build-watchlist", "add", "--type_id=36"])

        assert db.pushes == 0

    @patch("mkts_backend.cli_tools.build_watchlist_cli.add_to_build_watchlist")
    @patch("mkts_backend.cli_tools.build_watchlist_cli.DatabaseConfig")
    def test_no_sync_flag_skips_push(self, mock_db, mock_add, fake_db_factory, tmp_path):
        from mkts_backend.builder_costs.watchlist_sync import AddResult

        db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
        mock_db.return_value = db
        mock_add.return_value = AddResult(added=2)
        _run_cli(["build-watchlist", "add", "--type_id=34,35", "--no-sync"])

        assert db.pushes == 0

    @patch("mkts_backend.cli_tools.build_watchlist_cli.remove_from_build_watchlist")
    @patch("mkts_backend.cli_tools.build_watchlist_cli.DatabaseConfig")
    def test_remove_pushes_when_rows_removed(self, mock_db, mock_remove, fake_db_factory, tmp_path):
        from mkts_backend.builder_costs.watchlist_sync import RemoveResult

        db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
        mock_db.return_value = db
        mock_remove.return_value = RemoveResult(removed=1)
        _run_cli(["build-watchlist", "remove", "--type_id=34"])

        assert db.pushes == 1

