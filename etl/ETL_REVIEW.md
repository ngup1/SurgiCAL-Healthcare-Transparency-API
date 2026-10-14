# ETL Review and Refresh Plan

_Reviewed 2026-10-05._

**Method.** All 18 files in `etl/` were read in full. Items marked ✓ were confirmed in one of two ways:
- by calling the live CMS, NPPES and openFDA APIs;
- by reproducing the bug on the local Postgres, inside a transaction that was rolled back.

Unmarked items come from reading the code.

> **Update, 2026-10-13: the plan below has been carried out** (branch `etl-refresh`). See
> [Implementation status](#implementation-status) at the end for what changed, what was
> verified against live sources, and what is still open. The findings below describe the
> code as it was on 2026-10-05.

**Bottom line (2026-10-05):** the pipeline as written would not produce a usable real dataset. Of the 10 steps that need no Trilliant or Oria credentials, only `cpt_codes` works correctly. The FDA steps would take days, utilization would load nothing, and most location and quality columns would be empty.

---

## Critical: steps that load nothing, crash, or never finish

| # | Where | Problem | Effect |
|---|---|---|---|
| 1 | `etl/common/http.py:38` | The retry wrapper retries every error, including 404. openFDA returns 404 for "no results" ✓, so each empty lookup waits about 60 s through 5 retries. | Most device product types have no PMA approvals or recalls. With about 2,000 product types, `fda_devices` and `fda_recalls` each spend more than a day in retry waits. |
| 2 | All openFDA steps | No API key. Without one, openFDA allows about 1,000 requests a day, and `fda_devices` alone needs about 4,000. | Requests past the cap fail, get caught, and the device data is silently incomplete. |
| 3 | `etl/extract/cms_utilization.py:99-106` | The code reads lowercase keys (`rndrng_npi`), but the API returns `Rndrng_NPI`, `HCPCS_Cd` and so on ✓. | Every row's NPI is blank, so nothing matches. The step pages through all 9.78M national rows first (about 9,800 requests) ✓. Filtering on CA cuts that to 832k rows ✓. |
| 4 | `etl/extract/cms_utilization.py:128` | Rows aren't combined across place of service (facility vs. office), so the same NPI and CPT appear twice ✓. Codes like G- and J-codes, which aren't in `cpt_codes`, are kept too. | Once #3 is fixed, the upsert fails with `ON CONFLICT DO UPDATE command cannot affect row a second time` ✓. If it got past that, the codes missing from `cpt_codes` would fail the foreign-key check. |
| 5 | `etl/extract/cms_physicians.py:25-48` | NPPES searches by description, and 5 of the 22 names don't match ✓ (see below). The error response has no `results`, so each search quietly returns nothing. Some surgical specialties are missing and some non-surgical ones are included. | No general orthopedic surgeons are loaded. That's the core specialty for knee replacement (CPT 27447), the API's showcase procedure. |
| 6 | `etl/enrich/trilliant.py:553` | It upserts providers with only `npi` and `medical_school`, but Postgres checks NOT NULL columns before resolving the conflict ✓. Separately, `upsert_batch` takes its column list from the first record (`etl/common/db.py:36`), and Trilliant records don't all have the same fields, so it can fail with a `KeyError`. | Trilliant enrichment can never succeed. |
| 7 | `etl/extract/mrf_parser.py:209-223` | It looks for `in_network`, which is the insurer (Transparency in Coverage) file format. Hospital JSON price files use `standard_charge_information`. | Every hospital that publishes JSON contributes zero prices. |
| 8 | `etl/extract/mrf_parser.py:159` | `MAX_FILE_BYTES` is documented but never defined. The whole file is held in memory and copied twice, then processed with pandas `iterrows`. | Multi-GB hospital files run out of memory or take hours each. |
| 9 | `etl/extract/mrf_parser.py:975` | The price upsert isn't wrapped in a try/except for each hospital. | One unusual `last_updated_on` date in one hospital's file aborts the entire prices step. |

**The NPPES name problem (#5) in detail:**
- **Names that return an error** ✓: "Orthopedic Surgery" (NPPES spells it "Orthopaedic"), "Orthopedic Surgery of the Spine", "Hand Surgery (Orthopedic)", "Colon and Rectal Surgery", "Urology - Female Pelvic Medicine".
- **Missing specialties:** General Surgery, OB/GYN, Ophthalmology and Otolaryngology (ENT).
- **Included but not surgical:** Diagnostic Radiology, Nephrology, Endocrinology and Infectious Disease.

## High: data loads but is wrong

| Where | Problem |
|---|---|
| `etl/extract/cms_hospitals.py:65,68` | The code reads `city`/`city_town` and `phone_number`, but the API returns `citytown` and `telephone_number` ✓. Every hospital gets a blank city and no phone. |
| `etl/extract/cms_hospitals.py:74` | The CMS hospital dataset has no coordinates ✓, so `location` is always NULL and radius search can't work. City filtering also needs the casing fixed: CMS returns "HAYWARD", and the API matches against "Hayward". |
| `etl/extract/cms_hospitals.py:85` | `hospital_type` comes out as `acute_care_hospitals`; the API and seed data use `acute_care`. |
| `etl/extract/cms_hospital_quality.py` | Wrong dataset or measure IDs ✓ (see below). The `*_national_comparison` fields no longer exist ✓. Only star ratings, hip/knee complications and CABG mortality would load. |
| `etl/extract/cms_hospital_quality.py:79` | Star ratings come from a single unpaginated GET. It covers CA today only because CA facility IDs sort early in the 1,500-row default page ✓. |
| `etl/extract/fda_devices.py:123,131` | `generic_name` and `description` are filled from `statement_or_summary`, which holds the literal word "Summary" ✓. |
| `etl/extract/fda_devices.py:125` | Device class comes back as `"2"`, while PMA records and the API use `"II"`/`"III"` ✓. |
| `etl/extract/fda_recalls.py:63` | `manufacturer` is filled from `firm_fei_number`, which is a numeric ID ✓. It should use `recalling_firm`. |
| `etl/extract/fda_recalls.py:64` | `recall_class` is computed from `event_date_terminated` (a date). This endpoint has no recall-class field ✓; it is in openFDA's `device/enforcement` endpoint. Every recall ends up "II". |
| `etl/extract/fda_recalls.py:66` | It reads `status`, but the field is `recall_status` ✓, so every recall shows "ongoing". |
| `etl/extract/fda_recalls.py:48-50` | It keys on `res_event_number` (one per recall event) instead of `product_res_number` (one per product), so only the first product in each event is kept. |
| `etl/extract/fda_maude.py:74-76` | `device_problems` stores product codes instead of the top-level `product_problems` ✓. |
| Recalls and MAUDE | `device_id` is "linked in post-processing", but no such step exists, and nothing fills `device_procedure_map`. With real data, `/devices/{id}/recalls`, `/devices/{id}/adverse-events` and `/devices/by-procedure/{cpt}` return nothing. |
| `etl/extract/mrf_parser.py:518,593` | When there's no discounted cash price, `cash_price` falls back to the gross chargemaster price, which is often 2–5× higher. |
| `etl/extract/mrf_parser.py:488` | The `setting` column (inpatient/outpatient) isn't kept, so both are averaged into one price. |
| `etl/extract/mrf_parser.py:927` | `records is not None` is always true, so links that 404 are marked `active`. |
| `etl/extract/hospitalpricingfiles_scraper.py:57,264` | Hospital names are matched to CCNs by exact string, and `ccn` is excluded from the upsert's update columns, so unmatched links never get a CCN, and the prices step skips links without one. It also relies on solving a CAPTCHA on a third-party site. |
| `etl/extract/cms_physicians.py:128,189` | Re-running `providers` overwrites `medical_school` and `graduation_year` with NULL, wiping the Trilliant enrichment. |

**Correct CMS quality sources:**

| Measure | Code uses | Should use |
|---|---|---|
| PSI-90 composite | `PSI_90_SAFETY` | `PSI_90` in `ynj2-r877` (Complications and Deaths) |
| Infection rates (HAI SIRs) | `yq43-i98g` (HAC Reduction Program, which has no `measure_id`) | `77hc-ibv8` (Healthcare Associated Infections), `HAI_1_SIR` … `HAI_6_SIR` |
| 30-day readmissions | `9n3s-kdb3` (HRRP: measure IDs like `READM-30-HIP-KNEE-HRRP`, no `score`) | `632h-zaca` (Unplanned Hospital Visits): `READM_30_HIP_KNEE`, `READM_30_CABG`. The hospital-wide measure is `Hybrid_HWR`, not `READM_30_HOSP_WIDE` |

## Medium: operational fragility

- **No dependency gating** (`etl/run_pipeline.py`). If `hospitals` fails, every later step still runs against empty tables and reports "ok, 0 records".
- **Partial results are swallowed.** Utilization breaks out of its loop on any error and loads whatever it has (`cms_utilization.py:144`), and the JSON price stream returns partial data after an error (`mrf_parser.py:815`). Every FDA fetch catches exceptions and returns `[]`.
- **Each table loads in one transaction** (`upsert_batch`). A crash after hours of fetching saves nothing.
- **Rate limiting doesn't work for recalls.** `fetch_json` creates a new HTTP client per request, which resets the delay timer. Utilization makes the same mistake once per page.
- **`--steps trilliant` on its own always skips.** `run_pipeline` checks the environment variable before anything has loaded `etl/.env`.
- **Old prices are never removed**, so a payer a hospital dropped stays in the data forever.
- **Hardcoded values.** The utilization dataset ID and `MEASURE_YEAR` are hardcoded, and 2024 data is now out (`335e5f35-eca6-482d-87b3-f99883e213e3`) ✓. `'CA'` is hardcoded in several modules even though `--state` exists.
- **NPPES page cap.** It stops at 2,000 per specialty, while the comment says about 900. Several broad specialties exceed that in CA ✓.
- **Missing dependencies.** `etl/requirements.txt` lacks `playwright` and `requests`.
- **No tests, and CI doesn't cover `etl/`.**

---

## Plan to fix and refresh the ETL

### Phase 0: Foundations (do first; everything else depends on it)

1. **Point the ETL at Neon.**
   - Support `DATABASE_URL`.
   - Use the direct (non-`-pooler`) endpoint, since each table load is one long transaction.
   - Load into a **new Neon branch**, not the database that serves the demo's fictional seed data.
2. **Fix the HTTP layer.**
   - Retry only on 429, 5xx and timeouts.
   - Treat openFDA 404 as an empty result.
   - Use one client per step so rate limiting actually applies.
   - Add an `OPENFDA_API_KEY` setting.
3. **Fix `upsert_batch`.**
   - Use the same set of columns for every record.
   - De-duplicate on the conflict key before inserting.
   - Commit per chunk instead of once per table.
   - Add an update-only path for enrichment (`UPDATE … FROM (VALUES …)`).
4. **Fix `run_pipeline`.**
   - Stop dependent steps when a required step fails.
   - Treat a 0-row result as a warning or failure.
   - Load `.env` first.
   - Record each run's row counts in an `etl_runs` table.
5. **Add tests and CI.** Build tests from saved copies of real API responses, and run them in CI alongside the API tests.
6. **Drop Oria and `boto3`. Make Trilliant opt-in,** fixed to update existing rows only.

### Phase 1: Reference data (hospitals, CPT codes, providers, quality, utilization)

- **Hospitals:**
  - Correct the field names (`citytown`, `telephone_number`).
  - Title-case the city.
  - Map `hospital_type` to the API's values.
  - Geocode from the ZIP centre points in `ca_places`.
  - Fold the star ratings in here.
- **Quality:** switch to the correct datasets and measure IDs in the table above.
- **Providers:**
  - Use the exact NPPES spellings.
  - Add the missing surgical specialties and drop the non-surgical ones.
  - Consider NPPES's monthly bulk file instead of paging the API.
  - Geocode by ZIP.
  - Stop overwriting enrichment fields.
- **Utilization:**
  - Read the capitalized keys.
  - Filter to CA on the server (`filter[Rndrng_Prvdr_State_Abrvtn]=CA`).
  - Combine rows across place of service.
  - Keep only codes in `cpt_codes`.
  - Make the dataset ID and year configurable, and move to 2024.

### Phase 2: Devices

- **Fix the field mappings:**
  - 510(k): `generic_name`, and device class as roman numerals.
  - Recalls: `product_res_number`, `recalling_firm`, `recall_status`, and recall class from `device/enforcement`.
  - MAUDE: `product_problems`.
- **Fill in `device_id`.** Recalls carry `k_numbers`/`pma_numbers`, which match `devices.premarket_number`. MAUDE can fall back to matching on product code.
- **Add a curated CPT-to-product-type table** (for example, 27447 to knee implant product types). That fills `device_procedure_map` and makes `/devices/by-procedure/{cpt}` work.

### Phase 3: Prices (the most work, and what decides the storage size)

- **Find price files through each hospital's `cms-hpt.txt` file** (required since 2024) instead of the CAPTCHA scraper. Match files to CCNs by name and address, with a manual overrides table.
- **Download and parse safely.**
  - Write downloads to disk with a real size cap.
  - Read CSVs in chunks, or with DuckDB.
  - Stream-parse the hospital JSON format's `standard_charge_information`.
- **Change the schema.**
  - Add `setting` and `gross_charge` columns to `prices`.
  - Stop filling `cash_price` from the gross price.
  - Add a table that normalizes payer names.
- **Control the size.** Limit the procedures loaded, and store one row per payer instead of one per plan if storage is tight. Measure with `--mrf-limit 10` and `pg_total_relation_size('prices')` before a full load.
- **Isolate failures and stale data.** Give each hospital its own transaction, and replace that hospital's prices on every refresh so old rows don't pile up.

### Phase 4: Scheduled refresh

**Where it runs:** scheduled GitHub Actions workflows (free, 6-hour limit per job), each source on its own cadence:

| Source | Cadence |
|---|---|
| openFDA recalls and adverse events (MAUDE) | weekly |
| openFDA 510(k)/PMA | monthly |
| NPPES providers | monthly |
| Hospital price files | monthly, split across several jobs |
| CMS hospitals, quality, fee schedule (RVU) | quarterly |
| Medicare utilization | yearly |

**How each run works:**
- Load into a Neon branch.
- Run row-count checks; fail if a table shrinks by more than about 20%.
- If the checks pass, promote the branch.
- A failed workflow emails the repo owner.
- Every run checks storage against the 1 GB free-tier limit.

---

## Open decisions

1. **Storage budget.** Stay within Neon's free 1 GB, or pay for the full price data? Neon's Launch plan charges $0.35/GB-month, roughly $4–7/month for 10–20 GB. This sets the Phase 3 scope.
2. **Trilliant.** Keep it or drop it? The Delta Sharing token has probably expired.
3. **Procedure-to-device mapping.** Curate it by hand for the procedures the API features?

---

## Implementation status

_2026-10-13, branch `etl-refresh`._ Every step was run against the live sources into a scratch
Postgres (`surgical_etl` on the compose database); nothing has been loaded into Neon yet.

| Phase | State | Verified by |
|---|---|---|
| 0 Foundations | Done | 82 ETL tests; pipeline runs |
| 1 Reference data | Done | Full CA load: 3,480 places, 377 hospitals (all geocoded), 27,869 providers, 13,964 provider metrics, 150,781 procedure-volume rows |
| 2 Devices | Done | Full load: 89,107 devices, 32,324 recalls (23,484 linked to a device), 19,318 procedure links. Adverse events run for 60 product codes only |
| 3 Prices | Done; coverage partial | Link discovery for all of CA (about 170 hospitals matched); prices loaded for a sample of hospitals |
| 4 Scheduled refresh | Written, not run | Workflow parses; `neonctl` commands checked against the CLI's help. Needs a Neon API key to run |

**What differs from the plan**

- **openFDA: bulk files instead of the API.** 510(k), PMA, classification, recall and enforcement data
  come from openFDA's zipped downloads: five files, no API key, about a minute, instead of roughly
  4,000 rate-limited searches. Only adverse events (MAUDE, 26M reports) still use the API, at 25 recent
  reports per product code; that step is the one that wants `OPENFDA_API_KEY`.
- **Devices are not capped at 100 per product code**; all 510(k)/PMA devices in the surgical panels are
  loaded, which is what lets three quarters of recalls link to a device.
- **A `places` step was added.** Geocoding and the API's city/county search need the real Census
  gazetteer; `ca_places` only held the mock one. Postal city names the Census doesn't list
  (Van Nuys, La Jolla) are added from the hospital and provider data.
- **NPPES is still paged, not bulk.** A search stops at 1,200 results, so large specialties are split
  by ZIP prefix. The whole state takes under a minute, so the 1 GB monthly file wasn't needed.
- **Star-rating groups are derived.** CMS dropped the `*_national_comparison` fields; mortality,
  safety and readmission groups now come from its better/worse measure counts. Patient-experience
  and timely-care groups have no such counts and stay NULL.
- **The API returns `setting` and `gross_charge`** on price rows, since inpatient and outpatient are
  now separate rows.
- **Removed:** Oria (`oria_*.py`), `boto3`, `duckdb`, `remotezip`, the CAPTCHA scraper and
  `seleniumbase`/Playwright. Trilliant is opt-in (`--steps trilliant`) and update-only.

**Still open**

1. **Price-file coverage.** `etl/reference/hospital_websites.csv` has a website for 258 of 377
   hospitals (bootstrapped from the 2022 TPAFS list); about 170 end up with a matched price file.
   The rest need a website added there, or a row in `mrf_overrides.csv`.
2. **Price-file matching is by address, then name,** and a few matches are doubtful where a health
   system puts one billing address in every file. Worth a spot check before publishing prices.
3. **Storage.** Without prices and adverse events the database is about 160 MB. Prices run about
   325 bytes a row and 1,500 rows a hospital at the default `MRF_CPT_LIMIT=150`, one row per payer:
   roughly 85 MB for 170 hospitals. The free 1 GB has room for more codes or per-plan rows.
4. **`readmission_hosp_wide` is NULL for every hospital:** CMS currently publishes `Hybrid_HWR` as
   "Not Available" for all of California.
5. **About a third of recalls have no class** (`Not available`): enforcement reports, the only source
   of the class, start in 2012.
6. **Provider-hospital affiliations are not loaded** by any step (they never were). CMS's Facility
   Affiliation dataset would fill `provider_affiliations`.
7. **Line-item prices.** Some hospitals attach a surgery's CPT code to supplies or per-unit charges,
   so a few "knee replacement" prices are implausibly low. No filter for that yet.
