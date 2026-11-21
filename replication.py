import json
import sqlite3

import pandas as pd

from databases import DATA_COLUMNS, DELETE, GET, KEY, PUT, READ_ALL, now

# Replication protocol between the SQL and the NoSQL database.
#
# Only records whose location is 'both' are replicated. The protocol works like this:
#   1. Every change to a shared record is written to the database chosen by the user
#      and then added to the replication log with status 'pending'.
#   2. replicate() sends the pending log entries to the other database, oldest first,
#      and marks each one 'done'.
#   3. Every record has a version number and an 'updated' time. A log entry is applied
#      only if it is newer than the copy already in the target database, otherwise it
#      is skipped. If both copies were changed while replication was paused (same
#      version), the most recent change wins, so both databases end up equal.
#   4. While replication is paused (simulated network failure) the entries stay
#      'pending' and are sent as soon as replication is resumed.
#
# The log lives in its own SQLite file because it belongs to neither database:
# it plays the role of the network channel between them.

REPLICATION_FILE = "replication_log.db"


def get_connection():
    # Open the replication log database and create its tables the first time
    conn = sqlite3.connect(REPLICATION_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replication_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            record_key TEXT NOT NULL,
            operation  TEXT NOT NULL,          -- 'upsert' or 'delete'
            source     TEXT NOT NULL,          -- database where the change was made
            target     TEXT NOT NULL,          -- database that must receive it
            version    INTEGER NOT NULL,
            data       TEXT NOT NULL,          -- the full record as JSON
            status     TEXT NOT NULL,          -- 'pending' or 'done'
            result     TEXT,
            created    TEXT NOT NULL
        )""")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT)")
    return conn


def reset_log():
    # Delete the replication history (called when the databases are recreated)
    conn = get_connection()
    conn.execute("DELETE FROM replication_log")
    conn.execute("DELETE FROM settings")
    conn.commit()
    conn.close()


def other(db):
    # Return the name of the other database
    return "nosql" if db == "sql" else "sql"


# --- PAUSE SWITCH (simulated network failure) ---

def is_paused():
    # Read the pause flag; it is stored on disk so it survives Streamlit reruns
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE name = 'paused'").fetchone()
    conn.close()
    return row is not None and row[0] == "1"


def set_paused(paused):
    # Turn replication off (simulate a broken link) or back on
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('paused', ?)", ("1" if paused else "0",))
    conn.commit()
    conn.close()
    if not paused:
        replicate()  # deliver everything that queued up during the failure


# --- WRITE OPERATIONS (every change made by the web app goes through here) ---

def save_record(db, record):
    # Write the record to the chosen database, then replicate it if it is shared
    PUT[db](record)
    if record["location"] == "both":
        log_change("upsert", db, record)
        return replicate()
    return "not replicated (record stored in one database only)"


def delete_record(db, key):
    # Delete the record from the chosen database, then replicate the delete if it is shared
    current = GET[db](key)
    DELETE[db](key)
    if current["location"] == "both":
        tombstone = {**current, "version": current["version"] + 1, "updated": now()}
        log_change("delete", db, tombstone)
        return replicate()
    return "not replicated (record stored in one database only)"


def log_change(operation, source, record):
    # Add a 'pending' entry to the replication log
    conn = get_connection()
    conn.execute(
        "INSERT INTO replication_log (record_key, operation, source, target, version, data, status, created) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (record[KEY], operation, source, other(source), int(record["version"]),
         json.dumps(record), now()))
    conn.commit()
    conn.close()


def is_newer(a, b):
    # Compare two versions of the same record: higher version first, then the latest time
    return (int(a["version"]), a["updated"]) > (int(b["version"]), b["updated"])


def replicate():
    # Send all pending log entries to their target database, oldest first
    if is_paused():
        return "queued in the replication log (replication is paused)"

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    pending = conn.execute("SELECT * FROM replication_log WHERE status = 'pending' ORDER BY id").fetchall()

    for entry in pending:
        record = json.loads(entry["data"])
        target = entry["target"]
        current = GET[target](entry["record_key"])

        # Apply the change only if it is newer than the copy in the target database
        if current is not None and not is_newer(record, current):
            result = "skipped: the target copy is newer"
        elif entry["operation"] == "delete":
            DELETE[target](entry["record_key"])
            result = "deleted"
        else:
            PUT[target](record)
            result = "applied"

        conn.execute("UPDATE replication_log SET status = 'done', result = ? WHERE id = ?",
                     (result, entry["id"]))

    conn.commit()
    conn.close()
    return f"replicated ({len(pending)} log entries sent)"


# --- MONITORING ---

def read_log():
    # Return the replication log as a DataFrame, newest entries first
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT id, record_key, operation, source, target, version, status, result, created "
        "FROM replication_log ORDER BY id DESC", conn)
    conn.close()
    return df


def pending_count():
    # Number of changes still waiting to be replicated
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) FROM replication_log WHERE status = 'pending'").fetchone()[0]
    conn.close()
    return n


def check_consistency():
    # Compare every shared record in the two databases and report whether the copies match
    sql_df = READ_ALL["sql"]().set_index(KEY)
    nosql_df = READ_ALL["nosql"]().set_index(KEY)
    shared = sorted(set(sql_df.index[sql_df["location"] == "both"])
                    | set(nosql_df.index[nosql_df["location"] == "both"]))

    rows = []
    for key in shared:
        in_sql, in_nosql = key in sql_df.index, key in nosql_df.index
        equal = (in_sql and in_nosql and
                 all(sql_df.at[key, c] == nosql_df.at[key, c] for c in DATA_COLUMNS[1:]))
        rows.append({
            KEY: key,
            "sql_version": int(sql_df.at[key, "version"]) if in_sql else None,
            "nosql_version": int(nosql_df.at[key, "version"]) if in_nosql else None,
            "status": "in sync" if equal else "different",
        })
    return pd.DataFrame(rows)