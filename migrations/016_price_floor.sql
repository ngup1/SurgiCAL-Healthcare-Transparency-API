-- A sanity floor for hospital prices. Price files sometimes list a procedure code on a
-- supply or add-on line, which reads as (say) a $65 knee replacement. Rates far below
-- what Medicare pays the physician for the code are dropped while a file is parsed.
--   cpt_codes.medicare_amount         the reference, from the Physician Fee Schedule
--   hospital_mrf_links.rates_dropped  how many values the floor removed, for auditing

ALTER TABLE cpt_codes
  ADD COLUMN IF NOT EXISTS medicare_amount NUMERIC;

ALTER TABLE hospital_mrf_links
  ADD COLUMN IF NOT EXISTS rates_dropped INTEGER;

COMMENT ON COLUMN cpt_codes.medicare_amount IS
  'National Medicare Physician Fee Schedule payment in the facility setting (facility total RVUs x conversion factor); the lowest of the global, professional (26) and technical (TC) amounts when the code is split. NULL when Medicare sets no national amount';
COMMENT ON COLUMN hospital_mrf_links.rates_dropped IS
  'Price values the last load dropped as implausibly low (below PRICE_FLOOR_RATIO x cpt_codes.medicare_amount)';
