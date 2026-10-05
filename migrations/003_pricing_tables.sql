-- Pricing table for procedure costs from MRFs and Oria Trilliant

CREATE TABLE prices (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cpt             TEXT NOT NULL REFERENCES cpt_codes(code),
  ccn             TEXT NOT NULL REFERENCES hospitals(ccn),
  payer           TEXT NOT NULL,
  plan_name       TEXT,
  billing_class   TEXT,
  cash_price      NUMERIC,
  negotiated_rate NUMERIC,
  negotiated_min  NUMERIC,
  negotiated_max  NUMERIC,
  source          TEXT NOT NULL,
  measure_date    DATE,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX prices_unique_idx
  ON prices (cpt, ccn, payer, COALESCE(plan_name, ''), COALESCE(billing_class, ''));

COMMENT ON TABLE prices IS 'Procedure pricing from hospital MRFs and Oria Trilliant';
COMMENT ON COLUMN prices.payer IS 'Insurer name or CASH for self-pay';
COMMENT ON COLUMN prices.billing_class IS 'professional, facility, both';
COMMENT ON COLUMN prices.source IS 'oria_trilliant, mrf_parsed, tpafs';
