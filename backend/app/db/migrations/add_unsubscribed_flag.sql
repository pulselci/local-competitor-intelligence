-- CAN-SPAM compliance: track unsubscribes per prospect and per business
-- Run once in Supabase SQL editor (click "Run without RLS")

ALTER TABLE outreach_prospects
    ADD COLUMN IF NOT EXISTS email_unsubscribed BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE businesses
    ADD COLUMN IF NOT EXISTS email_unsubscribed BOOLEAN NOT NULL DEFAULT FALSE;
