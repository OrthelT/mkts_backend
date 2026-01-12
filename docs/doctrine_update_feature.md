# Doctine Update Feature

## Goal
Create simple functionality to parse an Eve Online doctrine fit from a text file in Eve Fitting Tool (EFT) format and update it in the appropriate database tables. It should be extendable to add new fits and new doctrines (which may include existing fits or new fits). These databases are the backend for two streamlit apps. 

## Desired functionality
- User points the function to a text file containing the EFT formatted fit file and a fit Metadata file providing things like fit name and description. 
- The program should read the fitting information and update all appropriate tables automatically. 
- The program should detect if a fitting exists and update the existing fitting, or create an entirely new one if it doesn't. 
- The program should allow the user to select whether the updates will be applied to the local or remote (production database). 

## Notes
- Fits are a group of modules intended for a particular ship and are identified by a unique fit_id. 
- Doctrines are groups of fits intended to be used together and are identified by a unique doctrine_id. A fit may be used in more than one doctrine. 

## Databases
- wcfitting.db is the master database containing fittings and doctrines. 
- wcmktprod.db contains tables with the market information for doctrines and fits that is used in our production streamlit app. 
- schemas for key tables are outlined in fit_schemas.md

## Existing Code
There is a great deal of existing code from my past incomplete attempts to implement this feature -- some may not work. This code is contained in:
- "src/mkts_backend/utils/parse_fits.py" (most recent attempt is the update_fits() function, which includes to dos)
- "src/mkts_backend/utils/doctrine_update.py"
- "src/mkts_backend/utils/add2doctrines_table.py"

## Sample Files
- "new_zealot993.txt" - EFT fit file
- "new_zealot_metadata.json" - Fit metadata file

## Assignment
- Review the existing code and design a plan for implementing this feature. 
- Determine which tables will need to be updated for existing fits and new fits. 
- Plan the code that will be required. 
- Plan a refactor of existing code to centralize and streamline the implementation of this functionality.

## Usage (implemented)
- Run the CLI to update a fit from EFT text + metadata:  
  `mkts-backend update-fit --fit-file=new_zealot993.txt --meta-file=new_zealot_metadata.json`
- Defaults to local databases; add `--remote` to target production.  
- Use `--no-clear` to keep existing `fittings_fittingitem` rows; `--dry-run` to preview parsed items without writing.
- To target the north market DB (`wcmktnorth2.db`), add `--target=wcmktnorth` (or `--north`) to the update-fit command; other flags behave the same.