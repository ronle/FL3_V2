-- FL3_V2 Seed Data Tables (Paper Trading)
-- Only creates tables that don't exist - does not modify existing tables

-- Table 1: Prior-day TA values (used for RSI filter)
-- This is a NEW table for paper trading
CREATE TABLE IF NOT EXISTS ta_daily_close (
    symbol VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    rsi_14 DECIMAL(5,2),
    macd DECIMAL(10,4),
    macd_signal DECIMAL(10,4),
    macd_histogram DECIMAL(10,4),
    sma_20 DECIMAL(10,2),
    ema_9 DECIMAL(10,2),
    close_price DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trade_date)
);

-- Table 2: Paper trading log (for tracking trades)
-- This is a NEW table for paper trading
CREATE TABLE IF NOT EXISTS paper_trades_log (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    entry_price DECIMAL(10,2),
    shares INTEGER,
    exit_time TIMESTAMP,
    exit_price DECIMAL(10,2),
    pnl DECIMAL(10,2),
    pnl_pct DECIMAL(6,3),
    exit_reason VARCHAR(20),
    signal_score INTEGER,
    signal_rsi DECIMAL(5,2),
    signal_notional DECIMAL(15,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Note: intraday_baselines_30m and tracked_tickers_v2 already exist from V1
-- We can INSERT into them but not modify their structure
