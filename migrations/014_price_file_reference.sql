-- Reference tables for loading hospital price files.

-- Payer names as hospitals write them ("AETNA HEALTH OF CA INC", "Aetna - PPO") mapped to
-- one name per insurer. Loaded from etl/reference/payer_aliases.csv; first match by priority.
CREATE TABLE payer_aliases (
  pattern  TEXT PRIMARY KEY,
  payer    TEXT NOT NULL,
  priority INTEGER NOT NULL
);

COMMENT ON TABLE payer_aliases IS 'Case-insensitive regex patterns that normalize payer names in price files';
COMMENT ON COLUMN payer_aliases.priority IS 'Lower runs first; the first matching pattern wins';

-- Price-file locations entered by hand, for hospitals whose cms-hpt.txt is missing or
-- whose file cannot be matched to a CCN automatically. Loaded from
-- etl/reference/mrf_overrides.csv and never overwritten by discovery.
ALTER TABLE hospital_mrf_links
  ADD COLUMN IF NOT EXISTS is_override BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS last_error  TEXT,
  ADD COLUMN IF NOT EXISTS price_rows  INTEGER;

COMMENT ON COLUMN hospital_mrf_links.source IS 'cms_hpt_txt (the hospital''s own index file) or manual';
COMMENT ON COLUMN hospital_mrf_links.is_override IS 'Entered by hand; discovery leaves the row alone';
COMMENT ON COLUMN hospital_mrf_links.last_error IS 'Why the last load of this file failed, if it did';
COMMENT ON COLUMN hospital_mrf_links.price_rows IS 'Price rows the last successful load produced';
