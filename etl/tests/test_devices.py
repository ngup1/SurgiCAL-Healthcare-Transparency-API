"""Phase 2 extractors, run on saved copies of real openFDA responses."""

import json

from etl.extract import fda_devices, fda_maude, fda_recalls
from etl.extract.device_procedures import load_curated_map
from etl.tests.conftest import load_fixture

PRODUCT_TYPES = fda_devices.build_product_types(load_fixture("fda_classification.json")["results"], set())


def test_product_types_use_roman_classes_and_panel_names():
    assert PRODUCT_TYPES["JWH"]["device_class"] == "II"
    assert PRODUCT_TYPES["JWH"]["specialty"] == "Orthopedic"
    assert PRODUCT_TYPES["JWH"]["name"].startswith("Prosthesis, Knee")


def test_class_iii_types_are_found_through_their_review_panel():
    # Coronary stents have no regulation of their own: medical_specialty is empty.
    stent = {"product_code": "NIQ", "device_name": "Coronary Drug-Eluting Stent", "device_class": "3",
             "medical_specialty": "", "review_panel": "CV"}
    hospital_bed = {"product_code": "FNL", "device_name": "Bed", "device_class": "1", "medical_specialty": "HO"}
    types = fda_devices.build_product_types([stent, hospital_bed], extra_codes=set())
    assert set(types) == {"NIQ"} and types["NIQ"]["specialty"] == "Cardiovascular"
    assert "FNL" in fda_devices.build_product_types([hospital_bed], extra_codes={"FNL"})


def test_510k_generic_name_is_the_product_type_not_the_word_summary():
    raw = load_fixture("fda_510k.json")["results"][0]
    assert raw["statement_or_summary"] == "Summary"
    device = fda_devices.transform_510k(raw, PRODUCT_TYPES)
    assert device["brand_name"] == "GMK Extension"
    assert device["generic_name"].startswith("Prosthesis, Knee")
    assert device["device_class"] == "II"
    assert device["premarket_number"] == "K262057"
    assert "Summary" not in (device["generic_name"], device["description"])


def test_untracked_product_types_are_skipped():
    raw = {**load_fixture("fda_510k.json")["results"][0], "product_code": "ZZZ"}
    assert fda_devices.transform_510k(raw, PRODUCT_TYPES) is None


def test_pma_keeps_the_original_approval_not_its_supplements():
    types = {"NIQ": {"name": "Coronary Drug-Eluting Stent", "device_class": "III", "specialty": "Cardiovascular", "definition": None}}
    rows = load_fixture("fda_pma.json")["results"]
    assert [r["supplement_number"] for r in rows] == ["", "S002"]
    [device] = fda_devices.collect_pma_devices(reversed(rows), types)
    assert device["premarket_number"] == "P160043"
    assert device["brand_name"] == "RESOLUTE ONYX ZOTAROLIMUS-ELUTING CORONARY STENT SYSTEM"
    assert device["device_class"] == "III"


def test_recall_fields():
    raw = load_fixture("fda_recall.json")["results"][0]
    classifications = fda_recalls.build_classifications(load_fixture("fda_enforcement.json")["results"])
    recall = fda_recalls.transform_recall(raw, classifications, known_premarket={"K042515"})
    assert recall["recall_number"] == "Z-0001-2019"  # product_res_number, not the event number
    assert recall["manufacturer"] == "Smith & Nephew, Inc."  # recalling_firm, not the FEI number
    assert recall["recall_class"] == "Class II"  # from the enforcement report
    assert recall["status"] in ("Ongoing", "Terminated", "Completed")
    assert recall["recall_date"] == "2018-06-13"
    assert recall["premarket_number"] == "K042515"
    assert recall["product_code"] == "JWH"


def test_recall_without_an_enforcement_report_has_no_class_and_keeps_its_status():
    raw = {**load_fixture("fda_recall.json")["results"][0], "recall_status": "Terminated"}
    recall = fda_recalls.transform_recall(raw, {}, known_premarket=set())
    assert recall["recall_class"] == fda_recalls.CLASS_NOT_AVAILABLE
    assert recall["status"] == "Terminated"


def test_recall_prefers_a_premarket_number_we_track():
    raw = {"k_numbers": ["K000001", "K000002"], "pma_numbers": ["P000003"]}
    assert fda_recalls.pick_premarket_number(raw, {"P000003"}) == "P000003"
    assert fda_recalls.pick_premarket_number(raw, set()) == "K000001"
    assert fda_recalls.pick_premarket_number({}, set()) is None


def test_adverse_event_fields():
    raw = load_fixture("fda_event.json")["results"][0]
    event = fda_maude.transform_event(raw, "JWH")
    assert event["mdr_report_key"] == "50941450"
    assert event["event_type"] == "injury"
    assert event["event_date"] == "2026-09-16"
    assert json.loads(event["device_problems"]) == ["Unstable"]  # product_problems, not product codes
    assert json.loads(event["patient_outcomes"]) == ["Required Intervention"]
    assert event["manufacturer"] == "Encore Medical L.P"
    assert event["premarket_number"] == "K140830"
    assert event["product_code"] == "JWH"


def test_adverse_events_need_one_request_per_code_and_a_404_is_empty():
    class Client:
        def __init__(self):
            self.calls = []

        def get_json(self, url, params=None, missing_ok=False):
            self.calls.append((params, missing_ok))
            return None  # what RateLimitedClient returns for openFDA's "no matches" 404

    client = Client()
    assert fda_maude.fetch_events("ZZZ", client) == []
    assert client.calls[0][1] is True


def test_curated_procedure_map_is_well_formed():
    rows = load_curated_map()
    assert {"cpt": "27447", "product_code": "JWH", "usage_type": "implant"} in rows
    assert all(r["usage_type"] in ("implant", "instrument", "consumable") for r in rows)
    assert all(len(r["product_code"]) == 3 and len(r["cpt"]) == 5 for r in rows)
    assert len({(r["cpt"], r["product_code"]) for r in rows}) == len(rows)
