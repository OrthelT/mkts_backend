"""Tests for the ``fit-manager`` command surface: help, option validation,
dispatch, and the behavior it merged from the removed ``fit-update``,
``update-fit``, and top-level ``update-target`` commands.

Database work is mocked at the ``fit_update`` / ``update_fit_workflow``
boundary; the writers themselves are covered by ``test_management_push.py``.
"""
import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from mkts_backend.cli_tools import fit_manager, fit_update
from mkts_backend.cli_tools.command_registry import (
    REMOVED_COMMANDS,
    get_registry,
    removed_command_message,
)
from mkts_backend.cli_tools.fit_manager import (
    PROG,
    SUBCOMMANDS,
    format_overview_help,
    format_subcommand_help,
    handle_fit_manager,
)
from mkts_backend.cli_tools.market_args import MARKET_DB_MAP

PRIMARY_DB = MARKET_DB_MAP["primary"]
DEPLOYMENT_DB = MARKET_DB_MAP["deployment"]
ALL_DBS = list(dict.fromkeys(MARKET_DB_MAP.values()))


@pytest.fixture
def workflow():
    """Stub the fit write: record each workflow call, capture UoW pushes."""
    from unittest.mock import MagicMock

    push_spy = MagicMock(return_value=True)

    class _FakeDb:
        def __init__(self, alias):
            self.alias = alias

    class _StubUoW:
        def __init__(self):
            self.touched: set[str] = set()
            self.failed: set[str] = set()
            self.failed_pushes: set[str] = set()

        def mark_touched(self, alias: str) -> str:
            self.touched.add(alias)
            return alias

        @property
        def ok(self) -> bool:
            return not self.failed and not self.failed_pushes

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                for alias in sorted(self.touched):
                    if not push_spy(alias):
                        self.failed_pushes.add(alias)
            return False

    def fake_workflow(**kwargs):
        uow = kwargs.get("uow")
        if uow is not None and not kwargs.get("dry_run"):
            uow.mark_touched("fittings")
            for alias in kwargs.get("target_aliases") or [kwargs.get("target_alias")]:
                uow.mark_touched(alias)
        return {"ship_name": "Rifter", "ship_type_id": 587, "items": [], "missing_items": []}

    with patch.object(fit_manager, "update_fit_workflow", side_effect=fake_workflow) as spy, \
         patch.object(fit_manager, "ReplicaUnitOfWork", side_effect=_StubUoW):
        spy.push = push_spy
        yield spy


def _meta_file(tmp_path, fit_id=313):
    path = tmp_path / "meta.json"
    path.write_text(json.dumps({
        "fit_id": fit_id, "name": "HFI", "description": "d",
        "doctrine_id": [42, 43], "target": 300,
    }))
    return str(path)


class TestHelp:
    @pytest.mark.parametrize("name", list(SUBCOMMANDS))
    def test_subcommand_help_documents_usage_options_and_examples(self, name, capsys):
        spec = SUBCOMMANDS[name]
        assert handle_fit_manager([name, "--help"], "primary") is True
        out = capsys.readouterr().out

        for section in ("Usage:", "Options:", "Market scope:", "Examples:"):
            assert section in out
        assert spec.summary in out
        for flag, _ in spec.options:
            assert flag in out
        assert spec.examples, f"{name} has no example"
        for _, command in spec.examples:
            assert command.startswith(name)
        # CI and the default terminal are 80 columns wide.
        assert max(len(line) for line in out.splitlines()) <= 80

    @pytest.mark.parametrize("name", list(SUBCOMMANDS))
    def test_examples_use_only_accepted_options(self, name):
        """An example that the parser would reject is a documentation bug."""
        spec = SUBCOMMANDS[name]
        for _, command in spec.examples:
            assert fit_manager._rejected_option(spec, command.split()) is None

    def test_help_does_not_run_the_subcommand(self):
        with patch.object(fit_update, "create_doctrine_command") as spy:
            handle_fit_manager(["create-doctrine", "--help"], "primary")
        spy.assert_not_called()

    def test_overview_lists_every_subcommand(self, capsys):
        assert handle_fit_manager(["--help"], "primary") is True
        out = capsys.readouterr().out
        for name, spec in SUBCOMMANDS.items():
            assert f"  {name} " in out
            assert spec.summary in out
        assert max(len(line) for line in out.splitlines()) <= 80

    def test_missing_subcommand_fails_and_shows_overview(self, capsys):
        assert handle_fit_manager([], "primary") is False
        out = capsys.readouterr().out
        assert format_overview_help() in out
        assert "requires a subcommand" in out

    def test_market_option_documented_only_where_accepted(self):
        for spec in SUBCOMMANDS.values():
            assert ("--market=<name>" in format_subcommand_help(spec)) == bool(spec.markets)


class TestValidation:
    def test_unknown_subcommand_suggests_the_closest(self, capsys):
        assert handle_fit_manager(["updat", "--fit-id=1"], "primary") is False
        assert f"{PROG} update" in capsys.readouterr().out

    @pytest.mark.parametrize("flag", ["--remote", "--local-only", "--update-targets"])
    def test_inert_flags_are_rejected(self, flag, workflow, capsys):
        result = handle_fit_manager(
            ["update", "--fit-id=313", "--file=fit.txt", flag], "primary"
        )
        assert result is False
        workflow.assert_not_called()
        assert f"does not accept {flag}" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "name", [n for n, s in SUBCOMMANDS.items() if not s.markets]
    )
    def test_market_option_rejected_where_it_would_be_ignored(self, name, capsys):
        spec = SUBCOMMANDS[name]
        with patch.object(fit_manager, "_resolve_invocation") as resolve:
            assert handle_fit_manager([name, "--market=deployment"], "deployment") is False
        resolve.assert_not_called()
        out = " ".join(capsys.readouterr().out.split())
        assert spec.scope in out

    def test_doctrine_add_fit_rejects_doctrine_id(self, capsys):
        """The handler always prompts for the doctrine, so the option is not accepted."""
        with patch.object(fit_update, "doctrine_add_fit_command") as spy:
            assert handle_fit_manager(
                ["doctrine-add-fit", "--doctrine-id=21", "--market=primary"], "primary"
            ) is False
        spy.assert_not_called()

    def test_bad_integer_reports_the_option(self, capsys):
        assert handle_fit_manager(
            ["remove", "--fit-id=abc", "--market=primary"], "primary"
        ) is False
        assert "--fit-id must be an integer" in capsys.readouterr().out

    def test_assign_market_needs_exactly_one_target(self):
        with patch.object(fit_update, "assign_market_command") as fit, \
             patch.object(fit_update, "assign_doctrine_market") as doctrine:
            for extra in ([], ["--fit-id=1", "--doctrine-id=2"]):
                assert handle_fit_manager(
                    ["assign-market", "--market=primary", *extra], "primary"
                ) is False
        fit.assert_not_called()
        doctrine.assert_not_called()

    def test_market_less_subcommand_never_prompts_for_a_market(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch.object(fit_manager, "resolve_market_alias_interactive") as prompt, \
             patch.object(fit_update, "update_friendly_name_command", return_value=True) as spy:
            assert handle_fit_manager(
                ["update-friendly-name", "--doctrine-id=21", "--name=Hurricane"],
                "primary",
            ) is True
        prompt.assert_not_called()
        spy.assert_called_once_with(21, "Hurricane", db_alias=PRIMARY_DB)


class TestRemovedCommands:
    @pytest.mark.parametrize("name", list(REMOVED_COMMANDS))
    def test_removed_command_is_not_registered(self, name):
        assert get_registry().resolve(name) is None

    @pytest.mark.parametrize("name", list(REMOVED_COMMANDS))
    def test_removed_command_points_to_fit_manager(self, name, capsys):
        from mkts_backend.cli_tools.args_parser import parse_args

        # `list-fits` is itself a registered command; the removed name must
        # win, or `fit-update list-fits` would silently run something else.
        with pytest.raises(SystemExit) as exc:
            parse_args([name, "list-fits", "--market=primary"])
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert f"'{name}' was removed" in out
        assert "fit-manager" in out

    def test_current_command_has_no_removal_notice(self):
        assert removed_command_message("fit-manager") is None


class TestAdd:
    def test_meta_file_add_writes_the_selected_market(self, tmp_path, workflow):
        assert handle_fit_manager(
            ["add", "--file=fit.txt", f"--meta-file={_meta_file(tmp_path)}",
             "--market=deployment"],
            "deployment",
        ) is True
        assert workflow.call_args.kwargs["target_aliases"] == [DEPLOYMENT_DB]
        assert workflow.call_args.kwargs["fit_id"] == 313
        assert workflow.call_args.kwargs["clear_existing"] is True

    def test_add_requires_exactly_one_metadata_source(self, tmp_path, workflow):
        meta = _meta_file(tmp_path)
        with patch.object(fit_update, "interactive_add_fit") as interactive:
            for extra in ([], ["--interactive", f"--meta-file={meta}"]):
                assert handle_fit_manager(
                    ["add", "--file=fit.txt", "--market=primary", *extra], "primary"
                ) is False
        interactive.assert_not_called()
        workflow.assert_not_called()

    def test_add_requires_a_fit_source(self, tmp_path, workflow):
        assert handle_fit_manager(
            ["add", f"--meta-file={_meta_file(tmp_path)}", "--market=primary"],
            "primary",
        ) is False
        workflow.assert_not_called()

    def test_dry_run_does_not_push(self, tmp_path, workflow):
        assert handle_fit_manager(
            ["add", "--file=fit.txt", f"--meta-file={_meta_file(tmp_path)}",
             "--market=primary", "--dry-run"],
            "primary",
        ) is True
        assert workflow.call_args.kwargs["dry_run"] is True
        workflow.push.assert_not_called()


class TestUpdate:
    """``update`` merged ``fit-update update`` and the removed ``update-fit``."""

    TRACKED = {"fit_id": 313, "name": "HFI", "description": "HFI doctrine fit",
               "doctrine_id": [42], "target": 120}

    def _tracked_in(self, db_aliases):
        return patch.object(
            fit_update, "find_tracked_fit", return_value=(db_aliases, self.TRACKED)
        )

    def test_updates_every_market_that_tracks_the_fit(self, workflow):
        with self._tracked_in([PRIMARY_DB, DEPLOYMENT_DB]):
            assert handle_fit_manager(
                ["update", "--fit-id=313", "--file=fit.txt"], "primary"
            ) is True
        assert workflow.call_args.kwargs["target_aliases"] == [
            PRIMARY_DB, DEPLOYMENT_DB,
        ]
        # No --meta-file: keep the metadata the market already stores.
        assert workflow.call_args.kwargs["metadata_override"] == self.TRACKED
        assert workflow.call_args.kwargs["clear_existing"] is True
        assert {c.args[0] for c in workflow.push.call_args_list} == {
            "fittings", PRIMARY_DB, DEPLOYMENT_DB,
        }

    def test_untracked_fit_is_an_error_not_an_add(self, workflow, capsys):
        with patch.object(fit_update, "find_tracked_fit", return_value=([], None)):
            assert handle_fit_manager(
                ["update", "--fit-id=313", "--file=fit.txt"], "primary"
            ) is False
        workflow.assert_not_called()
        assert "fit-manager add" in capsys.readouterr().out

    def test_no_clear_keeps_existing_items(self, workflow):
        with self._tracked_in([PRIMARY_DB]):
            handle_fit_manager(
                ["update", "--fit-id=313", "--file=fit.txt", "--no-clear"], "primary"
            )
        assert workflow.call_args.kwargs["clear_existing"] is False

    def test_meta_file_supplies_the_fit_id_and_metadata(self, tmp_path, workflow):
        meta = _meta_file(tmp_path, fit_id=313)
        with self._tracked_in([PRIMARY_DB]) as tracked:
            assert handle_fit_manager(
                ["update", "--file=fit.txt", f"--meta-file={meta}"], "primary"
            ) is True
        tracked.assert_called_once_with(313)
        assert workflow.call_args.kwargs["fit_metadata_file"] == meta
        assert workflow.call_args.kwargs["metadata_override"] is None

    def test_fit_id_overrides_the_meta_file_fit_id(self, tmp_path, workflow, capsys):
        meta = _meta_file(tmp_path, fit_id=999)
        with self._tracked_in([PRIMARY_DB]):
            assert handle_fit_manager(
                ["update", "--fit-id=313", "--file=fit.txt", f"--meta-file={meta}"],
                "primary",
            ) is True
        override = workflow.call_args.kwargs["metadata_override"]
        assert override["fit_id"] == 313
        assert override["doctrine_id"] == [42, 43]
        assert override["target"] == 300
        assert "overrides fit_id=999" in capsys.readouterr().out

    def test_requires_a_fit_id_or_meta_file(self, workflow):
        assert handle_fit_manager(["update", "--file=fit.txt"], "primary") is False
        workflow.assert_not_called()

    def test_push_failure_fails_the_command(self, workflow):
        workflow.push.return_value = False
        with self._tracked_in([PRIMARY_DB]):
            assert handle_fit_manager(
                ["update", "--fit-id=313", "--file=fit.txt"], "primary"
            ) is False


class TestRemoveScope:
    """``remove --market=primary`` used to delete the fit from every market."""

    def _run(self, market, tracked):
        with patch.object(fit_update, "find_tracked_fit", return_value=(tracked, {})), \
             patch.object(fit_update, "remove_fit_command", return_value=True) as spy:
            assert handle_fit_manager(
                ["remove", "--fit-id=313", f"--market={market}"], market
            ) is True
        return spy

    def test_primary_removes_from_primary_only(self):
        spy = self._run("primary", tracked=ALL_DBS)
        spy.assert_called_once_with(
            fit_id=313, db_alias=PRIMARY_DB, keep_doctrine_links=True
        )

    def test_all_removes_from_every_market_and_drops_the_links(self):
        spy = self._run("all", tracked=ALL_DBS)
        assert [c.kwargs["db_alias"] for c in spy.call_args_list] == ALL_DBS
        assert not any(c.kwargs["keep_doctrine_links"] for c in spy.call_args_list)

    def test_last_tracking_market_drops_the_links(self):
        spy = self._run("deployment", tracked=[DEPLOYMENT_DB])
        spy.assert_called_once_with(
            fit_id=313, db_alias=DEPLOYMENT_DB, keep_doctrine_links=False
        )

    def test_one_failed_market_fails_the_command(self):
        with patch.object(fit_update, "find_tracked_fit", return_value=(ALL_DBS, {})), \
             patch.object(fit_update, "remove_fit_command",
                          side_effect=[False] + [True] * (len(ALL_DBS) - 1)) as spy:
            assert handle_fit_manager(
                ["remove", "--fit-id=313", "--market=all"], "all"
            ) is False
        assert spy.call_count == len(ALL_DBS)


class TestFindTrackedFit:
    def test_reports_tracking_markets_and_stored_metadata(self, tmp_path):
        engines = {}

        def fake_db(alias):
            if alias not in engines:
                engine = create_engine(f"sqlite:///{tmp_path / alias}.db")
                with engine.begin() as conn:
                    conn.execute(text(
                        "CREATE TABLE doctrine_fits (doctrine_id INTEGER, fit_id INTEGER, "
                        "market_flag TEXT, doctrine_name TEXT, fit_name TEXT, "
                        "ship_name TEXT, ship_type_id INTEGER, target INTEGER)"
                    ))
                    if alias != PRIMARY_DB:
                        conn.execute(text(
                            "INSERT INTO doctrine_fits (doctrine_id, fit_id, fit_name, target) "
                            "VALUES (43, 313, 'HFI', 120), (42, 313, 'HFI', 120)"
                        ))
                engines[alias] = engine

            class FakeDb:
                def __init__(self, a):
                    self.alias = a
                    self.engine = engines[a]

            return FakeDb(alias)

        with patch.object(fit_update, "DatabaseConfig", fake_db):
            db_aliases, metadata = fit_update.find_tracked_fit(313)
            assert fit_update.find_tracked_fit(999) == ([], None)

        assert db_aliases == [a for a in ALL_DBS if a != PRIMARY_DB]
        assert metadata == {
            "fit_id": 313, "name": "HFI", "description": "HFI doctrine fit",
            "doctrine_id": [42, 43], "target": 120,
        }
