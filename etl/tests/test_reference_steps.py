"""Phase 1 extractors, run on saved copies of real CMS, NPPES and Census responses."""

import json

import pytest

from etl.common.places import fold
from etl.extract import census_places, cms_hospital_quality, cms_hospitals, cms_physicians, cms_utilization
from etl.tests.conftest import load_fixture

# --- places ---------------------------------------------------------------


def test_fold_matches_spellings_of_one_city():
    assert fold("LA CANADA FLINTRIDGE") == fold("La Cañada Flintridge")
    assert fold("Saint Helena") == fold("St. Helena")


def test_city_names_use_the_gazetteer_spelling(places):
    assert places.city_name("HAYWARD") == "Hayward"
    assert places.city_name("MCKINLEYVILLE") == "McKinleyville"
    assert places.city_name("VAN NUYS") == "Van Nuys"  # not in the gazetteer: title-cased
    assert places.city_name("  ") is None


def test_locate_uses_the_zip_then_the_city(places):
    assert places.locate("94545-1234") == "SRID=4326;POINT(-122.118 37.634)"
    assert places.locate("00000", "HAYWARD") == "SRID=4326;POINT(-122.1 37.63)"
    assert places.locate("00000", "Nowhere") is None


def test_census_city_records_strip_suffixes_and_prefer_incorporated_places():
    counties = census_places.build_counties([
        {"GEOID": "06085", "NAME": "Santa Clara County", "INTPTLAT": "37.22", "INTPTLONG": "-121.69"},
        {"GEOID": "06013", "NAME": "Contra Costa County", "INTPTLAT": "37.92", "INTPTLONG": "-121.95"},
    ])
    place_rows = [
        {"GEOID": "0649651", "NAME": "Mountain View CDP", "FUNCSTAT": "S", "ALAND": "9", "INTPTLAT": "38.0", "INTPTLONG": "-122.1"},
        {"GEOID": "0649670", "NAME": "Mountain View city", "FUNCSTAT": "A", "ALAND": "5", "INTPTLAT": "37.4", "INTPTLONG": "-122.08"},
    ]
    place_county_rows = [
        {"STATEFP": "06", "PLACEFP": "49651", "COUNTYFP": "013"},
        {"STATEFP": "06", "PLACEFP": "49670", "COUNTYFP": "085"},
    ]
    records = census_places.build_city_records(place_rows, place_county_rows, counties)
    assert records == [
        {"place_type": "city", "name": "Mountain View", "county": "Santa Clara", "location": "SRID=4326;POINT(-122.08 37.4)"}
    ]


def test_census_zip_records_keep_zips_whose_largest_part_is_in_the_state():
    counties = {"06001": {"name": "Alameda", "point": (37.6, -121.9)}}
    zcta_rows = [
        {"GEOID": "94545", "INTPTLAT": "37.63", "INTPTLONG": "-122.1"},
        {"GEOID": "89439", "INTPTLAT": "39.5", "INTPTLONG": "-120.0"},
    ]
    relations = [
        {"GEOID_ZCTA5_20": "94545", "GEOID_COUNTY_20": "06001", "AREALAND_PART": "100"},
        {"GEOID_ZCTA5_20": "89439", "GEOID_COUNTY_20": "06001", "AREALAND_PART": "10"},
        {"GEOID_ZCTA5_20": "89439", "GEOID_COUNTY_20": "32031", "AREALAND_PART": "900"},
        {"GEOID_ZCTA5_20": "", "GEOID_COUNTY_20": "06001", "AREALAND_PART": "5"},
    ]
    assert [r["name"] for r in census_places.build_zip_records(zcta_rows, relations, counties)] == ["94545"]


# --- hospitals ------------------------------------------------------------


def test_hospital_fields_come_from_the_names_cms_uses(places):
    raw = load_fixture("cms_hospitals.json")["results"][0]
    record = cms_hospitals.transform_hospital(raw, places)
    assert record == {
        "ccn": "050002",
        "name": "ST ROSE HOSPITAL",
        "address": "27200 CALAROGA AVE",
        "city": "Hayward",  # CMS sends HAYWARD in `citytown`
        "state": "CA",
        "zip": "94545",
        "phone": "(510) 782-6200",  # `telephone_number`
        "hospital_type": "acute_care",
        "ownership": "voluntary_nonprofit",
        "emergency_services": False,
        "location": "SRID=4326;POINT(-122.118 37.634)",  # ZIP centre point
    }


def test_star_rating_and_groups_come_from_the_general_information_row():
    raw = load_fixture("cms_hospitals.json")["results"][0]
    assert cms_hospitals.transform_rating(raw) == {
        "ccn": "050002",
        "overall_stars": 1.0,
        "mortality_group": "same",  # 0 better, 0 worse
        "safety_group": "same",
        "readmission_group": "below",  # 0 better, 2 worse
    }


def test_rating_not_available_is_null():
    rating = cms_hospitals.transform_rating({"facility_id": "050999", "hospital_overall_rating": "Not Available"})
    assert rating["overall_stars"] is None and rating["mortality_group"] is None


# --- quality --------------------------------------------------------------


def test_quality_uses_the_right_datasets_and_measure_ids():
    assert cms_hospital_quality.DATASET_MEASURES["ynj2-r877"]["PSI_90"] == "psi90_composite"
    assert cms_hospital_quality.DATASET_MEASURES["632h-zaca"]["Hybrid_HWR"] == "readmission_hosp_wide"
    assert cms_hospital_quality.HAI_DATASET == "77hc-ibv8"


def test_quality_rows_merge_into_one_record_per_hospital():
    measure_rows = {
        dataset: load_fixture(f"cms_quality_{dataset}.json")["results"]
        for dataset in cms_hospital_quality.DATASET_MEASURES
    }
    hai_rows = [
        *load_fixture("cms_quality_77hc-ibv8.json")["results"],  # HAI_1_SIR: Not Available
        {"facility_id": "050002", "measure_id": "HAI_6_SIR", "score": "0.563", "end_date": "09/30/2025"},
        {"facility_id": "999999", "measure_id": "HAI_6_SIR", "score": "1.0", "end_date": "09/30/2025"},
    ]
    [record] = cms_hospital_quality.merge_quality({"050002"}, measure_rows, hai_rows)
    assert record["psi90_composite"] == 1.02
    assert record["readmission_hip_knee"] == 5.4
    assert json.loads(record["hai_sirs"]) == {"cdi": 0.563}
    assert record["measure_period"] == "2025Q3"
    assert record["mortality_cabg"] is None


def test_hospital_without_any_score_gets_no_record():
    rows = {"ynj2-r877": [{"facility_id": "050002", "measure_id": "PSI_90", "score": "Not Available"}], "632h-zaca": []}
    assert cms_hospital_quality.merge_quality({"050002"}, rows, []) == []


# --- providers ------------------------------------------------------------


class FakeNppes:
    """Stands in for RateLimitedClient: serves pages of search results."""

    def __init__(self, respond):
        self.respond = respond
        self.calls: list[dict] = []

    def get_json(self, url, params=None, **kwargs):
        self.calls.append(params)
        return self.respond(params)


def test_unknown_specialty_name_is_an_error_not_an_empty_result():
    # "Orthopedic Surgery" (NPPES spells it "Orthopaedic") comes back as HTTP 200 with an Errors body.
    client = FakeNppes(lambda params: load_fixture("nppes_error.json"))
    with pytest.raises(cms_physicians.NppesError, match="Orthopedic Surgery"):
        cms_physicians.search_page(client, "Orthopedic Surgery", "CA", None, 0)


def test_search_names_are_the_spellings_nppes_accepts():
    assert "Orthopaedic Surgery" in cms_physicians.SEARCH_DESCRIPTIONS
    assert "Colon & Rectal Surgery" in cms_physicians.SEARCH_DESCRIPTIONS
    for dropped in ("Diagnostic Radiology", "Nephrology", "Endocrinology", "Infectious Disease"):
        assert dropped not in cms_physicians.SEARCH_DESCRIPTIONS


def test_surgical_taxonomies_cover_the_core_specialties():
    for code in ("207X00000X", "207XS0114X", "208600000X", "207V00000X", "207W00000X", "207Y00000X", "208800000X"):
        assert cms_physicians.is_surgical(code), code
    for code in ("2085R0202X", "207RN0300X", "207RE0101X", "207RI0200X", "363LX0001X"):
        assert not cms_physicians.is_surgical(code), code


def test_provider_fields(places):
    raw = load_fixture("nppes.json")["results"][0]
    raw["addresses"] = [
        {"address_purpose": "MAILING", "city": "ORANGE", "state": "CA", "postal_code": "928684203"},
        {"address_purpose": "LOCATION", "city": "HAYWARD", "state": "CA", "postal_code": "945451234"},
    ]
    record = cms_physicians.transform_provider(raw, "CA", places)
    assert record == {
        "npi": "1003437872",
        "first_name": "Mohammed",
        "last_name": "Abbas",
        "credential": "MD",
        "specialty": "Orthopaedic Surgery",
        "taxonomy_code": "207X00000X",
        "gender": "M",  # NPPES calls it `sex`
        "city": "Hayward",
        "state": "CA",
        "location": "SRID=4326;POINT(-122.118 37.634)",
    }
    # Enrichment columns are absent, so a refresh can't overwrite them with NULL.
    assert "medical_school" not in record and "graduation_year" not in record


def test_provider_without_surgical_taxonomy_or_in_state_practice_is_dropped(places):
    raw = load_fixture("nppes.json")["results"][0]
    nurse = {**raw, "taxonomies": [{"code": "363LX0001X", "desc": "Nurse Practitioner", "primary": True}]}
    assert cms_physicians.transform_provider(nurse, "CA", places) is None
    elsewhere = {**raw, "addresses": [{"address_purpose": "LOCATION", "city": "RENO", "state": "NV"}]}
    assert cms_physicians.transform_provider(elsewhere, "CA", places) is None


def test_general_surgery_is_named():
    assert cms_physicians.specialty_name("208600000X", "Surgery") == "General Surgery"
    assert cms_physicians.specialty_name("2086S0129X", "Surgery, Vascular Surgery") == "General Surgery, Vascular Surgery"
    assert cms_physicians.specialty_name("207XS0106X", "Orthopaedic Surgery, Hand Surgery") == "Orthopaedic Surgery, Hand Surgery"


def test_search_that_hits_the_cap_is_split_by_zip_prefix():
    full_page = [{"number": i} for i in range(cms_physicians.PAGE_SIZE)]

    def respond(params):
        if "postal_code" not in params:
            return {"results": full_page}  # every page full: more than the cap
        return {"results": [{"number": params["postal_code"]}] if params["skip"] == 0 else []}

    client = FakeNppes(respond)
    results = list(cms_physicians.search_all(client, "Ophthalmology", "CA", ["90", "94"]))
    assert results == [{"number": "90*"}, {"number": "94*"}]
    assert [c["skip"] for c in client.calls[:6]] == [0, 200, 400, 600, 800, 1000]


# --- utilization ----------------------------------------------------------


def test_utilization_reads_the_capitalised_keys():
    rows = load_fixture("cms_utilization.json")
    providers, procedures = cms_utilization.aggregate_utilization(rows, {"1003221078"}, {"27447": 19.6}, 2024)
    assert procedures == [{
        "npi": "1003221078", "cpt": "27447", "total_services": 51, "total_beneficiaries": 49,
        "total_payment": 7246.33, "wrvu_estimate": 999.6, "measure_year": 2024,
    }]
    assert providers[0]["volume_bucket"] == "MEDIUM"


def test_utilization_combines_place_of_service_and_drops_codes_not_in_cpt_codes():
    def row(code, place, services):
        return {"Rndrng_NPI": "1", "HCPCS_Cd": code, "Place_Of_Srvc": place, "Tot_Benes": "10",
                "Tot_Srvcs": services, "Avg_Mdcr_Pymt_Amt": "100"}

    rows = [row("27447", "F", "30"), row("27447", "O", "12"), row("J1100", "O", "5000"), row("G0463", "O", "40")]
    providers, procedures = cms_utilization.aggregate_utilization(rows, {"1"}, {"27447": 20.0, "99213": None}, 2024)
    # One row for the procedure (two would break the upsert), and no J- or G-codes (not in cpt_codes).
    assert [(p["cpt"], p["total_services"], p["total_payment"]) for p in procedures] == [("27447", 42, 4200.0)]
    assert providers == [{
        "npi": "1", "total_medicare_services": 42, "total_medicare_beneficiaries": 20,
        "total_medicare_payment": 4200.0, "wrvu_estimate": 840.0, "volume_bucket": "LOW",
    }]
