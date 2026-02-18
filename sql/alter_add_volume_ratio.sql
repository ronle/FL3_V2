-- Add volume ratio column to trade logs and active signals
-- volume_ratio = total_volume / volume_ema_30d from orats_daily
-- Used for informational display only (not filtering)

ALTER TABLE paper_trades_log ADD COLUMN IF NOT EXISTS volume_ratio DECIMAL(8,2);
ALTER TABLE paper_trades_log_b ADD COLUMN IF NOT EXISTS volume_ratio DECIMAL(8,2);
ALTER TABLE active_signals ADD COLUMN IF NOT EXISTS volume_ratio DECIMAL(8,2);
