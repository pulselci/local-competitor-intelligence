-- Growth alert log: tracks weekly alert emails sent to Growth subscribers
-- Prevents sending more than one alert email per subscriber per week

CREATE TABLE IF NOT EXISTS growth_alert_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    triggers    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- list of alert types that fired
    to_email    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_growth_alert_log_business_sent
    ON growth_alert_log (business_id, sent_at DESC);
