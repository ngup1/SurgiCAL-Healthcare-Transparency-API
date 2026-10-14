-- Curated link between procedures and the FDA product types used in them
-- (for example CPT 27447, knee replacement, uses product code JWH, knee prosthesis).
-- Loaded from etl/reference/procedure_product_codes.csv; device_procedure_map is
-- derived from it by joining to devices on product code.

CREATE TABLE procedure_product_codes (
  cpt          TEXT NOT NULL REFERENCES cpt_codes(code),
  product_code TEXT NOT NULL,
  usage_type   TEXT NOT NULL CHECK (usage_type IN ('implant', 'instrument', 'consumable')),
  PRIMARY KEY (cpt, product_code)
);

COMMENT ON TABLE procedure_product_codes IS 'Curated CPT to FDA product code map that fills device_procedure_map';

-- Recalls and adverse events are linked to a device by its 510(k)/PMA number.
ALTER TABLE device_recalls
  ADD COLUMN IF NOT EXISTS premarket_number TEXT;
ALTER TABLE device_adverse_events
  ADD COLUMN IF NOT EXISTS premarket_number TEXT;

COMMENT ON COLUMN device_recalls.premarket_number IS 'First 510(k)/PMA number the recall names; used to set device_id';
COMMENT ON COLUMN device_adverse_events.premarket_number IS '510(k)/PMA number on the report; used to set device_id';

CREATE INDEX IF NOT EXISTS idx_device_recalls_product_code ON device_recalls (product_code);
CREATE INDEX IF NOT EXISTS idx_device_adverse_product_code ON device_adverse_events (product_code);

COMMENT ON COLUMN hospital_quality.readmission_hosp_wide IS 'CMS Hybrid_HWR: hospital-wide all-cause 30-day readmission rate';
COMMENT ON COLUMN hospital_quality.hai_sirs IS 'Infection ratios (SIR): clabsi, cauti, ssi_colon, ssi_hysterectomy, mrsa, cdi as JSON';
