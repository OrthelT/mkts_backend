In our frontend repo @~/workspace/github/wcmkts_new/ we implemented logic to display friendly names for doctrines in alphabetical order. Develop a plan to implement the following:

## Refactor module equivalents to not use hard coded values
- THE PROBLEM: This feature hardcodes these values in doctrine_names.py. 
- This is a bad practice. 
- Refactor to move this data to database tables in our primary/deployment database as an additional column 'friendly_name' in the doctrine_fits table. 
- I have created a temporary doctrine_data table in the local wcmktprod.db to provide an example. 
- TASK 1: populate this data to the remote primary/deployment databases. 

## CLI feature
- TASK 2: expand the fit-update CLI command here in mkts-backend to add or update a friendly_name for a doctrine. 
