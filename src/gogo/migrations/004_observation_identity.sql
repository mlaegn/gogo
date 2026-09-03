-- One person cannot start surfing the same spot twice at the same instant, so this is
-- the natural key of an observation.
--
-- It exists for the importer. A hand-written CSV gets run more than once — you fix a
-- typo on line 30 and re-run the file — and without a key each run would append a
-- second copy of every earlier row. Duplicated labels are worse than missing ones: they
-- silently reweight whatever the score is fitted or measured against, and nothing about
-- the dataset looks wrong afterwards.
--
-- It also makes `gogo log` safe to fat-finger twice.
ALTER TABLE observations
    ADD CONSTRAINT observations_one_per_start
    UNIQUE (user_id, spot_id, started_at);
