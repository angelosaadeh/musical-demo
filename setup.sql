-- Musical Instruments — probabilistic demo: full database setup.
--
--   createdb amazon
--   psql -d amazon -f setup.sql
--   streamlit run demo.py
--
-- Builds everything in one pass: tables, data load, deterministic baseline
-- tables, and the ProvSQL provenance circuits.

CREATE EXTENSION IF NOT EXISTS provsql CASCADE;
SET search_path TO public, provsql;


-- ───────────────────────────────────────────────────────────────────────────
-- build_provenance(tbl, col): convert a JSONB {candidate: probability} column
-- into a ProvSQL agg_token column encoding a discrete probability distribution.
-- The monus(gate_i, plus(earlier gates)) construction makes the candidates
-- mutually exclusive.
-- ───────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION build_provenance(_tbl TEXT, _col TEXT)
RETURNS void AS $$
DECLARE
    choose_oid OID;
    row_record RECORD;
    col_list TEXT;
    final_col_list TEXT;
BEGIN
    -- Leftovers from a failed earlier run would make the CREATEs below fail.
    DROP TABLE IF EXISTS new_table;
    DROP TABLE IF EXISTS final_table;

    SELECT oid INTO choose_oid FROM pg_proc WHERE proname = 'choose' LIMIT 1;

    SELECT string_agg(
        CASE WHEN attname = _col
             THEN format('NULL::agg_token AS %I', attname)
             ELSE quote_ident(attname)
        END, ', ' ORDER BY attnum
    )
    INTO col_list
    FROM pg_attribute
    WHERE attrelid = _tbl::regclass
      AND attnum > 0
      AND NOT attisdropped;

    EXECUTE format('CREATE TABLE new_table AS SELECT row_number() OVER () AS rn, %s FROM %I', col_list, _tbl);
    -- Without this the per-row UPDATE below scans the whole table each time.
    EXECUTE 'CREATE INDEX ON new_table(rn)';

    FOR row_record IN EXECUTE format('SELECT row_number() OVER () AS rn, %I::jsonb AS candidates FROM %I', _col, _tbl) LOOP
    DECLARE
        answer TEXT;
        prob FLOAT;
        gate UUID;
        cumulative_gate UUID[] := ARRAY[]::UUID[];
        cumulative_prob FLOAT := 1.0;
        semimods UUID[] := ARRAY[]::UUID[];
        best_answer TEXT;
    BEGIN
        FOR answer, prob IN
            SELECT key, value::FLOAT
            FROM jsonb_each_text(row_record.candidates)
            ORDER BY value::FLOAT DESC, key
            LIMIT 5
        LOOP
            gate := uuid_generate_v4();
            PERFORM create_gate(gate, 'input');
            IF cumulative_gate = ARRAY[]::UUID[] THEN
                PERFORM set_prob(gate, prob);
                semimods := semimods || provenance_semimod(answer, gate);
                cumulative_gate := ARRAY[gate];
                best_answer := answer;
            ELSE
                -- LEAST guards the case where cumulative_prob has shrunk enough
                -- that the conditional probability would exceed 1.
                PERFORM set_prob(gate, LEAST(prob / cumulative_prob, 1.0));
                semimods := semimods || provenance_semimod(
                    answer,
                    provenance_monus(gate, provenance_plus(cumulative_gate))
                );
                cumulative_gate := cumulative_gate || ARRAY[gate];
            END IF;
            cumulative_prob := cumulative_prob * (1 - prob / cumulative_prob);
            IF cumulative_prob < 1e-6 THEN
                EXIT; -- Stop if the remaining probability is negligible
            END IF;
        END LOOP;
        EXECUTE format('UPDATE new_table SET %I = $1 WHERE rn = $2', _col)
        USING provenance_aggregate(choose_oid::int, 2283, best_answer, semimods), row_record.rn;
    END;
    END LOOP;

    SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum)
    INTO final_col_list
    FROM pg_attribute
    WHERE attrelid = 'new_table'::regclass
      AND attnum > 0
      AND NOT attisdropped
      AND attname != 'rn';

    EXECUTE format('CREATE TABLE final_table AS SELECT %s FROM new_table', final_col_list);
    DROP TABLE new_table;
    EXECUTE format('DROP TABLE %I', _tbl);
    EXECUTE format('ALTER TABLE final_table RENAME TO %I', _tbl);
END;
$$ LANGUAGE plpgsql;

-- ───────────────────────────────────────────────────────────────────────────
-- Tables
-- ───────────────────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS musical;
CREATE TABLE musical (
    asin TEXT PRIMARY KEY,
    title TEXT,
    average_rating FLOAT,
    price FLOAT,
    instrument JSONB,
    is_instrument JSONB
);

DROP TABLE IF EXISTS reviews;
CREATE TABLE reviews (
    asin TEXT,
    rating FLOAT,
    text TEXT,
    sentiment JSONB
);

DROP TABLE IF EXISTS instruments;
CREATE TABLE instruments (
    name    TEXT PRIMARY KEY,
    type    TEXT,
    subtype TEXT,
    family  TEXT  -- instrument family: chordophone, aerophone, membranophone, idiophone, electrophone
);

-- ───────────────────────────────────────────────────────────────────────────
-- Load data (populate.py matches JSON keys to columns; extra keys are ignored)
-- ───────────────────────────────────────────────────────────────────────────
\! python3 populate.py musical     products.jsonl
\! python3 populate.py reviews     reviews.jsonl
\! python3 populate.py instruments instruments.jsonl

-- Strip stray whitespace from JSONB candidate keys
UPDATE musical SET instrument = (SELECT jsonb_object_agg(btrim(key, E' \n\t\r'), value) FROM jsonb_each(instrument));
UPDATE reviews SET sentiment  = (SELECT jsonb_object_agg(btrim(key, E' \n\t\r'), value) FROM jsonb_each(sentiment));

-- Merge is_instrument synonyms: yes/1/true/y → yes, no/0/false/n → no (summing probabilities)
UPDATE musical SET is_instrument = (
    SELECT jsonb_object_agg(mapped_key, total_prob)
    FROM (
        SELECT
            CASE WHEN btrim(key, E' \n\t\r') IN ('yes', '1', 'true', 'y') THEN 'yes'
                 WHEN btrim(key, E' \n\t\r') IN ('no',  '0', 'false', 'n') THEN 'no'
                 ELSE btrim(key, E' \n\t\r') END AS mapped_key,
            SUM((value#>>'{}')::float) AS total_prob
        FROM jsonb_each(is_instrument)
        GROUP BY mapped_key
    ) sub
);

-- ───────────────────────────────────────────────────────────────────────────
-- Deterministic baseline tables: keep only the single most-likely value of
-- each distribution. Derived straight from the JSONB here (this replaces the
-- old flatten.py + *_det.jsonl files) while the columns are still JSONB.
-- ───────────────────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS musical_det;
CREATE TABLE musical_det AS
SELECT asin, title, average_rating, price,
       (SELECT key FROM jsonb_each_text(instrument)    ORDER BY value::float DESC, key LIMIT 1) AS instrument,
       (SELECT key FROM jsonb_each_text(is_instrument) ORDER BY value::float DESC, key LIMIT 1) AS is_instrument
FROM musical;

DROP TABLE IF EXISTS reviews_det;
CREATE TABLE reviews_det AS
SELECT asin, rating, text,
       (SELECT key FROM jsonb_each_text(sentiment) ORDER BY value::float DESC, key LIMIT 1) AS sentiment
FROM reviews;

-- ───────────────────────────────────────────────────────────────────────────
-- Build provenance circuits, then attach provenance tokens
-- ───────────────────────────────────────────────────────────────────────────
SELECT build_provenance('musical', 'instrument');
SELECT build_provenance('musical', 'is_instrument');
SELECT build_provenance('reviews', 'sentiment');

SELECT add_provenance('musical');
SELECT add_provenance('reviews');

-- build_provenance rebuilds each table with CREATE TABLE AS, which does not
-- carry over the primary key or its index. Re-create the lookup indexes the
-- demo's joins rely on (reviews.asin is not unique, so it takes an index
-- rather than a primary key).
CREATE INDEX IF NOT EXISTS musical_asin_idx ON musical(asin);
CREATE INDEX IF NOT EXISTS reviews_asin_idx ON reviews(asin);
