-- Cameron candidates daily coordination table
-- Written by V2 CameronScanner pre-market, read by V1 article fetch job
CREATE TABLE IF NOT EXISTS cameron_candidates_daily (
    trade_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    gap_pct NUMERIC,
    rvol NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (trade_date, symbol)
);

GRANT ALL ON cameron_candidates_daily TO fr3_app;
