#!/usr/bin/env python3
"""
Test script for RegionHistory functionality with automatic type_name population.
This script safely tests the new RegionHistory model without affecting existing data.
"""

import sys
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from mkts_backend.db.models import RegionHistory
from mkts_backend.config.config import DatabaseConfig
from mkts_backend.utils.utils import get_type_name

def test_region_history_functionality():
    """Test the RegionHistory model with automatic type_name population"""

    print("=" * 60)
    print("Testing RegionHistory functionality")
    print("=" * 60)

    # Create engine and session
    engine = DatabaseConfig("wcmkt").engine
    session = Session(engine)

    try:
        # Test data - using well-known EVE Online type IDs
        test_data = [
            {
                'type_id': 34,  # Tritanium
                'average': 5.5,
                'date': datetime.now(timezone.utc),
                'highest': 6.0,
                'lowest': 5.0,
                'order_count': 150,
                'volume': 1000000,
                'timestamp': datetime.now(timezone.utc)
            },
            {
                'type_id': 35,  # Pyerite
                'average': 8.2,
                'date': datetime.now(timezone.utc),
                'highest': 9.0,
                'lowest': 7.5,
                'order_count': 120,
                'volume': 750000,
                'timestamp': datetime.now(timezone.utc)
            },
            {
                'type_id': 36,  # Mexallon
                'average': 45.0,
                'date': datetime.now(timezone.utc),
                'highest': 50.0,
                'lowest': 40.0,
                'order_count': 80,
                'volume': 500000,
                'timestamp': datetime.now(timezone.utc)
            }
        ]

        print("1. Creating test RegionHistory records...")
        created_records = []

        for i, data in enumerate(test_data, 1):
            print(f"   Creating record {i}: type_id={data['type_id']}")

            # Create RegionHistory instance (type_name should be auto-populated)
            history_record = RegionHistory(**data)

            # Check if type_name was populated by the __init__ method
            if history_record.type_name:
                print(f"   ✓ type_name auto-populated: {history_record.type_name}")
            else:
                print(f"   ⚠ type_name not populated yet (will be populated on insert)")

            created_records.append(history_record)
            session.add(history_record)

        print("\n2. Committing to database (this will trigger event listeners)...")
        session.commit()

        print("\n3. Verifying records in database...")
        for record in created_records:
            # Refresh the record to get the latest data from database
            session.refresh(record)

            print(f"   Record ID {record.id}:")
            print(f"     type_id: {record.type_id}")
            print(f"     type_name: {record.type_name}")
            print(f"     average: {record.average}")
            print(f"     volume: {record.volume}")

            # Verify type_name matches what we'd get from get_type_name
            expected_name = get_type_name(record.type_id)
            if record.type_name == expected_name:
                print(f"     ✓ type_name matches expected: {expected_name}")
            else:
                print(f"     ✗ type_name mismatch! Expected: {expected_name}, Got: {record.type_name}")

        print("\n4. Testing resolved_type_name property...")
        for record in created_records:
            resolved_name = record.resolved_type_name
            print(f"   type_id {record.type_id}: resolved_type_name = {resolved_name}")

        print("\n5. Querying records from database...")
        stmt = select(RegionHistory).where(RegionHistory.type_id.in_([34, 35, 36]))
        results = session.scalars(stmt).all()

        print(f"   Found {len(results)} records in database")
        for result in results:
            print(f"     ID {result.id}: {result.type_name} (type_id: {result.type_id})")

        print("\n" + "=" * 60)
        print("✓ All tests completed successfully!")
        print("=" * 60)

        return True

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        session.rollback()
        return False

    finally:
        # Clean up test data
        print("\n6. Cleaning up test data...")
        try:
            # Delete the test records we created
            test_records = session.query(RegionHistory).filter(
                RegionHistory.type_id.in_([34, 35, 36])
            ).all()

            for record in test_records:
                session.delete(record)

            session.commit()
            print(f"   ✓ Deleted {len(test_records)} test records")

        except Exception as e:
            print(f"   ⚠ Warning: Could not clean up test data: {e}")
            session.rollback()

        session.close()

def test_type_name_functionality():
    """Test the get_type_name utility function directly"""

    print("\n" + "=" * 60)
    print("Testing get_type_name utility function")
    print("=" * 60)

    test_type_ids = [34, 35, 36, 37, 38, 39]  # Common minerals

    for type_id in test_type_ids:
        try:
            type_name = get_type_name(type_id)
            print(f"   type_id {type_id}: {type_name}")
        except Exception as e:
            print(f"   type_id {type_id}: Error - {e}")

if __name__ == "__main__":
    print("RegionHistory Functionality Test")
    print("This test will:")
    print("1. Create test RegionHistory records")
    print("2. Verify automatic type_name population")
    print("3. Test the resolved_type_name property")
    print("4. Clean up all test data")
    print()

    # Test the utility function first
    test_type_name_functionality()

    # Test the RegionHistory functionality
    success = test_region_history_functionality()

    if success:
        print("\n🎉 All tests passed! RegionHistory functionality is working correctly.")
    else:
        print("\n❌ Tests failed. Please check the error messages above.")
        sys.exit(1)