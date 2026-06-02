"""Load a JSONL file into a Postgres table.

JSON keys are matched to the table's columns by name; any extra keys in the
file are ignored, and dict/list values are stored as JSONB. One loader for all
three tables.

    python3 populate.py <table> <file.jsonl> [--db amazon] [--user angelo]
"""
import json
import argparse
import psycopg2


def main():
    p = argparse.ArgumentParser()
    p.add_argument("table")
    p.add_argument("file")
    p.add_argument("--db",   default="amazon")
    p.add_argument("--user", default=None)  # falls back to the OS user
    args = p.parse_args()

    conn = psycopg2.connect(dbname=args.db, **({"user": args.user} if args.user else {}))
    cur = conn.cursor()

    # Discover the target table's columns (in order) and only load those.
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "ORDER BY ordinal_position",
        (args.table,),
    )
    cols = [r[0] for r in cur.fetchall()]
    if not cols:
        raise SystemExit(f"table {args.table!r} not found")

    collist      = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert = f"INSERT INTO {args.table} ({collist}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    with open(args.file) as f:
        for line in f:
            row = json.loads(line)
            vals = [
                json.dumps(row[c]) if isinstance(row.get(c), (dict, list)) else row.get(c)
                for c in cols
            ]
            cur.execute(insert, vals)

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
