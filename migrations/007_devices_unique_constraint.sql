-- Add unique constraint on devices.premarket_number
-- Required for ON CONFLICT upserts in fda_devices.py

ALTER TABLE devices
  ADD CONSTRAINT devices_premarket_number_key UNIQUE (premarket_number);
