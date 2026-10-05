-- Add specialty_classification to provider_metrics for Trilliant broad specialty grouping
-- e.g. "Physician", "Pharmacist", "Nurse Practitioner", "Physical Therapist"

ALTER TABLE provider_metrics
    ADD COLUMN IF NOT EXISTS specialty_classification TEXT;

COMMENT ON COLUMN provider_metrics.specialty_classification
    IS 'Broad provider classification from Trilliant (Physician, NP, PA, etc.)';
