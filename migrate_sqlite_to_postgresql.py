"""
Copy the Research Grant Portal data from the local SQLite file into Supabase PostgreSQL.

Windows CMD:
    set "DATABASE_URL=YOUR_SUPABASE_CONNECTION_STRING"
    python migrate_sqlite_to_postgresql.py

Run it on an empty Supabase database, before starting the app with DATABASE_URL.
The app creates its tables and the admin on first start, and this script refuses
to copy into tables that already hold rows, so nothing is duplicated.
"""
import os
import sqlite3

import psycopg  # PostgreSQL driver
from psycopg import sql  # safe SQL building for table and column names

import researchgrantportal as core  # same table definitions as the app (also reads a local .env)

DATABASE_URL = os.getenv("DATABASE_URL")  # the Supabase connection string, never written in code
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set.")
if not os.path.exists(core.SQLITE_DB):
    raise RuntimeError(f"{core.SQLITE_DB} was not found. Run the app locally first.")

sqlite_conn = sqlite3.connect(core.SQLITE_DB)  # the source: the local SQLite file
sqlite_conn.row_factory = sqlite3.Row  # rows can be read by column name
pg_conn = psycopg.connect(DATABASE_URL, prepare_threshold=None)  # the destination; works with Supabase's pooler
pg_conn.autocommit = False  # one transaction: all tables copy, or nothing does

try:
    pg_cur = pg_conn.cursor()
    for statement in core.ddl(is_pg=True):  # create any missing tables in PostgreSQL
        pg_cur.execute(statement)

    counts = {}
    for table in core.TABLES:  # parents before children, so foreign keys always match
        pg_cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
        if pg_cur.fetchone()[0] > 0:
            raise RuntimeError(f"Destination table '{table}' is not empty. Use a fresh Supabase database.")
        rows = sqlite_conn.execute(f'SELECT * FROM "{table}"').fetchall()
        counts[table] = len(rows)
        if not rows:
            continue
        pg_cur.execute("SELECT column_name FROM information_schema.columns "
                       "WHERE table_schema = current_schema() AND table_name = %s", (table,))
        wanted = {row[0] for row in pg_cur.fetchall()}
        columns = [column for column in rows[0].keys() if column in wanted]  # skips columns an older version had
        insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        )
        pg_cur.executemany(insert, [tuple(row[column] for column in columns) for row in rows])  # ids are copied as they are

    for table in core.TABLES:  # continue new ids after the highest copied id (1 when the table is empty)
        if table == "serials":  # keyed by prefix and year, no id column
            continue
        pg_cur.execute(sql.SQL("SELECT setval(pg_get_serial_sequence({}, 'id'), COALESCE(MAX(id), 0) + 1, false) FROM {}")
                       .format(sql.Literal(table), sql.Identifier(table)))

    pg_conn.commit()
    print("Migration completed successfully.")
    for table in core.TABLES:
        print(f"  {table + ':':<14} {counts[table]}")
except Exception:
    pg_conn.rollback()  # undo everything from this run
    raise
finally:
    sqlite_conn.close()
    pg_conn.close()
