-- Add CABG readmission and hospital-wide readmission to hospital_quality.
-- READM_30_CABG: 30-day readmission rate after CABG (pairs with existing mortality_cabg).
-- READM_30_HOSP_WIDE: hospital-wide all-cause readmission (general quality signal
--   useful for all procedure searches where procedure-specific data is unavailable).

ALTER TABLE hospital_quality
    ADD COLUMN IF NOT EXISTS readmission_cabg      NUMERIC,
    ADD COLUMN IF NOT EXISTS readmission_hosp_wide NUMERIC;

COMMENT ON COLUMN hospital_quality.readmission_cabg      IS 'CMS READM_30_CABG: 30-day readmission rate after CABG';
COMMENT ON COLUMN hospital_quality.readmission_hosp_wide IS 'CMS READM_30_HOSP_WIDE: hospital-wide all-cause 30-day readmission rate';
