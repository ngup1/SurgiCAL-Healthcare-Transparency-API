-- Device marketplace tables: devices, recalls, adverse events, procedure mapping, pricing

CREATE TABLE devices (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fda_product_code  TEXT,
  brand_name        TEXT NOT NULL,
  generic_name      TEXT,
  manufacturer      TEXT NOT NULL,
  device_class      TEXT,
  medical_specialty TEXT,
  premarket_number  TEXT,
  description       TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE devices IS 'Medical devices from FDA registration and 510(k)/PMA databases';
COMMENT ON COLUMN devices.device_class IS 'FDA device class: I, II, III';
COMMENT ON COLUMN devices.premarket_number IS '510(k) or PMA approval number';

CREATE TABLE device_recalls (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id        UUID REFERENCES devices(id),
  recall_number    TEXT UNIQUE NOT NULL,
  product_code     TEXT,
  brand_name       TEXT,
  manufacturer     TEXT,
  recall_class     TEXT NOT NULL,
  reason           TEXT,
  status           TEXT,
  recall_date      DATE,
  termination_date DATE,
  quantity         TEXT,
  distribution     TEXT,
  created_at       TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE device_recalls IS 'FDA device recalls (Class I = most serious)';

CREATE TABLE device_adverse_events (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id        UUID REFERENCES devices(id),
  mdr_report_key   TEXT UNIQUE,
  product_code     TEXT,
  brand_name       TEXT,
  manufacturer     TEXT,
  event_type       TEXT NOT NULL,
  event_date       DATE,
  patient_outcomes JSONB,
  device_problems  JSONB,
  event_narrative  TEXT,
  created_at       TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE device_adverse_events IS 'FDA MAUDE adverse event reports';
COMMENT ON COLUMN device_adverse_events.event_type IS 'death, injury, malfunction';

CREATE TABLE device_procedure_map (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id  UUID NOT NULL REFERENCES devices(id),
  cpt        TEXT NOT NULL REFERENCES cpt_codes(code),
  usage_type TEXT,
  UNIQUE(device_id, cpt)
);

COMMENT ON TABLE device_procedure_map IS 'Maps devices to CPT procedures they are used in';
COMMENT ON COLUMN device_procedure_map.usage_type IS 'implant, instrument, consumable';

CREATE TABLE device_prices (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id        UUID NOT NULL REFERENCES devices(id),
  ccn              TEXT REFERENCES hospitals(ccn),
  list_price       NUMERIC,
  negotiated_price NUMERIC,
  source           TEXT NOT NULL,
  measure_date     DATE,
  created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX device_prices_unique_idx
  ON device_prices (device_id, COALESCE(ccn, ''), source);

COMMENT ON TABLE device_prices IS 'Device pricing from MRFs or manufacturer data';
