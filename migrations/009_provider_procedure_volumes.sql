-- Per-provider, per-procedure volume from CMS Medicare utilization data.
-- Enables procedure-specific provider ranking (e.g. top knee surgeons by volume).

CREATE TABLE provider_procedure_volumes (
  npi                 TEXT NOT NULL REFERENCES providers(npi),
  cpt                 TEXT NOT NULL REFERENCES cpt_codes(code),
  total_services      INTEGER,
  total_beneficiaries INTEGER,
  total_payment       NUMERIC,
  wrvu_estimate       NUMERIC,
  measure_year        INTEGER NOT NULL,
  updated_at          TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (npi, cpt, measure_year)
);

COMMENT ON TABLE provider_procedure_volumes IS 'CMS Medicare utilization per provider per CPT code';
COMMENT ON COLUMN provider_procedure_volumes.total_services IS 'Number of times provider billed this CPT to Medicare';
COMMENT ON COLUMN provider_procedure_volumes.wrvu_estimate IS 'services * avg_work_rvu for this CPT';
COMMENT ON COLUMN provider_procedure_volumes.measure_year IS 'CMS dataset year (e.g. 2023)';

CREATE INDEX ON provider_procedure_volumes (cpt);
CREATE INDEX ON provider_procedure_volumes (npi);
