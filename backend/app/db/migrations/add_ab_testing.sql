-- A/B test tracking: which group (A or B) and which subject line was used
ALTER TABLE outreach_prospects
    ADD COLUMN IF NOT EXISTS ab_group         TEXT,
    ADD COLUMN IF NOT EXISTS ab_subject_label TEXT;
