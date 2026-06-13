-- Add is_test flag to businesses table
-- All existing businesses are tagged as test since they predate the July 2026 go-live

ALTER TABLE public.businesses
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;

-- Tag every existing business as a test record
UPDATE public.businesses SET is_test = true;
