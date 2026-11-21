import sqlite3

from databases import SQL_FILE, SQL_TABLE, load_and_split
from replication import reset_log

# Create the SQL database (SQLite) and fill it with its part of the CSV file:
# the rows marked 'sql' (only in SQL) and 'both' (also copied to NoSQL)


def create_sql_db():
    df = load_and_split()
    sql_rows = df[df["location"].isin(["sql", "both"])]

    conn = sqlite3.connect(SQL_FILE)
    cursor = conn.cursor()

    # Recreate the table with a fixed schema
    cursor.execute(f"DROP TABLE IF EXISTS {SQL_TABLE}")
    cursor.execute(f"""
        CREATE TABLE {SQL_TABLE} (
            Column_Name      TEXT PRIMARY KEY,
            Description      TEXT,
            Data_Type        TEXT,
            Example_Value    TEXT,
            Business_Meaning TEXT,
            location         TEXT NOT NULL CHECK (location IN ('sql', 'both')),
            version          INTEGER NOT NULL,
            updated          TEXT NOT NULL
        )
    """)

    # Insert the selected rows into the new table
    sql_rows.to_sql(SQL_TABLE, conn, if_exists="append", index=False)
    conn.commit()
    conn.close()

    # A fresh database invalidates the old replication history
    reset_log()

    print(f"SQL database '{SQL_FILE}' created with {len(sql_rows)} records "
          f"({(sql_rows['location'] == 'sql').sum()} SQL only, "
          f"{(sql_rows['location'] == 'both').sum()} shared)")


if __name__ == "__main__":
    create_sql_db()
