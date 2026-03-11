-- adv_14d: 14-day average daily stock volume (precomputed from spot_prices_1m)
-- Populated by refresh_baselines.py (daily 4:30 PM ET job)
-- Consumed by DayTrading engulfing dashboard for fast ADV lookups

CREATE TABLE IF NOT EXISTS adv_14d (
    symbol        TEXT    PRIMARY KEY,
    avg_volume    BIGINT  NOT NULL,
    trading_days  INTEGER NOT NULL,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON adv_14d TO fr3_app;
