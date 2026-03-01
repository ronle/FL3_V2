-- Add article enrichment columns to paper_trades_log_c
ALTER TABLE paper_trades_log_c ADD COLUMN IF NOT EXISTS has_news BOOLEAN DEFAULT FALSE;
ALTER TABLE paper_trades_log_c ADD COLUMN IF NOT EXISTS article_count INTEGER DEFAULT 0;
