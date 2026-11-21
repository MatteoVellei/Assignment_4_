import os

import pandas as pd
import streamlit as st

from databases import DATA_COLUMNS, GET, KEY, META_COLUMNS, NOSQL_FILE, READ_ALL, SQL_FILE, now
from replication import (REPLICATION_FILE, check_consistency, delete_record, is_paused,
                         pending_count, read_log, replicate, save_record, set_paused)

# Configure the Streamlit page layout and title
st.set_page_config(page_title="Sales Data Manager", layout="wide")

st.title("Data-Intensive Systems - SQL + NoSQL with Replication")

# Stop the app with a clear message if the databases have not been created yet
if not all(os.path.exists(f) for f in [SQL_FILE, NOSQL_FILE, REPLICATION_FILE]):
    st.error("Database files not found. Run 'init_sql_db.py', 'init_nosql_db.py' and 'python replication.py' first.")
    st.stop()

# Map sidebar names to the database they show ('all' merges both databases)
VIEWS = {
    "All Databases": "all",
    "SQL DB (SQLite)": "sql",
    "NoSQL DB (TinyDB)": "nosql",
}

# Sidebar widget allowing the user to select which database to search or modify
st.sidebar.header("Database Selector")
selected_view_name = st.sidebar.selectbox("Choose a database:", list(VIEWS.keys()))
selected_db = VIEWS[selected_view_name]

# Sidebar switch that simulates a network failure between the two databases
st.sidebar.header("Replication")
paused = st.sidebar.toggle("Pause replication (simulate network failure)", value=is_paused())
if paused != is_paused():
    set_paused(paused)  # resuming also sends every change that was queued
    st.rerun()
st.sidebar.metric("Changes waiting to be replicated", pending_count())


def load_merged():
    # Combine both databases into one table and show where each record is stored
    sql_df = READ_ALL["sql"]()
    nosql_df = READ_ALL["nosql"]()
    sql_keys, nosql_keys = set(sql_df[KEY]), set(nosql_df[KEY])

    # Keep one row per key (the SQL copy when it exists, otherwise the NoSQL copy)
    merged = pd.concat([sql_df, nosql_df[~nosql_df[KEY].isin(sql_keys)]], ignore_index=True)
    merged["stored_in"] = merged[KEY].map(
        lambda k: " + ".join(name for name, keys in [("SQL", sql_keys), ("NoSQL", nosql_keys)] if k in keys))
    return merged.sort_values(KEY).reset_index(drop=True)


def filter_rows(df, text):
    # Keep only the rows where any data column contains the search text (case-insensitive)
    if not text:
        return df
    mask = df[DATA_COLUMNS].apply(lambda col: col.astype(str).str.contains(text, case=False, regex=False))
    return df[mask.any(axis=1)].reset_index(drop=True)


st.header(selected_view_name)

# --- SECTION 1: SEARCH, READ RECORDS & INLINE CELL EDITING ---
st.subheader("1. Search (View and Edit)")

search_text = st.text_input("Search in every field (leave empty to show everything)")

df = pd.DataFrame()
try:
    # Load the records of the selected view and apply the search filter
    df = load_merged() if selected_db == "all" else READ_ALL[selected_db]()
    df = filter_rows(df, search_text)
    st.caption(f"{len(df)} records found")

    if df.empty:
        st.info("No records found")
    elif selected_db == "all":
        # The merged view is read-only: changes are made in one specific database
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption("To edit or delete a record, choose the SQL or the NoSQL database in the sidebar.")
    else:
        # Data editor component allowing direct cell modification
        edited_df = st.data_editor(
            df,
            disabled=[KEY] + META_COLUMNS,  # Keep the key and the replication fields read-only
            width="stretch",
            hide_index=True,
            key=f"editor_{selected_db}_{search_text}"
        )

        # Button to commit modified table cells back to the selected database
        if st.button("Save Table Changes", type="primary"):
            try:
                messages = []

                # Compare every edited row with the original and save only the changed ones
                for index, row in edited_df.iterrows():
                    original = df.loc[index]
                    if all(str(row[c]) == str(original[c]) for c in DATA_COLUMNS):
                        continue

                    record = {c: ("" if pd.isna(row[c]) else str(row[c])) for c in DATA_COLUMNS}
                    record["location"] = original["location"]
                    record["version"] = int(original["version"]) + 1
                    record["updated"] = now()

                    result = save_record(selected_db, record)
                    messages.append(f"{record[KEY]}: {result}")

                if messages:
                    st.session_state["message"] = "Saved. " + " | ".join(messages)
                else:
                    st.session_state["message"] = "No changes to save."
                st.rerun()
            except Exception as e:
                st.error(f"Failed to update database: {e}")

except Exception as e:
    st.error(
        f"Could not load the data. Make sure you ran 'init_sql_db.py', 'init_nosql_db.py' and 'python replication.py' first.\nDetails: {e}"
    )

# Show the result of the last save, add or delete after the page reloads
if "message" in st.session_state:
    st.success(st.session_state.pop("message"))

# --- SECTION 2: ADD NEW RECORD ---
st.subheader("2. Add New Record")

with st.form(key="add_record_form"):
    inputs = {}

    # Input controls for every CSV column plus the database where the record is stored
    inputs[KEY] = st.text_input("Column Name (unique key, e.g. Delivery_Address)")
    inputs["Description"] = st.text_input("Description")
    inputs["Data_Type"] = st.selectbox("Data Type", ["Text", "Integer", "Decimal", "Date", "Time"])
    inputs["Example_Value"] = st.text_input("Example Value")
    inputs["Business_Meaning"] = st.text_input("Business Meaning")
    location = st.selectbox("Store in", ["sql", "nosql", "both"],
                            format_func=lambda v: {"sql": "SQL only", "nosql": "NoSQL only",
                                                   "both": "Both (replicated)"}[v])

    submit_button = st.form_submit_button(label="Insert Record")

if submit_button:
    # Input validation for the key, which must be filled in and unique across both databases
    key = inputs[KEY].strip()
    if not key:
        st.warning("Field 'Column Name' cannot be empty")
    elif GET["sql"](key) or GET["nosql"](key):
        st.warning(f"A record with key '{key}' already exists")
    else:
        try:
            record = {**inputs, KEY: key, "location": location, "version": 1, "updated": now()}

            # A shared record is written to SQL first and reaches NoSQL through the replication log
            first_db = "sql" if location == "both" else location
            result = save_record(first_db, record)

            st.session_state["message"] = f"New record '{key}' added to {first_db.upper()}: {result}"
            st.rerun()
        except Exception as e:
            st.error(f"Failed to insert record: {e}")

# --- SECTION 3: DELETE RECORD ---
st.subheader("3. Delete Record")

if selected_db == "all":
    st.info("Choose the SQL or the NoSQL database in the sidebar to delete a record")
elif not df.empty:
    selected_key = st.selectbox("Select record to delete:", df[KEY].tolist())

    if st.button("Delete Record", type="primary"):
        try:
            # Delete from the selected database; a shared record is also removed from the other one
            result = delete_record(selected_db, selected_key)
            st.session_state["message"] = f"Record '{selected_key}' deleted: {result}"
            st.rerun()
        except Exception as e:
            st.error(f"Failed to delete record: {e}")
else:
    st.info("No records available to delete")

# --- SECTION 4: REPLICATION MONITOR ---
st.subheader("4. Replication")

if is_paused():
    st.warning("Replication is paused: changes to shared records wait in the log until it is resumed.")

# Consistency check of the records stored in both databases
consistency = check_consistency()
if not consistency.empty:
    in_sync = (consistency["status"] == "in sync").sum()
    st.write(f"**{in_sync} of {len(consistency)}** shared records are identical in both databases.")
    with st.expander("Show shared records"):
        st.dataframe(consistency, width="stretch", hide_index=True)

# Replication log: every change to a shared record and what happened to it
st.write("**Replication log**")
log_df = read_log()
if log_df.empty:
    st.info("No changes replicated yet. Edit a record stored in both databases to see an entry here.")
else:
    st.dataframe(log_df, width="stretch", hide_index=True)
