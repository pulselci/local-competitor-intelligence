-- Email open and click tracking for outreach cold emails
ALTER TABLE outreach_prospects
    ADD COLUMN IF NOT EXISTS email_opened_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS email_open_count  INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS link_clicked_at   TIMESTAMPTZ;
