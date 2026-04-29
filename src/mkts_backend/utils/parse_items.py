import csv
import re
from sqlalchemy import text
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.utils.utils import init_databases

logger = configure_logging(__name__)

def parse_items(input_file: str, output_file: str):
    """
    Parse Eve Online structure window data and create CSV with pricing from database.

    Expected input format (tab-separated):
    Item Name    Quantity    Group    Size    Slot    Volume    Total Value

    Args:
        input_file: Path to file containing Eve structure data
        output_file: Path for output CSV file
    """
    try:
        # Initialize database
        init_databases()
        db = DatabaseConfig("wcmkt")

        # Read input file
        with open(input_file, 'r') as f:
            lines = f.readlines()

        if not lines:
            print("Error: Input file is empty")
            return False

        # Parse structure data
        parsed_items = []
        for line in lines:
            # Remove extra whitespace and split by multiple spaces/tabs
            parts = re.split(r'\s{2,}|\t+', line.strip())

            if len(parts) < 2:
                logger.warning(f"Skipping malformed line (too few fields): {line.strip()}")
                continue

            # Extract item name (first element)
            item_name = parts[0].strip()

            # Extract quantity (second element)
            try:
                quantity = int(parts[1].strip().replace(',', ''))
            except ValueError:
                logger.warning(f"Invalid quantity for {item_name}: {parts[1]}")
                continue

            parsed_items.append({
                'item_name': item_name,
                'quantity': quantity,
                'raw_data': parts
            })

        logger.info(f"Parsed {len(parsed_items)} items from structure data")

        # Query database for pricing information
        results = []
        for item in parsed_items:
            # Query marketstats table for pricing
            with db.engine.connect() as conn:
                query = text("""
                    SELECT type_id, type_name, price, min_price, avg_price,
                           total_volume_remain, days_remaining, group_name, category_name
                    FROM marketstats
                    WHERE type_name = :item_name
                """)
                result = conn.execute(query, {"item_name": item['item_name']})
                row = result.fetchone()

                if row:
                    # Calculate total value based on market price
                    market_price = row[2] if row[2] else 0  # price column
                    total_value = market_price * item['quantity']

                    results.append({
                        'item_name': item['item_name'],
                        'type_id': row[0],
                        'quantity': item['quantity'],
                        'market_price': market_price,
                        'min_price': row[3] if row[3] else 0,
                        'avg_price': row[4] if row[4] else 0,
                        'total_value': total_value,
                        'volume_available': row[5] if row[5] else 0,
                        'days_remaining': row[6] if row[6] else 0,
                        'group_name': row[7] if row[7] else '',
                        'category_name': row[8] if row[8] else ''
                    })
                else:
                    logger.warning(f"No market data found for: {item['item_name']}")
                    results.append({
                        'item_name': item['item_name'],
                        'type_id': 'N/A',
                        'quantity': item['quantity'],
                        'market_price': 0,
                        'min_price': 0,
                        'avg_price': 0,
                        'total_value': 0,
                        'volume_available': 0,
                        'days_remaining': 0,
                        'group_name': 'N/A',
                        'category_name': 'N/A'
                    })

        # Write to CSV
        with open(output_file, 'w', newline='') as csvfile:
            fieldnames = ['item_name', 'type_id', 'quantity', 'market_price', 'min_price',
                         'avg_price', 'total_value', 'volume_available', 'days_remaining',
                         'group_name', 'category_name']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            writer.writeheader()
            for row in results:
                writer.writerow(row)

        print(f"Successfully created CSV with {len(results)} items at: {output_file}")
        logger.info(f"CSV created with {len(results)} items at: {output_file}")

        # Print summary
        total_market_value = sum(r['total_value'] for r in results)
        items_with_data = sum(1 for r in results if r['type_id'] != 'N/A')
        print(f"\nSummary:")
        print(f"  Total items: {len(results)}")
        print(f"  Items with market data: {items_with_data}")
        print(f"  Total market value: {total_market_value:,.2f} ISK")

        return True

    except FileNotFoundError:
        print(f"Error: Input file not found: {input_file}")
        logger.error(f"Input file not found: {input_file}")
        return False
    except Exception as e:
        print(f"Error parsing structure data: {e}")
        logger.error(f"Error parsing structure data: {e}", exc_info=True)
        return False
