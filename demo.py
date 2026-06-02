"""Musical Instruments — Probabilistic Explorer.

A small Streamlit app that runs probabilistic SQL over the `amazon` database
using ProvSQL. Every query computes `probability_evaluate(provenance())` — the
probability that a row's LLM-predicted labels hold — and `choose(...)` picks
each label's single most likely value.

Each tab pairs a probabilistic query with an optional deterministic baseline
(the `*_det` tables) so the two can be compared side by side. The last tab is a
point-and-click builder for ad-hoc probabilistic queries.
"""
import time

import pandas as pd
import psycopg2
import streamlit as st

# One shared, autocommitting connection. The search_path includes `provsql` so
# the provenance functions resolve without schema qualification.
conn = psycopg2.connect(dbname="amazon", options="-c search_path=public,provsql")
conn.autocommit = True


# ─── Running queries ──────────────────────────────────────────────────────────
def run_query(query, params=()):
    """Run a probabilistic query and return (dataframe, seconds, notices, methods).

    verbose_level=20 makes ProvSQL emit the rewritten SQL as NOTICEs (shown in
    the "Query rewriting" expander); last_eval_method reports which evaluation
    method(s) ProvSQL used for this query.
    """
    del conn.notices[:]
    cur = conn.cursor()
    try:
        cur.execute("SET provsql.verbose_level = 20")
        cur.execute("SET provsql.last_eval_method = ''")
        start = time.time()
        cur.execute(query, params or None)
        elapsed = time.time() - start
        df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
        notices = list(conn.notices)
        cur.execute("SELECT current_setting('provsql.last_eval_method', true)")
        methods = set((cur.fetchone()[0] or "").split(",")) - {""}
    finally:
        cur.execute("SET provsql.verbose_level = 0")
        cur.close()
    return df, elapsed, notices, methods or None


def run_det_query(query, params=()):
    """Run a plain (deterministic) query against the `*_det` tables.

    Returns (df, seconds, error). On failure df is None and error holds the
    message, so the UI can show the real reason instead of guessing.
    """
    cur = conn.cursor()
    try:
        start = time.time()
        cur.execute(query, params or None)
        df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
        return df, time.time() - start, None
    except Exception as e:
        return None, 0, str(e)
    finally:
        cur.close()


def det_tables_exist():
    """True only if both deterministic baseline tables are present."""
    cur = conn.cursor()
    cur.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name IN ('musical_det', 'reviews_det')"
    )
    n = cur.fetchone()[0]
    cur.close()
    return n == 2


# ─── Displaying results ───────────────────────────────────────────────────────
def rewritten_sql(notices, marker):
    """Return the SQL that follows a ProvSQL rewriting NOTICE, or None."""
    for n in notices:
        if marker in n:
            return n.split(marker, 1)[1].lstrip(":\r\n ").strip()
    return None


def show_query_info(elapsed, notices, methods):
    """Show timing, the evaluation method(s), and the before/after rewritten SQL."""
    st.caption(f"⏱ {elapsed:.3f} s")
    if methods:
        st.caption("Evaluation method(s): " + ", ".join(f"`{m}`" for m in sorted(methods)))

    before = rewritten_sql(notices, "before ProvSQL query rewriting")
    after = rewritten_sql(notices, "after ProvSQL query rewriting")
    with st.expander("Query rewriting"):
        if before:
            st.markdown("**Before rewriting**")
            st.code(before, language="sql")
        if after:
            st.markdown("**After rewriting**")
            st.code(after, language="sql")
        if not before and not after:
            st.caption("No ProvSQL notices captured.")
            for n in notices:
                st.text(n.strip())


def show_results(key):
    """Render a probabilistic result stored in session_state under `key`."""
    if key not in st.session_state:
        return
    df, elapsed, notices, methods = st.session_state[key]
    st.write(f"{len(df)} rows found")
    st.dataframe(df, use_container_width=True)
    show_query_info(elapsed, notices, methods)


def show_comparison(det_key, toggle_key):
    """Optional 'compare with deterministic' toggle and its results block."""
    if not st.toggle("Compare with deterministic", key=toggle_key):
        return
    if det_key not in st.session_state:
        st.info("Run the query first to load the deterministic comparison.")
        return
    df, elapsed, error = st.session_state[det_key]
    if df is None:
        if not det_tables_exist():
            st.warning("Deterministic tables not found. Build them with: `psql -d amazon -f setup.sql`")
        else:
            st.error(f"Deterministic query failed: {error}")
        return
    st.write(f"{len(df)} rows found (deterministic)")
    st.dataframe(df, use_container_width=True)
    st.caption(f"⏱ {elapsed:.3f} s")


# ─── UI ───────────────────────────────────────────────────────────────────────
st.title("Musical Instruments — Probabilistic Explorer")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Reviews by Sentiment × Rating",
    "Instrument vs Accessory",
    "Products by Instrument Type",
    "Sentiment × Instrument Type",
    "Query Builder",
])

# ── Tab 1: reviews whose predicted sentiment disagrees with the star rating ────
with tab1:
    st.header("Reviews by Sentiment × Rating")
    st.caption(
        "Reviews where the LLM-predicted sentiment disagrees with the star rating — "
        'e.g. a confident "positive" on a 1-star review. These mismatches reveal '
        "data-quality issues that star ratings alone miss."
    )
    with st.form("form1"):
        sentiment1 = st.radio("LLM-predicted sentiment:", ["positive", "neutral", "negative"])
        ratings1 = st.multiselect("Star rating(s):", [1, 2, 3, 4, 5], default=[1])
        min_prob1, max_prob1 = st.slider("Probability range", 0.0, 1.0, (0.0, 1.0))
        if st.form_submit_button("▶ Run"):
            if not ratings1:
                st.warning("Select at least one rating.")
            else:
                st.session_state["res1"] = run_query("""
                    SELECT * FROM (
                        SELECT r.asin, r.rating, r.text, r.sentiment,
                               probability_evaluate(provenance()) AS probability
                        FROM reviews r
                        WHERE r.sentiment = %s AND r.rating IN %s
                    ) sub
                    WHERE probability BETWEEN %s AND %s
                    ORDER BY probability DESC
                """, (sentiment1, tuple(ratings1), min_prob1, max_prob1))
                st.session_state["det1"] = run_det_query("""
                    SELECT asin, rating, text, sentiment
                    FROM reviews_det
                    WHERE sentiment = %s AND rating IN %s
                    ORDER BY rating
                """, (sentiment1, tuple(ratings1)))
    show_results("res1")
    show_comparison("det1", "tog1")

# ── Tab 2: products that are plausibly BOTH an instrument and an accessory ─────
with tab2:
    st.header("Instrument vs Accessory")
    st.caption(
        "Products that are genuinely ambiguous — likely to be an instrument or accessory "
        "with equal probability."
    )
    with st.form("form2"):
        min_yes2, max_yes2 = st.slider("Probability range for is_instrument = yes", 0.0, 1.0, (0.0, 1.0))
        min_no2, max_no2 = st.slider("Probability range for is_instrument = no", 0.0, 1.0, (0.0, 1.0))
        if st.form_submit_button("▶ Run"):
            st.session_state["res2"] = run_query("""
                SELECT yes.asin, yes.title,
                       yes.probability  AS prob_instrument,
                       no.probability   AS prob_not_instrument
                FROM (
                    SELECT * FROM (
                        SELECT m.asin, m.title,
                               probability_evaluate(provenance()) AS probability
                        FROM musical m
                        WHERE m.is_instrument = 'yes'
                    ) s WHERE probability BETWEEN %s AND %s
                ) yes
                JOIN (
                    SELECT * FROM (
                        SELECT m.asin,
                               probability_evaluate(provenance()) AS probability
                        FROM musical m
                        WHERE m.is_instrument = 'no'
                    ) s WHERE probability BETWEEN %s AND %s
                ) no ON yes.asin = no.asin
                ORDER BY yes.probability DESC
            """, (min_yes2, max_yes2, min_no2, max_no2))
    show_results("res2")

# ── Tab 3: products (with LLM instrument labels) joined to the reference table ─
with tab3:
    st.header("Products by Instrument Type")
    st.caption(
        "A join between products — augmented with LLM instrument labels — and the "
        "instrument reference table. The probability combines confidence in both the "
        "instrument classification and the instrument-vs-accessory call."
    )
    with st.form("form3"):
        is_instrument3 = "yes" if st.radio("Instrument or accessory", ["Instrument", "Accessory / Spare part"]) == "Instrument" else "no"
        itype3 = st.selectbox("Instrument type", ["wind", "string", "keyboard", "percussion", "electronic"])
        pc1, pc2 = st.columns(2)
        min_price3 = pc1.number_input("Min price ($)", min_value=0, value=0, step=1)
        max_price3 = pc2.number_input("Max price ($)", min_value=0, value=3900, step=1)
        min_prob3, max_prob3 = st.slider("Probability range", 0.0, 1.0, (0.0, 1.0))
        if st.form_submit_button("▶ Run"):
            st.session_state["res3"] = run_query("""
                SELECT * FROM (
                    SELECT m.asin, m.title, m.price,
                           choose(i.name) AS instrument,
                           probability_evaluate(provenance()) AS probability
                    FROM musical m
                    JOIN instruments i ON m.instrument = i.name
                    WHERE m.is_instrument = %s AND m.price BETWEEN %s AND %s
                    GROUP BY m.asin, m.title, m.price
                    HAVING choose(i.type) = %s
                ) sub
                WHERE probability BETWEEN %s AND %s
                ORDER BY probability DESC
            """, (is_instrument3, min_price3, max_price3, itype3, min_prob3, max_prob3))
            st.session_state["det3"] = run_det_query("""
                SELECT m.asin, m.title, m.price, m.instrument
                FROM musical_det m JOIN instruments i ON m.instrument = i.name
                WHERE m.is_instrument = %s AND m.price BETWEEN %s AND %s AND i.type = %s
                ORDER BY m.title
            """, (is_instrument3, min_price3, max_price3, itype3))
    show_results("res3")
    show_comparison("det3", "tog3")

# ── Tab 4: per-product average sentiment probability for an instrument type ────
with tab4:
    st.header("Sentiment × Instrument Type")
    st.caption(
        "For each product, the average probability its reviews match the chosen sentiment "
        "— filtered to one instrument type."
    )
    with st.form("form4"):
        sentiment4 = st.radio("Sentiment:", ["positive", "neutral", "negative"])
        is_instrument4 = "yes" if st.radio("Instrument or accessory:", ["Instrument", "Accessory / Spare part"]) == "Instrument" else "no"
        itype4 = st.selectbox("Instrument type:", ["wind", "string", "keyboard", "percussion", "electronic"])
        min_prob4, max_prob4 = st.slider("Probability range", 0.0, 1.0, (0.0, 1.0))
        if st.form_submit_button("▶ Run"):
            df4, elapsed4, notices4, methods4 = run_query("""
                SELECT asin, title, average_rating,
                       avg(probability) AS probability
                FROM (
                    SELECT m.asin, m.title, m.average_rating,
                           probability_evaluate(provenance()) AS probability
                    FROM musical m
                    JOIN instruments i ON m.instrument = i.name
                    JOIN reviews r ON m.asin = r.asin
                    WHERE m.is_instrument = %s AND r.sentiment = %s
                    GROUP BY m.asin, m.title, m.average_rating, r.rating, r.text
                    HAVING choose(i.type) = %s
                ) inner_sub
                GROUP BY asin, title, average_rating
            """, (is_instrument4, sentiment4, itype4))
            # avg(probability) comes back as text; pull out the number, then
            # apply the probability-range filter and sort in pandas.
            df4["probability"] = pd.to_numeric(
                df4["probability"].astype(str).str.extract(r"([\d.eE+\-]+)")[0],
                errors="coerce",
            )
            df4 = df4[df4["probability"].between(min_prob4, max_prob4)]
            df4 = df4.sort_values("probability", ascending=False)
            st.session_state["res4"] = (df4, elapsed4, notices4, methods4)
            st.session_state["det4"] = run_det_query("""
                SELECT DISTINCT md.asin, md.title, md.average_rating, md.instrument
                FROM (
                    SELECT m.asin, m.title, m.average_rating, m.instrument
                    FROM musical_det m
                    JOIN instruments i ON m.instrument = i.name
                    WHERE m.is_instrument = %s AND i.type = %s
                ) md
                JOIN reviews_det r ON md.asin = r.asin
                WHERE r.sentiment = %s
            """, (is_instrument4, itype4, sentiment4))
    show_results("res4")
    show_comparison("det4", "tog4")


# ─── Query Builder ──────────────────────────────────────────────────────────
# A column definition is (label, SELECT expression, GROUP BY expression). The
# GROUP BY expression is None for aggregate columns such as choose(...).
def musical_columns(join_reviews):
    cols = [
        ("m.asin", "m.asin", "m.asin"),
        ("m.title", "m.title", "m.title"),
        ("m.price", "m.price", "m.price"),
        ("m.average_rating", "m.average_rating", "m.average_rating"),
        ("instrument", "choose(m.instrument) AS instrument", None),
        ("is_instrument", "choose(m.is_instrument) AS is_instrument", None),
    ]
    if join_reviews:
        cols += [
            ("r.rating", "r.rating", "r.rating"),
            ("r.text", "r.text", "r.text"),
            ("sentiment", "choose(r.sentiment) AS sentiment", None),
        ]
    return cols


def reviews_columns(join_musical):
    cols = [
        ("r.asin", "r.asin", "r.asin"),
        ("r.rating", "r.rating", "r.rating"),
        ("r.text", "r.text", "r.text"),
        ("sentiment", "choose(r.sentiment) AS sentiment", None),
    ]
    if join_musical:
        cols += [
            ("m.title", "m.title", "m.title"),
            ("m.price", "m.price", "m.price"),
            ("instrument", "choose(m.instrument) AS instrument", None),
            ("is_instrument", "choose(m.is_instrument) AS is_instrument", None),
        ]
    return cols


INSTRUMENT_COLUMNS = [
    ("i.name", "choose(i.name) AS instrument_name", None),
    ("i.type", "choose(i.type) AS instrument_type", None),
    ("i.subtype", "choose(i.subtype) AS instrument_subtype", None),
    ("i.family", "choose(i.family) AS family", None),
]

# Columns on the instruments table are aggregated with choose(), so conditions
# on them must go in HAVING rather than WHERE.
CHOOSE_COLS = {"i.type", "i.subtype", "i.name", "i.family"}


def build_query_sql(from_clause, sel_defs, conditions, join_instruments, join_reviews,
                    prob_min, prob_max, limit):
    """Assemble a probabilistic query string from the builder's selections."""
    select_exprs = [c[1] for c in sel_defs] + ["probability_evaluate(provenance()) AS probability"]
    group_by_cols = [c[2] for c in sel_defs if c[2] is not None]
    needs_group_by = any(c[2] is None for c in sel_defs) or join_instruments or join_reviews

    where_parts, having_parts = [], []
    for wcol, wop, wval in conditions:
        if wcol in CHOOSE_COLS:
            having_parts.append(f"choose({wcol}) {wop} '{wval}'")
        elif wop == "IN":
            vals = ", ".join(f"'{v.strip()}'" for v in wval.split(","))
            where_parts.append(f"{wcol} IN ({vals})")
        elif wval.lstrip("-").replace(".", "", 1).isdigit():
            where_parts.append(f"{wcol} {wop} {wval}")
        else:
            where_parts.append(f"{wcol} {wop} '{wval}'")

    inner_lines = [f"SELECT {(chr(10) + '               ,').join(select_exprs)}", f"FROM   {from_clause}"]
    if where_parts:
        inner_lines.append("WHERE  " + "\n           AND ".join(where_parts))
    if needs_group_by and group_by_cols:
        inner_lines.append("GROUP BY " + ", ".join(group_by_cols))
    if having_parts:
        inner_lines.append("HAVING " + "\n           AND ".join(having_parts))

    return (
        "SELECT * FROM (\n"
        f"        {chr(10).join(inner_lines)}\n"
        f"    ) sub\n"
        f"    WHERE probability BETWEEN {prob_min} AND {prob_max}\n"
        f"    ORDER BY probability DESC\n"
        f"    LIMIT {limit}"
    )


with tab5:
    st.header("Custom Query Builder")
    st.caption("Configure your query step by step, generate the SQL, edit if needed, then run.")
    st.session_state.setdefault("qb_sql_area", "")

    # 1. Tables — pick the primary table and which others to JOIN.
    st.subheader("1. Tables")
    primary_table = st.selectbox("Primary table", ["musical", "reviews"], key="qb_primary")
    join_instruments = join_musical = join_reviews = False
    if primary_table == "musical":
        join_instruments = st.checkbox("JOIN instruments", value=True, key="qb_join_instr")
        join_reviews = st.checkbox("JOIN reviews", key="qb_join_reviews")
    else:
        join_musical = st.checkbox("JOIN musical", key="qb_join_musical")
        if join_musical:
            join_instruments = st.checkbox("JOIN instruments (requires musical)", key="qb_join_instr2")

    # 2. SELECT columns — the set of available columns depends on the joins.
    st.subheader("2. SELECT columns")
    if primary_table == "musical":
        col_defs = musical_columns(join_reviews)
    else:
        col_defs = reviews_columns(join_musical)
    if join_instruments:
        col_defs += INSTRUMENT_COLUMNS
    col_labels = [c[0] for c in col_defs]
    selected_lbls = st.multiselect("Columns", col_labels, default=col_labels[:3], key="qb_cols")

    # 3. WHERE / HAVING conditions.
    st.subheader("3. WHERE conditions")
    where_candidates = []
    if primary_table == "musical":
        where_candidates += ["m.asin", "m.price", "m.average_rating", "m.instrument", "m.is_instrument"]
    else:
        where_candidates += ["r.asin", "r.rating", "r.sentiment"]
    if join_reviews:
        where_candidates += ["r.asin", "r.rating", "r.sentiment"]
    if join_musical:
        where_candidates += ["m.asin", "m.price", "m.average_rating", "m.instrument", "m.is_instrument"]
    if join_instruments:
        where_candidates += ["i.type", "i.subtype", "i.name", "i.family"]
    where_opts = list(dict.fromkeys(where_candidates))

    n_cond = int(st.number_input("Number of conditions", 0, 5, 0, key="qb_ncond"))
    conditions = []
    for idx in range(n_cond):
        c1, c2, c3 = st.columns([3, 2, 3])
        wcol = c1.selectbox("col", where_opts, key=f"qb_wcol_{idx}", label_visibility="collapsed")
        wop = c2.selectbox("op", ["=", "!=", "<", ">", "<=", ">=", "LIKE", "IN"], key=f"qb_wop_{idx}", label_visibility="collapsed")
        wval = c3.text_input("val", key=f"qb_wval_{idx}", label_visibility="collapsed", placeholder="value")
        if wval.strip():
            conditions.append((wcol, wop, wval.strip()))

    # 4. Output limits.
    st.subheader("4. Output")
    prob_min, prob_max = st.slider("Probability range", 0.0, 1.0, (0.0, 1.0), key="qb_prob")
    limit = int(st.number_input("LIMIT", 1, 1000, 100, key="qb_limit"))

    st.divider()

    # Assemble the SQL from the selections above.
    if primary_table == "musical":
        from_clause = "musical m"
        if join_instruments:
            from_clause += "\n        JOIN instruments i ON m.instrument = i.name"
        if join_reviews:
            from_clause += "\n        JOIN reviews r ON m.asin = r.asin"
    else:
        from_clause = "reviews r"
        if join_musical:
            from_clause += "\n        JOIN musical m ON r.asin = m.asin"
        if join_instruments:
            from_clause += "\n        JOIN instruments i ON m.instrument = i.name"

    sel_defs = [c for c in col_defs if c[0] in selected_lbls]
    generated_sql = build_query_sql(
        from_clause, sel_defs, conditions, join_instruments, join_reviews,
        prob_min, prob_max, limit,
    )

    # Generate fills the editable text area; Run executes whatever is in it.
    btn_col, run_col = st.columns(2)
    if btn_col.button("⚙ Generate SQL", key="qb_gen_btn"):
        st.session_state["qb_sql_area"] = generated_sql
    run_qb = run_col.button("▶ Run query", key="qb_run_btn")

    final_sql = st.text_area("SQL (edit freely before running)", height=280, key="qb_sql_area")

    if run_qb:
        if not final_sql.strip():
            st.warning("Generate or write a query first.")
        else:
            try:
                st.session_state["res_qb"] = run_query(final_sql.strip())
            except Exception as e:
                st.error(str(e))

    show_results("res_qb")
