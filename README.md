# SQL + NoSQL with replication (Streamlit version)

## File structure
| File | What it does |
|---|---|
| `databases.py` | Shared configuration (file names, columns), the split of the CSV into three parts, and functions to read/write both databases |
| `init_sql_db.py` | Creates the SQL database (SQLite, `sales_sql.db`) with the *sql* and *both* rows |
| `init_nosql_db.py` | Creates the NoSQL database (TinyDB, `sales_nosql.json`) with the *nosql* and *both* rows |
| `replication.py` | Replication protocol: change log, delivery to the other database, pause switch, consistency check |
| `web_app.py` | Streamlit front end: search, edit, insert, delete, replication monitor |

## How to run it
```
pip install -r requirements.txt
python init_sql_db.py
python init_nosql_db.py
python init_log.py
streamlit run web_app.py
```
Run the commands from the project folder. To go back to the original data, run the two `init_*` scripts again.

## Data split
The rule lives in one place, `load_and_split()` in `databases.py`: 1st row → SQL only, 2nd → NoSQL only, 3rd → both, and so on. With 36 rows this gives 12 / 12 / 12. Each init script takes only the rows that belong to its database.

## Replication
- **Where:** in `replication.py`. The log (`replication_log.db`) is a separate file because it belongs to neither database: it acts as the network channel between them.
- **How:** every change to a shared record is written to the chosen database and recorded in the log as *pending*. `replicate()` sends it to the other database, which applies it only if it is newer than the copy it already has (version number and timestamp).
- **Simulating a failure:** the *Pause replication* switch in the sidebar. While it is on, changes wait in the log; when you turn it off, they are all sent.

### Try it
1. Turn on *Pause replication*.
2. Choose *SQL DB*, change `Example_Value` of a record whose location is *both*, and click *Save Table Changes*.
3. Choose *NoSQL DB*, change the same field of the same record, and save.
4. Section 4 shows 2 pending changes and 11 of 12 shared records identical.
5. Turn off the pause: one change shows as *applied*, the other as *skipped*, and the shared records are back to 12 of 12 identical (the most recent change wins).
