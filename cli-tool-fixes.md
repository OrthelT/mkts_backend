# TASK: CLI Tool Fixes

## The fit-update assign/unassign-market has problems. 
- Database access is very inefficient. We are creating new database connections every time. When assigning/unassigning an entire doctrine, this is resulting in frequent timeouts and errors accessing remote databases (likely because connections are not cleaned up before a new one is called. We should batch database updates to execute in a single connection. 
- Assign-market command does not provision the ship targets table for fits that are added when assigning a doctrine. 
- Assign-market command does not provision a lead ship when a new doctrine is added.
- The code is incredibly verbose and complicated. Develop a plan to simplify it. My hunch is that there is a great deal of duplicative functionality between add fit, add doctrine and assign market and their mirror functions. 

## The update-lead-ship-command 
- does not respect passing --market=both 
- defaults to db_alias "wcmkt", which is deprecated.
