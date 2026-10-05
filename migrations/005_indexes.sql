-- Indexes for query performance

-- Spatial indexes for "nearby" queries
CREATE INDEX idx_hospitals_location ON hospitals USING GIST(location);
CREATE INDEX idx_providers_location ON providers USING GIST(location);

-- Hospital lookups
CREATE INDEX idx_hospitals_state ON hospitals(state);

-- Price lookups
CREATE INDEX idx_prices_cpt ON prices(cpt);
CREATE INDEX idx_prices_ccn ON prices(ccn);
CREATE INDEX idx_prices_payer ON prices(payer);

-- Provider lookups
CREATE INDEX idx_providers_specialty ON providers(specialty);
CREATE INDEX idx_providers_state ON providers(state);
CREATE INDEX idx_affiliations_npi ON provider_affiliations(npi);
CREATE INDEX idx_affiliations_ccn ON provider_affiliations(ccn);

-- Device lookups
CREATE INDEX idx_devices_product_code ON devices(fda_product_code);
CREATE INDEX idx_devices_manufacturer ON devices(manufacturer);
CREATE INDEX idx_device_recalls_device ON device_recalls(device_id);
CREATE INDEX idx_device_adverse_device ON device_adverse_events(device_id);
CREATE INDEX idx_device_proc_map_cpt ON device_procedure_map(cpt);
CREATE INDEX idx_device_proc_map_device ON device_procedure_map(device_id);

-- Trigram indexes for fuzzy text search
CREATE INDEX idx_hospitals_name_trgm ON hospitals USING GIN(name gin_trgm_ops);
CREATE INDEX idx_providers_name_trgm ON providers USING GIN(
  (first_name || ' ' || last_name) gin_trgm_ops
);
CREATE INDEX idx_devices_brand_trgm ON devices USING GIN(brand_name gin_trgm_ops);
