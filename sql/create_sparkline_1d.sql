-- sparkline_1d: intraday sparkline data (precomputed from spot_prices_1m)
-- Populated by paper_trading main.py every 5 min during market hours
-- Consumed by DayTrading engulfing dashboard for fast sparkline lookups

CREATE TABLE IF NOT EXISTS sparkline_1d (
    symbol      TEXT    PRIMARY KEY,
    trade_date  DATE    NOT NULL,
    closes      JSONB   NOT NULL,
    bar_count   INTEGER NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sparkline_1d_date ON sparkline_1d(trade_date);

GRANT SELECT, INSERT, UPDATE, DELETE ON sparkline_1d TO fr3_app;
