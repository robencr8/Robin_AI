-- ECO Technology Pipeline — Phase 2 DB Migration
-- Run once on Neon DB via the Neon console or psql
-- Idempotent — safe to run multiple times

-- Add RAG intelligence columns to leads table
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS lead_score       INTEGER DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS score_band       VARCHAR(10) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS rag_intent       VARCHAR(50) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS rag_confidence   FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS rag_method       VARCHAR(50) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS proposal_context TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS recommended_action TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS scored_at        TIMESTAMPTZ DEFAULT NULL;

-- Index for fast hot/warm lead queries
CREATE INDEX IF NOT EXISTS idx_leads_score_band
  ON leads (score_band)
  WHERE score_band IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_leads_lead_score
  ON leads (lead_score DESC NULLS LAST);

-- Add emirate column if missing (needed for scoring)
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS emirate VARCHAR(100) DEFAULT NULL;

-- Add urgency column if missing
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS urgency VARCHAR(100) DEFAULT NULL;

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'leads'
  AND column_name IN (
    'lead_score','score_band','rag_intent','rag_confidence',
    'rag_method','proposal_context','recommended_action',
    'scored_at','emirate','urgency'
  )
ORDER BY column_name;
