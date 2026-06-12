-- Add agency prospect support to outreach_prospects table
-- prospect_type: 'local_business' (default, existing records) | 'agency'
-- partnership_type: 'reseller' | 'referral' | 'both' (for agencies only)

ALTER TABLE outreach_prospects
  ADD COLUMN IF NOT EXISTS prospect_type TEXT NOT NULL DEFAULT 'local_business';

ALTER TABLE outreach_prospects
  ADD COLUMN IF NOT EXISTS partnership_type TEXT;

CREATE INDEX IF NOT EXISTS idx_outreach_prospects_type ON outreach_prospects(prospect_type);
