-- Core tables: cpt_codes, hospitals, hospital_quality, hospital_mrf_links,
-- providers, provider_affiliations, provider_metrics

CREATE TABLE cpt_codes (
  code         TEXT PRIMARY KEY,
  description  TEXT NOT NULL,
  category     TEXT,
  body_system  TEXT,
  avg_work_rvu NUMERIC,
  is_surgical  BOOLEAN DEFAULT FALSE
);

COMMENT ON TABLE cpt_codes IS 'Reference table for CPT/HCPCS procedure codes';
COMMENT ON COLUMN cpt_codes.category IS 'surgery, plastics, scans, iv, device';
COMMENT ON COLUMN cpt_codes.avg_work_rvu IS 'Average work RVU from CMS Physician Fee Schedule';

CREATE TABLE hospitals (
  ccn                TEXT PRIMARY KEY,
  npi                TEXT,
  name               TEXT NOT NULL,
  address            TEXT,
  city               TEXT NOT NULL,
  state              TEXT NOT NULL,
  zip                TEXT,
  location           GEOGRAPHY(Point, 4326),
  phone              TEXT,
  hospital_type      TEXT,
  ownership          TEXT,
  emergency_services BOOLEAN DEFAULT FALSE,
  metadata           JSONB DEFAULT '{}'::jsonb,
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE hospitals IS 'Core hospital data from CMS Hospital General Information';
COMMENT ON COLUMN hospitals.ccn IS 'CMS Certification Number';
COMMENT ON COLUMN hospitals.location IS 'PostGIS point for spatial queries';
COMMENT ON COLUMN hospitals.hospital_type IS 'acute_care, critical_access, childrens, etc.';
COMMENT ON COLUMN hospitals.ownership IS 'government, proprietary, voluntary_nonprofit';


CREATE TABLE hospital_quality (
  ccn                      TEXT PRIMARY KEY REFERENCES hospitals(ccn),
  overall_stars            NUMERIC(2,1),
  mortality_group          TEXT,
  safety_group             TEXT,
  readmission_group        TEXT,
  patient_experience_group TEXT,
  timely_care_group        TEXT,
  psi90_composite          NUMERIC,
  hai_sirs                 JSONB,
  readmission_hip_knee     NUMERIC,
  complication_hip_knee    NUMERIC,
  mortality_cabg           NUMERIC,
  measure_period           TEXT,
  updated_at               TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE hospital_quality IS 'CMS quality metrics per hospital';
COMMENT ON COLUMN hospital_quality.hai_sirs IS 'HAC rates: CLABSI, CAUTI, SSI, MRSA, CDI as JSON';
COMMENT ON COLUMN hospital_quality.measure_period IS 'CMS reporting period, e.g. 2024Q3';

CREATE TABLE hospital_mrf_links (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ccn                  TEXT REFERENCES hospitals(ccn),
  hospital_name        TEXT NOT NULL,
  state                TEXT NOT NULL,
  machine_readable_url TEXT NOT NULL,
  source               TEXT NOT NULL,
  url_status           TEXT DEFAULT 'unknown',
  file_format          TEXT,
  file_size            TEXT,
  last_checked         TIMESTAMPTZ,
  created_at           TIMESTAMPTZ DEFAULT NOW(),
  updated_at           TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(hospital_name, machine_readable_url)
);

COMMENT ON TABLE hospital_mrf_links IS 'Machine-readable file URLs for hospital price transparency';
COMMENT ON COLUMN hospital_mrf_links.source IS 'tpafs_github, manual, etc.';
COMMENT ON COLUMN hospital_mrf_links.url_status IS 'active, broken, unknown';

CREATE TABLE providers (
  npi             TEXT PRIMARY KEY,
  first_name      TEXT NOT NULL,
  last_name       TEXT NOT NULL,
  credential      TEXT,
  specialty       TEXT,
  taxonomy_code   TEXT,
  gender          TEXT,
  medical_school  TEXT,
  graduation_year INTEGER,
  city            TEXT,
  state           TEXT,
  location        GEOGRAPHY(Point, 4326),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE providers IS 'Physicians/surgeons from NPPES registry';
COMMENT ON COLUMN providers.taxonomy_code IS 'NUCC healthcare provider taxonomy code';

CREATE TABLE provider_affiliations (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  npi        TEXT NOT NULL REFERENCES providers(npi),
  ccn        TEXT NOT NULL REFERENCES hospitals(ccn),
  is_primary BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(npi, ccn)
);

COMMENT ON TABLE provider_affiliations IS 'Many-to-many link between providers and hospitals';

CREATE TABLE provider_metrics (
  npi                          TEXT PRIMARY KEY REFERENCES providers(npi),
  patient_rating               NUMERIC(2,1),
  num_reviews                  INTEGER DEFAULT 0,
  total_medicare_services      INTEGER,
  total_medicare_beneficiaries INTEGER,
  total_medicare_payment       NUMERIC,
  wrvu_estimate                NUMERIC,
  volume_bucket                TEXT,
  trilliant_specialty          TEXT,
  trilliant_active             BOOLEAN,
  patient_demographics         JSONB,
  has_sanctions                BOOLEAN DEFAULT FALSE,
  last_refreshed               TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE provider_metrics IS 'Performance, volume, and quality metrics per provider';
COMMENT ON COLUMN provider_metrics.wrvu_estimate IS 'Computed from services * avg_work_rvu';
COMMENT ON COLUMN provider_metrics.volume_bucket IS 'LOW, MEDIUM, HIGH';
