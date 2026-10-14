-- Hospital price files give separate inpatient and outpatient prices, and a gross
-- (chargemaster) charge that is not what a cash patient pays.
--   setting      keeps inpatient and outpatient rows apart instead of averaging them.
--   gross_charge holds the chargemaster price, so cash_price is only ever the
--                discounted cash price the hospital published.

ALTER TABLE prices
  ADD COLUMN IF NOT EXISTS setting      TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS gross_charge NUMERIC;

COMMENT ON COLUMN prices.setting IS 'inpatient, outpatient, both, or empty when the file does not say';
COMMENT ON COLUMN prices.gross_charge IS 'Chargemaster (list) price before any discount';
COMMENT ON COLUMN prices.cash_price IS 'Discounted cash price; NULL when the hospital published none';
COMMENT ON COLUMN prices.source IS 'mrf_parsed (hospital machine-readable file) or mock';

DROP INDEX IF EXISTS prices_unique_idx;
CREATE UNIQUE INDEX prices_unique_idx
  ON prices (cpt, ccn, payer, plan_name, billing_class, setting);
