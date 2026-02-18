-- Account B trade log (V2 + Engulfing Pattern)
-- Clones schema from paper_trades_log including indexes and constraints
CREATE TABLE IF NOT EXISTS paper_trades_log_b (LIKE paper_trades_log INCLUDING ALL);

-- Permissions (run as superuser or table owner)
-- GRANT ALL ON TABLE paper_trades_log_b TO fr3_app;
-- GRANT USAGE, SELECT ON SEQUENCE paper_trades_log_b_id_seq TO fr3_app;
