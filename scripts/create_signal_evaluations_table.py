"""
Create signal_evaluations table.
Run once to set up the table for signal logging.
"""
import psycopg2

def main():
    # Password decoded from Secret Manager URL encoding
    # %24 = $, %21 = !, %40 = @
    conn = psycopg2.connect(
        host='localhost',
        port=5433,
        dbname='fl3',
        user='fr3_app',
        password='cviHs9NaUqS45$0gjkBu2znKyFV!@LCTOQd18RDW'
    )
    cur = conn.cursor()

    sql = """
    CREATE TABLE IF NOT EXISTS signal_evaluations (
        id SERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        detected_at TIMESTAMPTZ NOT NULL,
        notional NUMERIC,
        ratio NUMERIC,
        call_pct NUMERIC,
        sweep_pct NUMERIC,
        num_strikes INTEGER,
        contracts INTEGER,
        rsi_14 NUMERIC,
        macd_histogram NUMERIC,
        trend INTEGER,
        score_volume INTEGER,
        score_call_pct INTEGER,
        score_sweep INTEGER,
        score_strikes INTEGER,
        score_notional INTEGER,
        score_total INTEGER,
        passed_all_filters BOOLEAN,
        rejection_reason TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_signal_evaluations_symbol_date
        ON signal_evaluations(symbol, detected_at DESC);
    CREATE INDEX IF NOT EXISTS idx_signal_evaluations_date
        ON signal_evaluations(detected_at DESC);
    CREATE INDEX IF NOT EXISTS idx_signal_evaluations_passed
        ON signal_evaluations(passed_all_filters) WHERE passed_all_filters = true;
    """

    print("Creating signal_evaluations table...")
    cur.execute(sql)
    conn.commit()

    # Verify
    cur.execute("SELECT COUNT(*) FROM signal_evaluations")
    count = cur.fetchone()[0]
    print(f"Table created successfully. Current rows: {count}")

    cur.close()
    conn.close()
    print("Done!")

if __name__ == "__main__":
    main()
