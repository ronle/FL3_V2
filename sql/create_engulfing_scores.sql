-- Engulfing pattern detections (written by DayTrading scanner, read by V2 live trader)
CREATE TABLE IF NOT EXISTS engulfing_scores (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    scan_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timeframe TEXT NOT NULL DEFAULT '5min',
    direction TEXT NOT NULL,                     -- 'bullish' or 'bearish'
    pattern_date TIMESTAMPTZ NOT NULL,
    entry_price NUMERIC,
    stop_loss NUMERIC,
    target_1 NUMERIC,
    target_2 NUMERIC,
    pattern_strength TEXT,                       -- 'strong', 'moderate', 'weak'
    body_ratio NUMERIC,
    range_ratio NUMERIC,
    candle_range NUMERIC,
    volume_confirmed BOOLEAN,
    score NUMERIC(5,4),                          -- reserved for future use
    UNIQUE(symbol, pattern_date, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_engulfing_scores_symbol_ts
    ON engulfing_scores(symbol, scan_ts DESC);
CREATE INDEX IF NOT EXISTS idx_engulfing_scores_live_lookup
    ON engulfing_scores(symbol, direction, scan_ts);

-- Permissions (run as superuser or table owner)
-- GRANT ALL ON TABLE engulfing_scores TO fr3_app;
-- GRANT USAGE, SELECT ON SEQUENCE engulfing_scores_id_seq TO fr3_app;
