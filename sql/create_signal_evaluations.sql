-- Create signal_evaluations table for V2 paper trading
-- This table records ALL signal evaluations (pass/fail) for analysis
-- Includes metadata JSONB column for shadow GEX data

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
    trade_placed BOOLEAN DEFAULT FALSE,
    entry_price NUMERIC,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signal_evaluations_symbol_date
    ON signal_evaluations(symbol, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_evaluations_date
    ON signal_evaluations(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_evaluations_passed
    ON signal_evaluations(passed_all_filters) WHERE passed_all_filters = true;

COMMENT ON TABLE signal_evaluations IS 'V2: All signal evaluations with pass/fail reasons + GEX metadata for analysis';

-- Required permissions (run as table owner or superuser):
-- GRANT SELECT, INSERT, UPDATE, DELETE ON signal_evaluations TO fr3_app;
-- GRANT USAGE, SELECT ON SEQUENCE signal_evaluations_id_seq TO fr3_app;
