from mkts_backend.config.market_context import MarketContext
from mkts_backend.config.config import DatabaseConfig
from sqlalchemy import text

def check_tables(market_alias: str = "primary"):
    """Check tables in the database for the specified market."""
    market_ctx = MarketContext.from_settings(market_alias)
    db = DatabaseConfig(market_context=market_ctx)

    print(f"Checking tables for market: {market_ctx.name} ({market_ctx.alias})")
    print(f"Database: {db.alias} ({db.path})")
    print("=" * 80)

    tables = db.get_table_list()

    for table in tables:
        print(f"Table: {table}")
        print("=" * 80)
        with db.engine.connect() as conn:
            result = conn.execute(text(f"SELECT * FROM {table} LIMIT 10"))
            for row in result:
                print(row)
            print("\n")
        conn.close()
    db.engine.dispose()

if __name__ == "__main__":
    pass