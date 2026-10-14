"""Finding price files in cms-hpt.txt and matching them to hospitals."""

from etl.extract.mrf_links import header_numbers, match_ccn, name_similarity, parse_cms_hpt

HOSPITALS = {
    "050002": {"name": "ST ROSE HOSPITAL", "address": "27200 CALAROGA AVE", "zip": "94545"},
    "050006": {"name": "PROVIDENCE ST JOSEPH HOSPITAL", "address": "2700 DOLBEER ST", "zip": "95501"},
    "050069": {"name": "PROVIDENCE ST. JOSEPH HOSPITAL", "address": "1100 WEST STEWART DR", "zip": "92868"},
    "050348": {"name": "UCI HEALTH-ORANGE", "address": "101 THE CITY DRIVE SOUTH", "zip": "92868"},
    "050510": {"name": "KAISER FOUNDATION HOSPITAL", "address": "99 MONTECILLO ROAD", "zip": "94903"},
}
STATE_ZIPS = {"94545", "95501", "92868", "94903", "90095"}

CMS_HPT = """\
location-name: Ronald Reagan UCLA Medical Center
source-page-url: https://www.uclahealth.org/price-transparency
mrf-url: https://www.uclahealth.org/files/956006143_ronald-reagan_standardcharges.json
contact-name: Media Relations
contact-email: news@example.org

Location-Name: UCLA Santa Monica Medical Center
MRF-URL: https://www.uclahealth.org/files/954541687_santa-monica_standardcharges.json
mrf-url: https://www.uclahealth.org/files/954541687_santa-monica_professional.csv
mrf-url: not a url
"""


def test_parse_cms_hpt_reads_every_file_of_every_location():
    assert parse_cms_hpt("﻿" + CMS_HPT) == [
        {"location_name": "Ronald Reagan UCLA Medical Center",
         "mrf_url": "https://www.uclahealth.org/files/956006143_ronald-reagan_standardcharges.json"},
        {"location_name": "UCLA Santa Monica Medical Center",
         "mrf_url": "https://www.uclahealth.org/files/954541687_santa-monica_standardcharges.json"},
        {"location_name": "UCLA Santa Monica Medical Center",
         "mrf_url": "https://www.uclahealth.org/files/954541687_santa-monica_professional.csv"},
    ]


def test_parse_cms_hpt_ignores_html_error_pages():
    assert parse_cms_hpt("<html><body>Page not found</body></html>") == []


def test_header_numbers_reads_only_the_hospital_description():
    csv_header = (
        b"hospital_name,last_updated_on,version,hospital_address\n"
        b'Hayward Sisters Hospital,2026-02-23,3.0.0,"27200 Calaroga Ave Hayward Ca  94545"\n'
        b"description,code|1,code|1|type\nKNEE,27447,CPT\n"
    )
    assert header_numbers(csv_header) >= {"27200", "94545"}
    assert "27447" not in header_numbers(csv_header)

    json_header = b'{"hospital_address": ["757 Westwood Plaza, Los Angeles, CA 90095"], "standard_charge_information": [{"code": "27447"}]}'
    assert {"757", "90095"} <= header_numbers(json_header)
    assert "27447" not in header_numbers(json_header)

    assert header_numbers(None) is None
    assert header_numbers(b"PK\x03\x04zipped") is None


def test_name_similarity_ignores_generic_words():
    assert name_similarity("St Rose Hospital", "ST ROSE HOSPITAL") == 1.0
    assert name_similarity("San Joaquin Community Hospital", "Good Samaritan Hospital") < 0.5


def test_out_of_state_file_matches_nothing():
    # A system's index lists its hospitals in every state; this one is in Washington.
    assert match_ccn("Providence St Joseph Hospital", {"98225", "2901"}, HOSPITALS, STATE_ZIPS) is None


def test_same_name_in_two_cities_is_settled_by_the_address():
    name = "Providence St Joseph Hospital"
    assert match_ccn(name, {"1100", "92868"}, HOSPITALS, STATE_ZIPS) == "050069"
    assert match_ccn(name, {"2700", "95501"}, HOSPITALS, STATE_ZIPS) == "050006"


def test_a_similar_name_at_another_address_is_not_a_match():
    # Providence Saint John's (Santa Monica) reads almost like Providence St Joseph.
    assert name_similarity("Providence Saint John's Health Center", "PROVIDENCE ST JOSEPH HOSPITAL") >= 0.8
    assert match_ccn("Providence Saint John's Health Center", {"2121", "90404"}, HOSPITALS, STATE_ZIPS | {"90404"}) is None


def test_another_hospitals_file_with_a_shared_billing_address_is_not_a_match():
    # The file is named for the Eureka hospital but carries the Orange hospital's address.
    hospitals = {**HOSPITALS, "050570": {"name": "UCI HEALTH - FOUNTAIN VALLEY", "address": "17100 EUCLID ST", "zip": "92708"}}
    assert match_ccn("UCI Health - Fountain Valley", {"101", "92868"}, hospitals, STATE_ZIPS) is None
    assert match_ccn("UCI Health - Fountain Valley", {"17100", "92708"}, hospitals, STATE_ZIPS | {"92708"}) == "050570"


def test_unreadable_file_with_an_ambiguous_name_matches_nothing():
    assert match_ccn("Providence St Joseph Hospital", None, HOSPITALS, STATE_ZIPS) is None


def test_address_matches_when_the_name_does_not():
    # Kaiser names its file after the city; CMS calls it Kaiser Foundation Hospital.
    assert match_ccn("San Rafael Medical Center", {"99", "94903"}, HOSPITALS, STATE_ZIPS) == "050510"
    assert match_ccn("Hayward sisters hospital dba st rose hospital", {"27200", "94545"}, HOSPITALS, STATE_ZIPS) == "050002"


def test_sharing_a_zip_code_is_not_enough():
    # Another facility in Orange's 92868, at neither hospital's street number.
    assert match_ccn("Childrens Clinic of Orange", {"455", "92868"}, HOSPITALS, STATE_ZIPS) is None


def test_unreadable_file_needs_a_near_exact_name():
    assert match_ccn("St. Rose Hospital", None, HOSPITALS, STATE_ZIPS) == "050002"
    assert match_ccn("Rose Garden Surgery Center", None, HOSPITALS, STATE_ZIPS) is None
