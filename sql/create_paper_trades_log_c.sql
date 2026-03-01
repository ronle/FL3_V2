-- Account C trade log — same schema as paper_trades_log_b
CREATE TABLE IF NOT EXISTS paper_trades_log_c (LIKE paper_trades_log_b INCLUDING ALL);
