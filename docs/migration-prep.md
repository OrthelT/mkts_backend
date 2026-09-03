# pyturso migration review brief

The reviewed and independently verified findings are maintained in
[`migration-review.md`](migration-review.md). This file preserves the original review
brief.

We are making final preparations for our migration to pyturso. There are two elements being migrated, which are currently on worktrees branched from their respective production repositories.
 - **mkts_backend** (this repository): /home/orthel/workspace/github/mkts-turso (backend database updates and cli tools)
 - wcmkts_new: /home/orthel/workspace/github/wcmkts-pyturso-migration (frontend streamlit app)

To avoid interfering with production both worktrees are tracking staging repositories and have their own testing versions of their production databases. Ensure that they can be easily reverted to their production versions with a simple update to settings files (perhaps with a shell script to automate the task.)
To enable this, the settings.toml files should be authoritative for all db access operations.

Perform a review of both repositories and document any remaining work outstanding prior to merging. Document your findings in a .md file for review by me or another coding agent.
