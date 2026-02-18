-- Migration: engulfing_scores v51 → v51b
-- Changes: 5-min timeframe, nullable score, target columns, new index
-- Safe to run multiple times (all statements are idempotent)

-- 1. Add target columns (IF NOT EXISTS via DO block)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'engulfing_scores' AND column_name = 'target_1') THEN
        ALTER TABLE engulfing_scores ADD COLUMN target_1 NUMERIC;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'engulfing_scores' AND column_name = 'target_2') THEN
        ALTER TABLE engulfing_scores ADD COLUMN target_2 NUMERIC;
    END IF;
END $$;

-- 2. Change timeframe default from 'daily' to '5min'
ALTER TABLE engulfing_scores ALTER COLUMN timeframe SET DEFAULT '5min';

-- 3. Make score nullable (drop NOT NULL if it exists)
ALTER TABLE engulfing_scores ALTER COLUMN score DROP NOT NULL;

-- 4. Add comment to score column
COMMENT ON COLUMN engulfing_scores.score IS 'Reserved for future use — nullable';

-- 5. Drop old score-based partial index
DROP INDEX IF EXISTS idx_engulfing_scores_lookup;

-- 6. Create new live lookup index
CREATE INDEX IF NOT EXISTS idx_engulfing_scores_live_lookup
    ON engulfing_scores(symbol, direction, scan_ts);
