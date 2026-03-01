-- Cameron pattern scores table
-- Written by CameronScanner, read by CameronChecker
CREATE TABLE IF NOT EXISTS cameron_scores (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    scan_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pattern_type TEXT NOT NULL,
    pattern_strength TEXT NOT NULL,
    entry_price NUMERIC,
    stop_loss NUMERIC,
    target_1 NUMERIC,
    target_2 NUMERIC,
    gap_pct NUMERIC,
    rvol NUMERIC,
    interval TEXT DEFAULT '5min',
    pattern_date DATE NOT NULL,
    UNIQUE(symbol, pattern_date, pattern_type, interval)
);
CREATE INDEX IF NOT EXISTS idx_cameron_scores_ts ON cameron_scores(scan_ts);
