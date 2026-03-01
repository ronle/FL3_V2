-- Migration: Add big-hitter columns to paper_trades_log_b
-- For Account B redesign: direction-aware trading with limit orders and stop/target levels.
-- Safe to re-run (IF NOT EXISTS on all columns).

ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS direction TEXT DEFAULT 'bullish';
ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS stop_price NUMERIC;
ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS target_price NUMERIC;
ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS risk_per_share NUMERIC;
ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS limit_order_id TEXT;
ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS order_submitted_at TIMESTAMPTZ;
ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS pattern_date TIMESTAMPTZ;
ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS candle_range NUMERIC;
ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS pattern_strength TEXT;
