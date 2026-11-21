from tinydb import TinyDB

from databases import NOSQL_FILE, load_and_split

# Create the NoSQL database (TinyDB) and fill it with its part of the CSV file:
# the rows marked 'nosql' (only in NoSQL) and 'both' (also copied to SQL)


def create_nosql_db():
    df = load_and_split()
    nosql_rows = df[df["location"].isin(["nosql", "both"])]

    with TinyDB(NOSQL_FILE, indent=1) as db:
        table = db.table("records")

        # Empty the collection, then store every row as a JSON document (no fixed schema)
        table.truncate()
        documents = nosql_rows.to_dict(orient="records")
        for doc in documents:
            doc["version"] = int(doc["version"])
        table.insert_multiple(documents)

    print(f"NoSQL database '{NOSQL_FILE}' created with {len(nosql_rows)} documents "
          f"({(nosql_rows['location'] == 'nosql').sum()} NoSQL only, "
          f"{(nosql_rows['location'] == 'both').sum()} shared)")


if __name__ == "__main__":
    create_nosql_db()
