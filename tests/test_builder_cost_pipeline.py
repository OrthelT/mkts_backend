from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd


def _make_db_mock(watchlist_df: pd.DataFrame | None = None) -> MagicMock:
    db = MagicMock()
    engine = MagicMock()
    conn = MagicMock()
    context_manager = MagicMock()
    context_manager.__enter__.return_value = conn
    context_manager.__exit__.return_value = False
    engine.connect.return_value = context_manager
    db.engine = engine
    db.verify_db_exists.return_value = True
    db.get_watchlist.return_value = watchlist_df if watchlist_df is not None else pd.DataFrame()
    return db


class TestProcessBuilderCosts:
    @patch("pandas.read_sql_query")
    @patch("mkts_backend.esi.async_everref.run_async_fetch_builder_costs")
    @patch("mkts_backend.cli.DatabaseConfig")
    def test_returns_false_when_any_market_prep_fails(
        self,
        mock_db_config,
        mock_fetch,
        mock_read_sql_query,
        primary_market_context,
        deployment_market_context,
    ):
        from mkts_backend.cli import process_builder_costs

        prep_primary = _make_db_mock()
        prep_deployment = _make_db_mock()
        prep_deployment.sync.side_effect = RuntimeError("sync failed")
        watch_primary = _make_db_mock(
            pd.DataFrame(
                [
                    {
                        "type_id": 100,
                        "type_name": "Large Shield Extender I",
                        "group_name": "Shield Extender",
                        "category_id": 7,
                    }
                ]
            )
        )
        jita_primary = _make_db_mock()
        sde_db = _make_db_mock()

        mock_db_config.side_effect = [
            prep_primary,
            prep_deployment,
            watch_primary,
            jita_primary,
            sde_db,
        ]
        mock_fetch.return_value = [
            {
                "type_id": 100,
                "total_cost_per_unit": 123.4,
                "time_per_unit": 60.0,
                "me": 4,
                "runs": 5,
                "fetched_at": datetime(2026, 4, 17, tzinfo=timezone.utc),
            }
        ]
        mock_read_sql_query.return_value = pd.DataFrame(
            [{"type_id": 100, "sell_price": 50_000_000.0}]
        )

        result = process_builder_costs(
            market_contexts=[primary_market_context, deployment_market_context]
        )

        assert result is False
        mock_fetch.assert_not_called()

    @patch("mkts_backend.cli.log_update")
    @patch("mkts_backend.cli.upsert_database", return_value=True)
    @patch("mkts_backend.cli._ensure_builder_costs_table")
    @patch("mkts_backend.esi.async_everref.run_async_fetch_builder_costs")
    @patch("pandas.read_sql_query")
    @patch("mkts_backend.cli.DatabaseConfig")
    def test_syncs_market_dbs_and_uses_first_nonempty_jita_table(
        self,
        mock_db_config,
        mock_read_sql_query,
        mock_fetch,
        mock_ensure_table,
        mock_upsert,
        mock_log_update,
        primary_market_context,
        deployment_market_context,
    ):
        from mkts_backend.cli import process_builder_costs

        watchlist_df = pd.DataFrame(
            [
                {
                    "type_id": 100,
                    "type_name": "Large Shield Extender I",
                    "group_name": "Shield Extender",
                    "category_id": 7,
                }
            ]
        )

        prep_primary = _make_db_mock()
        prep_deployment = _make_db_mock()
        watch_primary = _make_db_mock(watchlist_df)
        watch_deployment = _make_db_mock(pd.DataFrame())
        jita_primary = _make_db_mock()
        jita_deployment = _make_db_mock()
        sde_db = _make_db_mock()

        mock_db_config.side_effect = [
            prep_primary,
            prep_deployment,
            watch_primary,
            watch_deployment,
            jita_primary,
            jita_deployment,
            sde_db,
        ]
        mock_read_sql_query.side_effect = [
            pd.DataFrame(columns=["type_id", "sell_price"]),
            pd.DataFrame([{"type_id": 100, "sell_price": 50_000_000.0}]),
        ]
        mock_fetch.return_value = [
            {
                "type_id": 100,
                "total_cost_per_unit": 123.4,
                "time_per_unit": 60.0,
                "me": 4,
                "runs": 5,
                "fetched_at": datetime(2026, 4, 17, tzinfo=timezone.utc),
            }
        ]

        result = process_builder_costs(
            market_contexts=[primary_market_context, deployment_market_context]
        )

        assert result is True
        prep_primary.sync.assert_called_once_with()
        prep_deployment.sync.assert_called_once_with()
        assert mock_read_sql_query.call_count == 2
        fetch_args, fetch_kwargs = mock_fetch.call_args
        assert fetch_args[1] == {100: 50_000_000.0}
        assert fetch_kwargs["watchlist_metadata"] == {
            100: {
                "type_id": 100,
                "type_name": "Large Shield Extender I",
                "group_name": "Shield Extender",
                "category_id": 7,
            }
        }
        assert mock_ensure_table.call_count == 2
        assert mock_upsert.call_count == 2
        assert mock_log_update.call_count == 2

    @patch("mkts_backend.cli.log_update")
    @patch("mkts_backend.cli._ensure_builder_costs_table")
    @patch("mkts_backend.esi.async_everref.run_async_fetch_builder_costs")
    @patch("pandas.read_sql_query")
    @patch("mkts_backend.cli.DatabaseConfig")
    @patch("mkts_backend.cli.upsert_database")
    def test_returns_false_if_any_market_write_fails(
        self,
        mock_upsert,
        mock_db_config,
        mock_read_sql_query,
        mock_fetch,
        mock_ensure_table,
        mock_log_update,
        primary_market_context,
        deployment_market_context,
    ):
        from mkts_backend.cli import process_builder_costs

        watchlist_df = pd.DataFrame(
            [
                {
                    "type_id": 100,
                    "type_name": "Large Shield Extender I",
                    "group_name": "Shield Extender",
                    "category_id": 7,
                }
            ]
        )

        prep_primary = _make_db_mock()
        prep_deployment = _make_db_mock()
        watch_primary = _make_db_mock(watchlist_df)
        watch_deployment = _make_db_mock(pd.DataFrame())
        jita_primary = _make_db_mock()
        sde_db = _make_db_mock()

        mock_db_config.side_effect = [
            prep_primary,
            prep_deployment,
            watch_primary,
            watch_deployment,
            jita_primary,
            sde_db,
        ]
        mock_read_sql_query.return_value = pd.DataFrame(
            [{"type_id": 100, "sell_price": 50_000_000.0}]
        )
        mock_fetch.return_value = [
            {
                "type_id": 100,
                "total_cost_per_unit": 123.4,
                "time_per_unit": 60.0,
                "me": 4,
                "runs": 5,
                "fetched_at": datetime(2026, 4, 17, tzinfo=timezone.utc),
            }
        ]
        mock_upsert.side_effect = [True, False]

        result = process_builder_costs(
            market_contexts=[primary_market_context, deployment_market_context]
        )

        assert result is False
        assert mock_upsert.call_count == 2
        assert mock_log_update.call_count == 1