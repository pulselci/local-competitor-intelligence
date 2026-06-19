-- A/B test tracking: which group (A or B) and which subject line was used
ALTER TABLE outreach_prospects
    ADD COLUMN IF NOT EXISTS ab_group         TEXT,          -- 'A' or 'B'
    ADD COLUMN IF NOT EXISTS ab_subject_label TEXT;          -- e.g. 'A-S1', 'B-S4'
