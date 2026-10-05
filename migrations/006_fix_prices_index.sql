-- Replace the expression-based unique index on prices with a simple column index.
-- This lets ON CONFLICT clauses reference plan_name and billing_class directly,
-- which is required by upsert_batch().

-- Coerce existing NULLs to empty string before adding NOT NULL constraint
UPDATE prices SET plan_name   = '' WHERE plan_name   IS NULL;
UPDATE prices SET billing_class = '' WHERE billing_class IS NULL;

-- Set NOT NULL + default '' so new inserts don't need to specify these columns
ALTER TABLE prices ALTER COLUMN plan_name    SET NOT NULL;
ALTER TABLE prices ALTER COLUMN plan_name    SET DEFAULT '';
ALTER TABLE prices ALTER COLUMN billing_class SET NOT NULL;
ALTER TABLE prices ALTER COLUMN billing_class SET DEFAULT '';

-- Drop the old expression-based index
DROP INDEX IF EXISTS prices_unique_idx;

-- Simple column index — compatible with ON CONFLICT (cpt, ccn, payer, plan_name, billing_class)
CREATE UNIQUE INDEX prices_unique_idx
  ON prices (cpt, ccn, payer, plan_name, billing_class);
