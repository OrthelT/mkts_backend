from datetime import datetime, timezone
import json
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import RegionHistory

from main import check_tables, fetch_market_orders, fetch_history, calculate_doctrine_stats, calculate_market_stats

def run_diagnostics():
    function_choice = input("Enter the number of the function you want to run: \n1. Check tables\n2. Fetch market orders\n3. Fetch market history\n4. Calculate market stats\n5. Calculate doctrine stats\n")
    if function_choice == "1":
        check_tables()
    elif function_choice == "2":
        orders = fetch_market_orders()
        return orders
    elif function_choice == "3":
        history = fetch_history(pd.read_csv("data/watchlist.csv"))
        return history
    elif function_choice == "4":
        calculate_market_stats()
    elif function_choice == "5":
        calculate_doctrine_stats()





if __name__ == "__main__":
    pass