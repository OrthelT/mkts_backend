"""Tests for the 1-hour Jita price cache.

Covers the shared freshness helper (``get_update_age``), the cached table read
(``read_jita_prices``), the pipeline TTL guard in ``process_jita_prices``, the
fitcheck read-through helper, and the ``fetch_jita_prices`` wrapper that now
shares one fetcher with ``fetch_jita_price_data``.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import text

from mkts_backend.db.db_queries import get_update_age, read_jita_prices


def _make_updatelog_db(fake_db_factory, tmp_path, rows=(), create=True):
    """Build a FakeDatabaseConfig whose updatelog holds ``rows``.

    ``rows`` is an iterable of ``(table_name, timestamp)`` where timestamp is a
    naive UTC datetime — the shape SQLAlchemy's SQLite DateTime writes and
    reads back, and the shape ``log_update`` produces once the tz-aware value
    it binds has round-tripped through the column.
    """
    db = fake_db_factory(tmp_path / "market.db", alias="test-market")
    with db.engine.begin() as conn:
        if create:
            conn.execute(
                text(
                    "CREATE TABLE updatelog ("
                    "table_name TEXT PRIMARY KEY, timestamp DATETIME NOT NULL)"
                )
            )
        for name, ts in rows:
            conn.execute(
                text(
                    "INSERT INTO updatelog (table_name, timestamp) "
                    "VALUES (:name, :ts)"
                ),
                {"name": name, "ts": ts.strftime("%Y-%m-%d %H:%M:%S.%f")},
            )
    return db


class TestGetUpdateAge:
    def test_fresh_row_returns_small_age(self, fake_db_factory, tmp_path):
        written = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
        db = _make_updatelog_db(fake_db_factory, tmp_path, [("jita_prices", written)])

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            age = get_update_age("jita_prices")

        assert age is not None
        assert timedelta(minutes=4) < age < timedelta(minutes=6)

    def test_stale_row_returns_age_over_an_hour(self, fake_db_factory, tmp_path):
        written = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
        db = _make_updatelog_db(fake_db_factory, tmp_path, [("jita_prices", written)])

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            age = get_update_age("jita_prices")

        assert age is not None
        assert age > timedelta(hours=1)

    def test_no_row_returns_none(self, fake_db_factory, tmp_path):
        other = datetime.now(timezone.utc).replace(tzinfo=None)
        db = _make_updatelog_db(fake_db_factory, tmp_path, [("marketstats", other)])

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            assert get_update_age("jita_prices") is None

    def test_missing_table_returns_none(self, fake_db_factory, tmp_path):
        db = _make_updatelog_db(fake_db_factory, tmp_path, create=False)

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            assert get_update_age("jita_prices") is None

    def test_naive_timestamp_is_read_as_utc_not_local(self, fake_db_factory, tmp_path):
        """A naive stored timestamp must be treated as UTC, never subtracted raw.

        Without an explicit ``timezone.utc`` on the value read back, subtracting
        it from ``datetime.now(timezone.utc)`` raises TypeError; treating it as
        local time silently offsets the age by the machine's UTC offset.
        """
        written = datetime.now(timezone.utc).replace(tzinfo=None)
        db = _make_updatelog_db(fake_db_factory, tmp_path, [("jita_prices", written)])

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            age = get_update_age("jita_prices")

        assert age is not None
        assert abs(age) < timedelta(minutes=1)


def _make_jita_prices_db(fake_db_factory, tmp_path, rows=(), create=True):
    """Build a FakeDatabaseConfig whose jita_prices holds ``(type_id, sell_price)``."""
    db = fake_db_factory(tmp_path / "prices.db", alias="test-market")
    with db.engine.begin() as conn:
        if create:
            conn.execute(
                text(
                    "CREATE TABLE jita_prices ("
                    "type_id INTEGER PRIMARY KEY, sell_price REAL, buy_price REAL)"
                )
            )
        for type_id, sell_price in rows:
            conn.execute(
                text(
                    "INSERT INTO jita_prices (type_id, sell_price) "
                    "VALUES (:type_id, :sell_price)"
                ),
                {"type_id": type_id, "sell_price": sell_price},
            )
    return db


class TestReadJitaPrices:
    def test_reads_every_row_when_no_filter(self, fake_db_factory, tmp_path):
        db = _make_jita_prices_db(
            fake_db_factory, tmp_path, [(34, 5.5), (35, 10.0), (36, 42.0)]
        )

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            prices = read_jita_prices()

        assert prices == {34: 5.5, 35: 10.0, 36: 42.0}

    def test_filters_to_requested_type_ids(self, fake_db_factory, tmp_path):
        db = _make_jita_prices_db(
            fake_db_factory, tmp_path, [(34, 5.5), (35, 10.0), (36, 42.0)]
        )

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            prices = read_jita_prices(type_ids=[34, 36, 999])

        assert prices == {34: 5.5, 36: 42.0}

    def test_skips_null_sell_price(self, fake_db_factory, tmp_path):
        db = _make_jita_prices_db(fake_db_factory, tmp_path, [(34, 5.5), (35, None)])

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            prices = read_jita_prices()

        assert prices == {34: 5.5}

    def test_missing_table_returns_empty_dict(self, fake_db_factory, tmp_path):
        db = _make_jita_prices_db(fake_db_factory, tmp_path, create=False)

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            assert read_jita_prices() == {}

    def test_empty_type_ids_reads_nothing(self, fake_db_factory, tmp_path):
        db = _make_jita_prices_db(fake_db_factory, tmp_path, [(34, 5.5)])

        with patch("mkts_backend.db.db_queries._get_db", return_value=db):
            assert read_jita_prices(type_ids=[]) == {}


class TestProcessJitaPricesTTL:
    """The pipeline skips the fetch AND the per-market writes while fresh."""

    @staticmethod
    def _contexts():
        ctx = MagicMock()
        ctx.alias = "primary"
        return [ctx]

    def test_fresh_table_skips_fetch_and_writes(self):
        from mkts_backend import cli

        with (
            patch.object(cli, "get_update_age", return_value=timedelta(minutes=30)),
            patch("mkts_backend.utils.jita.fetch_jita_price_data") as mock_fetch,
            patch.object(cli, "upsert_database") as mock_upsert,
            patch("mkts_backend.db.db_queries.get_watchlist_ids") as mock_watchlist,
        ):
            assert cli.process_jita_prices(self._contexts()) is True

        mock_fetch.assert_not_called()
        mock_upsert.assert_not_called()
        mock_watchlist.assert_not_called()

    def test_stale_table_fetches_and_writes(self):
        from mkts_backend import cli

        with (
            patch.object(cli, "get_update_age", return_value=timedelta(hours=5)),
            patch(
                "mkts_backend.utils.jita.fetch_jita_price_data",
                return_value=[{"type_id": 34, "sell_price": 5.5, "buy_price": 4.0}],
            ) as mock_fetch,
            patch.object(cli, "upsert_database", return_value=True) as mock_upsert,
            patch.object(cli, "log_update"),
            patch.object(cli, "_ensure_jita_prices_table"),
            patch(
                "mkts_backend.db.db_queries.get_watchlist_ids", return_value=[34]
            ),
        ):
            assert cli.process_jita_prices(self._contexts()) is True

        mock_fetch.assert_called_once()
        mock_upsert.assert_called_once()

    def test_no_updatelog_row_fetches(self):
        from mkts_backend import cli

        with (
            patch.object(cli, "get_update_age", return_value=None),
            patch(
                "mkts_backend.utils.jita.fetch_jita_price_data",
                return_value=[{"type_id": 34, "sell_price": 5.5, "buy_price": 4.0}],
            ) as mock_fetch,
            patch.object(cli, "upsert_database", return_value=True),
            patch.object(cli, "log_update"),
            patch.object(cli, "_ensure_jita_prices_table"),
            patch("mkts_backend.db.db_queries.get_watchlist_ids", return_value=[34]),
        ):
            assert cli.process_jita_prices(self._contexts()) is True

        mock_fetch.assert_called_once()

    def test_no_market_contexts_returns_false(self):
        """The TTL guard reads market_contexts[0]; an empty list must not raise."""
        from mkts_backend import cli

        with patch("mkts_backend.utils.jita.fetch_jita_price_data") as mock_fetch:
            assert cli.process_jita_prices([]) is False

        mock_fetch.assert_not_called()

    def test_refresh_overrides_a_fresh_table(self):
        from mkts_backend import cli

        with (
            patch.object(cli, "get_update_age", return_value=timedelta(minutes=1)),
            patch(
                "mkts_backend.utils.jita.fetch_jita_price_data",
                return_value=[{"type_id": 34, "sell_price": 5.5, "buy_price": 4.0}],
            ) as mock_fetch,
            patch.object(cli, "upsert_database", return_value=True),
            patch.object(cli, "log_update"),
            patch.object(cli, "_ensure_jita_prices_table"),
            patch("mkts_backend.db.db_queries.get_watchlist_ids", return_value=[34]),
        ):
            assert cli.process_jita_prices(self._contexts(), refresh=True) is True

        mock_fetch.assert_called_once()


class TestFitCheckJitaPrices:
    """fitcheck reads the jita_prices table while fresh, and never writes it."""

    def test_fresh_table_is_read_without_any_fetch(self):
        from mkts_backend.cli_tools import fit_check

        with (
            patch.object(fit_check, "get_update_age", return_value=timedelta(minutes=10)),
            patch.object(
                fit_check, "read_jita_prices", return_value={34: 5.5, 35: 10.0}
            ) as mock_read,
            patch.object(fit_check, "fetch_jita_prices") as mock_fetch,
        ):
            prices = fit_check._get_jita_prices([34, 35], None)

        assert prices == {34: 5.5, 35: 10.0}
        mock_read.assert_called_once_with(None, [34, 35])
        mock_fetch.assert_not_called()

    def test_fresh_table_fetches_only_the_ids_it_lacks(self):
        from mkts_backend.cli_tools import fit_check

        with (
            patch.object(fit_check, "get_update_age", return_value=timedelta(minutes=10)),
            patch.object(fit_check, "read_jita_prices", return_value={34: 5.5}),
            patch.object(
                fit_check, "fetch_jita_prices", return_value={99: 1234.0}
            ) as mock_fetch,
        ):
            prices = fit_check._get_jita_prices([34, 99], None)

        assert prices == {34: 5.5, 99: 1234.0}
        mock_fetch.assert_called_once_with([99])

    def test_stale_table_fetches_every_id(self):
        from mkts_backend.cli_tools import fit_check

        with (
            patch.object(fit_check, "get_update_age", return_value=timedelta(hours=2)),
            patch.object(fit_check, "read_jita_prices") as mock_read,
            patch.object(
                fit_check, "fetch_jita_prices", return_value={34: 6.0, 99: 1.0}
            ) as mock_fetch,
        ):
            prices = fit_check._get_jita_prices([34, 99], None)

        assert prices == {34: 6.0, 99: 1.0}
        mock_read.assert_not_called()
        mock_fetch.assert_called_once_with([34, 99])

    def test_no_updatelog_row_fetches_every_id(self):
        from mkts_backend.cli_tools import fit_check

        with (
            patch.object(fit_check, "get_update_age", return_value=None),
            patch.object(fit_check, "read_jita_prices") as mock_read,
            patch.object(
                fit_check, "fetch_jita_prices", return_value={34: 6.0}
            ) as mock_fetch,
        ):
            assert fit_check._get_jita_prices([34], None) == {34: 6.0}

        mock_read.assert_not_called()
        mock_fetch.assert_called_once_with([34])

    def test_refresh_bypasses_a_fresh_table(self):
        from mkts_backend.cli_tools import fit_check

        with (
            patch.object(fit_check, "get_update_age", return_value=timedelta(minutes=1)),
            patch.object(fit_check, "read_jita_prices") as mock_read,
            patch.object(
                fit_check, "fetch_jita_prices", return_value={34: 6.0}
            ) as mock_fetch,
        ):
            assert fit_check._get_jita_prices([34], None, refresh=True) == {34: 6.0}

        mock_read.assert_not_called()
        mock_fetch.assert_called_once_with([34])


class TestFetchJitaPricesDelegates:
    """``fetch_jita_prices`` projects sell prices out of ``fetch_jita_price_data``.

    Sharing one fetcher gives fitcheck the Janice fallback and the 250-id
    batching that only the fuller function had.
    """

    def test_projects_sell_price_per_requested_id(self):
        from mkts_backend.utils import jita

        with patch.object(
            jita,
            "fetch_jita_price_data",
            return_value=[
                {"type_id": 34, "sell_price": 5.5, "buy_price": 4.0},
                {"type_id": 35, "sell_price": 10.0, "buy_price": 9.0},
            ],
        ) as mock_fetch:
            prices = jita.fetch_jita_prices([34, 35])

        assert prices == {34: 5.5, 35: 10.0}
        mock_fetch.assert_called_once_with([34, 35])

    def test_unreturned_ids_map_to_none(self):
        from mkts_backend.utils import jita

        with patch.object(
            jita,
            "fetch_jita_price_data",
            return_value=[{"type_id": 34, "sell_price": 5.5, "buy_price": 4.0}],
        ):
            prices = jita.fetch_jita_prices([34, 99])

        assert prices == {34: 5.5, 99: None}

    def test_non_positive_sell_price_maps_to_none(self):
        """An item priced only on the buy side has no usable Jita sell price."""
        from mkts_backend.utils import jita

        with patch.object(
            jita,
            "fetch_jita_price_data",
            return_value=[{"type_id": 34, "sell_price": 0.0, "buy_price": 4.0}],
        ):
            assert jita.fetch_jita_prices([34]) == {34: None}

    def test_empty_input_fetches_nothing(self):
        from mkts_backend.utils import jita

        with patch.object(jita, "fetch_jita_price_data") as mock_fetch:
            assert jita.fetch_jita_prices([]) == {}

        mock_fetch.assert_not_called()
