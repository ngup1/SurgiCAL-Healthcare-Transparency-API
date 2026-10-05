-- California place gazetteer for city / county / ZIP location search.
-- The API resolves a user-supplied place name to a canonical name and centroid here,
-- and rejects places that aren't in California (SurgiCAL covers CA only).
-- Production source: Census Gazetteer files (places, counties, ZCTAs) filtered to CA.

CREATE TABLE ca_places (
  id         SERIAL PRIMARY KEY,
  place_type TEXT NOT NULL CHECK (place_type IN ('city', 'county', 'zip')),
  name       TEXT NOT NULL,
  county     TEXT NOT NULL,
  location   GEOGRAPHY(Point, 4326) NOT NULL,
  UNIQUE (place_type, name)
);

COMMENT ON TABLE ca_places IS 'California cities, counties and ZIP codes with centroids for location search';
COMMENT ON COLUMN ca_places.name IS 'City name, county name without the word County, or 5-digit ZIP';
COMMENT ON COLUMN ca_places.county IS 'County the place belongs to (equals name for county rows)';

-- Case-insensitive exact lookup, and trigram index for "did you mean" suggestions.
CREATE INDEX idx_ca_places_lower_name ON ca_places (place_type, lower(name));
CREATE INDEX idx_ca_places_name_trgm ON ca_places USING GIN (name gin_trgm_ops);

-- /search matches procedure descriptions by trigram word similarity.
CREATE INDEX IF NOT EXISTS idx_cpt_codes_description_trgm ON cpt_codes USING GIN (description gin_trgm_ops);
