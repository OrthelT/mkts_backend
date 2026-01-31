# Ensure Fresh Data

## Project Goal
Each day we pull market history data for our app with a series of API call for each of the 870 items in our watch-list. Often, there is no new data and the call is wasted. The ESI has two methods to signal if data has changed since your last call that can be configured in headers. ESI returns Status Code 304 if data is unchanged. Refactor aync history ESI function to utilize these methods. 

### "If-None-Match": "Etag":
- ESI response returns a Etag value in the headers.
- Store this in the database.
- For each ESI call to the market history endpoint, check if we have stored an Etag for the item. 
- If an etag exists, pass it in the request header as the value of the "If-None-Match" field.

### "If-Modified-Since"": "Last-Modified":
- Store the 'Last-Modified' value returned from the ESI response header. 
- Pass it as the value of "Last-Modified" in the request header if it exists for the item. 

### "Data Handeling"
- When the ESI returns a 304 response, do not update the DB row for the item. 
- Ensure that 304 responses are not treated as errors that trigger any error Handeling logic 
- Only store new data returned with a 200 status code. 
- Keep a record of requests that resulted in 304 status code (i.e. were unchanged) and include in logs. 

### Data base updates
- make needed changes to the database to implement this logic. 
- do not break existing workflows

### Testing
- Write tests to ensure that this feature works correctly. 

