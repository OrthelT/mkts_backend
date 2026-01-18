# Doctrine Tools CLI


This update will expand the functionality of 'src/mkts-backend/utils/parse_fits.py' 

It will add two new features:

## Fit Check
- Add a command line interface called fit-check that will display a table of market availability from the wcmkt db of fit items for an EFT formatted .txt (and if possible from a cut and paste in the cli.) 
- It should take market as an argument: either primary or deployment. 
- It should present a beautiful table that includes type_id, type_name, market_stock, fit_qty, (the quantity of the item required for each fit), fits (the number of fits that can be constructed based on the market_stock of the item), price (the market price of the item), fit_price (fit_qty * price) and avg_price (average price over 30 days). The header names should align with the schema of the marketstats table for simplicity. 
- It should include a header with the name of the fit, ship_name, ship type id and the fit_cost (some of fit_price)
- If the item is not on the watchlist, pricing information will not be available in the marketstats table in the wcmkt database. In this case, it should query the marketorder table to obtain the market information using similar logic to the calculate_market_stats() function, and execute an ESI api call to obtain the historical information. 
- Use a cli library like Rich or other libraries that you think would be useful in creating a beautiful cli. 

## Fit Update Tool
Extend the update_fit_workflow() in parse_fits.py with an interactive interface that:
- allows a user to add a new fit from an EFT formatted txt file or by pasting text in the cli (if possible). Reuse the functionality from Fit Check. It should update all appropriate database tables with the new fit information.
- allows the user to create a new doctrine and choosing the fits that will be used with it interactively.
- allows the user to input the fitting metadata in an interactive interface in the CLI or read it from a fit_metadata file. 
- allows the user to update an existing fit from an EFT formatted fitting or interactively change elements of a fit. 
- allows the user to assign the market that a new or existing fit will be assigned to. 
- confirms the changes before committing them to the database.
- there should be dry-run and local-only options for testing. 

## Doctrine Market Assignment
- Add functionality to configure which markets a doctrine will be tracked in: primary, secondary, or both.
- This can be implemented with a simple flag in the doctrine_fits that can be read by the front end when determining which fits to display. 

## Project Plan and Rules
- First, create a plan that divides the implementation into several phases. Extend this file with your plan, and use it to track progress.
- Write and execute tests prior to concluding each phase and document the work completed in this file. Include any information that a fresh instance of Claude will need to begin the next phase. 
- Use sub-agents to make your work more efficient and preserve your context window. Deploy them concurrently when appropriate to allow faster progress. 
- Call the documentation sub-agent as features are completed to update user and LLM documentation as features are completed. Check documentation at the end of each phase to see if any changes should be documented. 
- IMPORTANT: Avoid complexity. Ensure that new features are implemented with simple solutions that are understandable and do not add unnecessary complexity. 
- If an existing function is modified, be sure to write tests to confirm that 1) it continues to work properly after any changes and 2) that the new functionality works properly.
