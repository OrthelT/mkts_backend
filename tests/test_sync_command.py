"""`sync` must cover every replica the deleted refresh scripts covered.

It previously pulled the three markets plus buildcost and silently skipped
the shared sde and fittings replicas, so a refresh left those two stale.
"""
import pytest

from mkts_backend.cli_tools.command_registry import get_registry
from mkts_backend.config.settings_service import (
    SettingsService,
    get_all_market_contexts,
)


@pytest.fixture
def routed_aliases():
    return set(SettingsService().database_routing())


@pytest.fixture
def market_aliases():
    return {ctx.database_alias for ctx in get_all_market_contexts().values()}


@pytest.fixture
def testing_alias():
    return SettingsService().shared_testing["database_alias"]


@pytest.fixture
def stub_credentials(monkeypatch):
    """Stub in dummy Turso credentials for every routed alias.

    The sync handler checks ``os.getenv(...)`` for each route's credentials
    before ever touching ``DatabaseConfig`` — so a test that mocks
    ``DatabaseConfig`` still silently depends on whatever the ambient
    environment happens to provide (a developer's real ``.env``) unless it
    also stubs the env vars itself. Without this, these tests only pass
    because real credentials happen to be sitting in the environment; in a
    clean CI runner with no secrets, every route looks credential-less and
    the tests that expect full coverage fail for the wrong reason.
    """
    routing = SettingsService().database_routing()
    for cfg in routing.values():
        if cfg["turso_url_env"]:
            monkeypatch.setenv(cfg["turso_url_env"], "dummy-url")
        if cfg["turso_token_env"]:
            monkeypatch.setenv(cfg["turso_token_env"], "dummy-token")


@pytest.fixture
def synced(monkeypatch, stub_credentials):
    """Record every alias a DatabaseConfig was built for and pulled."""
    seen = []

    class Recorder:
        def __init__(self, alias=None, market_context=None):
            self.alias = alias or market_context.database_alias
            self.path = f"{self.alias}.db"

        def verify_db_exists(self):
            return True

        def sync(self):
            seen.append(self.alias)

    monkeypatch.setattr(
        "mkts_backend.config.db_config.DatabaseConfig", Recorder
    )
    return seen


def _run(args, market_alias="all"):
    handler = get_registry().resolve("sync").handler
    return handler(args, market_alias)


def test_sync_all_covers_every_routed_replica(synced, routed_aliases, testing_alias):
    _run([])
    assert set(synced) >= routed_aliases - {testing_alias}


def test_sync_excludes_testing_unless_explicit(synced, testing_alias):
    _run([])
    assert testing_alias not in synced
    _run(["--include-testing"])
    assert testing_alias in synced


def test_sync_covers_sde_and_fittings(synced):
    _run([])
    assert "sdelitetest" in synced or "sde" in " ".join(synced)
    assert any("fitting" in a for a in synced)


def test_single_market_skips_other_markets(synced, market_aliases):
    _run([], market_alias="primary")
    from mkts_backend.config.market_context import MarketContext
    primary = MarketContext.from_settings("primary").database_alias
    assert primary in synced
    assert not (market_aliases - {primary}) & set(synced)


def test_no_buildcost_flag_skips_buildcost(synced):
    _run(["--no-buildcost"])
    assert not any("buildcost" in a for a in synced)


def test_markets_only_flag_skips_shared(synced, market_aliases):
    _run(["--markets-only"])
    assert set(synced) == market_aliases


def test_verify_failure_aborts_that_replica(monkeypatch, stub_credentials, capsys):
    instances = []

    class Broken:
        def __init__(self, alias=None, market_context=None):
            self.alias = alias or market_context.database_alias
            self.path = f"{self.alias}.db"
            self.pulled = False
            instances.append(self)

        def verify_db_exists(self):
            return False

        def sync(self):
            self.pulled = True
            raise AssertionError("sync must not run after verify_db_exists fails")

    monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", Broken)
    assert _run([], market_alias="primary") is False
    assert instances, "DatabaseConfig was never constructed for the primary market"
    assert all(inst.pulled is False for inst in instances), (
        "sync() ran after verify_db_exists() returned False"
    )


@pytest.mark.parametrize("missing_var", ["turso_url_env", "turso_token_env"])
@pytest.mark.parametrize("route_kind", ["market", "shared"])
def test_missing_required_url_or_token_fails(monkeypatch, synced, missing_var, route_kind):
    """Required routes must not silently become local-only databases."""
    routing = SettingsService().database_routing()

    if route_kind == "market":
        from mkts_backend.config.market_context import MarketContext
        primary = MarketContext.from_settings("primary")
        alias = primary.database_alias
        market_alias = "primary"
    else:
        # Pick a required (non-optional) shared route that isn't the market DBs.
        market_aliases = {
            ctx.database_alias for ctx in get_all_market_contexts().values()
        }
        alias = next(
            a
            for a, cfg in routing.items()
            if a not in market_aliases and not cfg["optional"] and a != "wcmkttest"
        )
        market_alias = "all"

    env_var = routing[alias][missing_var]
    monkeypatch.delenv(env_var, raising=False)

    result = _run([], market_alias=market_alias)

    assert result is False
    assert alias not in synced


def test_optional_route_with_no_credentials_warns_and_skips(monkeypatch, synced, capsys):
    """An optional route missing credentials should warn and be skipped, not fail."""
    routing = SettingsService().database_routing()
    market_aliases = {
        ctx.database_alias for ctx in get_all_market_contexts().values()
    }
    alias = next(
        a for a, cfg in routing.items() if a not in market_aliases and cfg["optional"]
    )
    cfg = routing[alias]
    monkeypatch.delenv(cfg["turso_url_env"], raising=False)
    monkeypatch.delenv(cfg["turso_token_env"], raising=False)

    result = _run([])

    assert result is True
    assert alias not in synced
    captured = capsys.readouterr()
    assert "warning" in captured.out.lower()
    assert alias in captured.out


def test_missing_db_with_leftover_sidecars_is_rebuilt(
    monkeypatch, stub_credentials, tmp_path
):
    """A deleted .db beside a valid -info must be re-pulled, not fail.

    pyturso refuses to open that state ("main DB file doesn't exists, but
    metadata is"), and heal_metadata() alone accepts it because the -info
    is well-formed. sync must nuke the leftover sidecars and pull fresh.
    """
    import json
    from pathlib import Path

    from mkts_backend.config import db_config

    remote = "https://test-db.turso.io"

    class TmpConfig(db_config.DatabaseConfig):
        def __init__(self, alias=None, market_context=None):
            self.alias = alias or market_context.database_alias
            self.path = str(tmp_path / f"{self.alias}.db")
            self.turso_url = remote
            self.token = "dummy-token"
            self._engine = None

        def pull(self):
            # Mirror pyturso: metadata without its database is fatal.
            db, info = Path(self.path), Path(f"{self.path}-info")
            if info.exists() and not db.exists():
                raise RuntimeError("main DB file doesn't exists, but metadata is")
            db.write_bytes(b"x")
            info.write_text(json.dumps({
                "version": "v1",
                "client_unique_id": "turso-sync-py-test",
                "saved_configuration": {"remote_url": remote},
            }))

    monkeypatch.setattr(db_config, "DatabaseConfig", TmpConfig)

    from mkts_backend.config.market_context import MarketContext
    alias = MarketContext.from_settings("primary").database_alias
    leftover = TmpConfig(alias)
    leftover.pull()
    Path(leftover.path).unlink()
    Path(f"{leftover.path}-changes").write_bytes(b"")

    assert _run(["--markets-only"], market_alias="primary") is True
    assert Path(leftover.path).exists()
