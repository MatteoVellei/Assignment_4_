import sqlite3
from datetime import datetime

import pandas as pd
from tinydb import Query, TinyDB

# Shared configuration used by the init scripts, the replication module and the web app
CSV_FILE = "sales_data_dictionary.csv"
SQL_FILE = "sales_sql.db"            # SQL database (SQLite)
NOSQL_FILE = "sales_nosql.json"      # NoSQL database (TinyDB document store)
SQL_TABLE = "records"

# Columns coming from the CSV file; 'Column_Name' is the unique key of every record
KEY = "Column_Name"
DATA_COLUMNS = ["Column_Name", "Description", "Data_Type", "Example_Value", "Business_Meaning"]

# Extra columns added to every record to support the split and the replication
# location: 'sql', 'nosql' or 'both'   version: change counter   updated: time of last change
META_COLUMNS = ["location", "version", "updated"]


def now():
    # Utility helper returning the current time as text (used to order changes)
    return datetime.now().isoformat(timespec="microseconds")


# --- DATA SPLIT (shared by both init scripts) ---

def load_and_split():
    # Read the CSV file and assign every row to a location, in turn:
    # 1st row -> SQL only, 2nd row -> NoSQL only, 3rd row -> both, 4th row -> SQL only, ...
    # Keeping this rule in one place guarantees that both databases agree on the thirds
    df = pd.read_csv(CSV_FILE, dtype=str, encoding="utf-8-sig").fillna("")
    df["location"] = [["sql", "nosql", "both"][i % 3] for i in range(len(df))]
    df["version"] = 1
    df["updated"] = now()
    return df


# --- SQL DATABASE (SQLite) ---

def sql_connection():
    # Open a connection to the SQLite database file
    return sqlite3.connect(SQL_FILE)


def sql_read_all():
    # Load every SQL record into a Pandas DataFrame
    conn = sql_connection()
    df = pd.read_sql_query(f"SELECT * FROM {SQL_TABLE} ORDER BY {KEY}", conn)
    conn.close()
    return df


def sql_get(key):
    # Return one SQL record as a dictionary, or None if it does not exist
    conn = sql_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {SQL_TABLE} WHERE {KEY} = ?", (key,)).fetchone()
    conn.close()
    return dict(row) if row else None


def sql_put(record):
    # Insert a new SQL record or replace the existing one with the same key
    cols = DATA_COLUMNS + META_COLUMNS
    placeholders = ", ".join(["?"] * len(cols))
    conn = sql_connection()
    conn.execute(f"INSERT OR REPLACE INTO {SQL_TABLE} ({', '.join(cols)}) VALUES ({placeholders})",
                 [record[c] for c in cols])
    conn.commit()
    conn.close()


def sql_delete(key):
    # Remove a record from the SQL database
    conn = sql_connection()
    conn.execute(f"DELETE FROM {SQL_TABLE} WHERE {KEY} = ?", (key,))
    conn.commit()
    conn.close()


# --- NoSQL DATABASE (TinyDB) ---

Record = Query()


def nosql_read_all():
    # Load every NoSQL document into a Pandas DataFrame
    with TinyDB(NOSQL_FILE) as db:
        docs = db.table("records").all()
    df = pd.DataFrame(docs, columns=DATA_COLUMNS + META_COLUMNS)
    return df.sort_values(KEY).reset_index(drop=True)


def nosql_get(key):
    # Return one NoSQL document as a dictionary, or None if it does not exist
    with TinyDB(NOSQL_FILE) as db:
        doc = db.table("records").get(Record[KEY] == key)
    return dict(doc) if doc else None


def nosql_put(record):
    # Insert a new document or replace the existing one with the same key
    with TinyDB(NOSQL_FILE, indent=1) as db:
        db.table("records").upsert(dict(record), Record[KEY] == record[KEY])


def nosql_delete(key):
    # Remove a document from the NoSQL database
    with TinyDB(NOSQL_FILE, indent=1) as db:
        db.table("records").remove(Record[KEY] == key)


# Map database names to their access functions so the other modules can treat both the same way
GET = {"sql": sql_get, "nosql": nosql_get}
PUT = {"sql": sql_put, "nosql": nosql_put}
DELETE = {"sql": sql_delete, "nosql": nosql_delete}
READ_ALL = {"sql": sql_read_all, "nosql": nosql_read_all}