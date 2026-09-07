-- The page is a serving surface.
--
-- 'web' is where labels realistically come from: nobody opens a terminal in a car park.
-- An impression shown on the page has to be recordable, or the one surface that produces
-- anchored labels is the one surface we keep no record of showing.
--
-- The allowed set only grows, so every existing row stays valid.

ALTER TABLE window_impressions
    DROP CONSTRAINT window_impressions_surface_check;

ALTER TABLE window_impressions
    ADD CONSTRAINT window_impressions_surface_check
    CHECK (surface IN ('api', 'cli', 'web'));
