# Musical Instruments — Probabilistic Demo

A Streamlit app that runs probabilistic queries over Amazon Musical Instruments
data — products and reviews augmented with LLM-generated probability
distributions (instrument type, is_instrument, sentiment) — using
[ProvSQL](https://provsql.org), a PostgreSQL extension for probabilistic data.

## Files

| File | What it is |
|------|------------|
| `demo.py`           | the Streamlit app |
| `setup.sql`         | builds the whole `amazon` database in one pass (tables, data load, deterministic baseline tables, provenance circuits) |
| `populate.py`       | loads a JSONL file into a Postgres table |
| `requirements.txt`  | Python dependencies |
| `products.jsonl`    | musical products with `instrument` / `is_instrument` distributions |
| `reviews.jsonl`     | reviews with `sentiment` distributions |
| `instruments.jsonl` | instrument reference table |

## Requirements

- PostgreSQL with the [ProvSQL](https://provsql.org) extension installed
  (`shared_preload_libraries = 'provsql'`, then restart the server)
- Python: `pip install -r requirements.txt`

## Run

From this directory:

```sh
createdb amazon
psql -d amazon -f setup.sql
streamlit run demo.py
```

Both connect as the current OS user. The database name `amazon` is set in
`demo.py` and is the default in `populate.py` — change it there if yours differs.
