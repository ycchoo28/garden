# Card statement import

Finance imports the company card statement once a month, as a CSV file.
The first line must match the header exactly: transaction_ref, posted_on, department_code, category, merchant and amount_usd.
Each row needs a unique transaction reference, a date in YYYY-MM-DD form, a department code, one of the fixed categories and an amount in US dollars.
Run a preview before you import, so that every row error and duplicate is listed.
An import saves every row or none, so fix the file and try again when any row has an error.
A row whose transaction reference was already imported is a duplicate, and it blocks the import.
A voided statement keeps its rows for the record, but those rows stop counting as spend.
Voiding a statement needs a written reason, and an AI agent's request to void waits for a finance user to confirm it.
A statement cannot include rows for a month that is closed.
