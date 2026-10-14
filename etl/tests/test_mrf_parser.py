"""The price-file parser, on small files in each layout of the CMS template."""

import gzip
import json
import zipfile
from datetime import date

import pytest

from etl.extract import mrf_parser
from etl.extract.mrf_parser import PayerNames, PriceBook, load_payer_aliases, parse_file, parse_measure_date

TARGETS = {"27447", "29881"}
GENERAL = (
    "hospital_name,last_updated_on,version,hospital_location,hospital_address\n"
    'Test Hospital,2026-02-23,3.0.0,Test Hospital,"1 Main St, Hayward, CA 94545"\n'
)

TALL_CSV = GENERAL + (
    "description,code|1,code|1|type,code|2,code|2|type,modifiers,setting,billing_class,standard_charge|gross,"
    "standard_charge|discounted_cash,payer_name,plan_name,standard_charge|negotiated_dollar,"
    "standard_charge|negotiated_percentage,median_amount,standard_charge|min,standard_charge|max\n"
    'KNEE REPLACEMENT,470,MS-DRG,27447,CPT,,inpatient,facility,"90,000.00",45000,AETNA HEALTH OF CA,PPO,25000,,,18000,61000\n'
    "KNEE REPLACEMENT,470,MS-DRG,27447,CPT,,inpatient,facility,90000,45000,Aetna,HMO,21000,,,18000,61000\n"
    "KNEE REPLACEMENT,470,MS-DRG,27447,CPT,,inpatient,facility,90000,45000,BLUE SHIELD OF CA,PPO,,55,30000,18000,61000\n"
    "KNEE REPLACEMENT,,,27447,CPT,,outpatient,facility,70000,,Aetna,PPO,19000,,,15000,40000\n"
    "KNEE REPLACEMENT BILATERAL,,,27447,CPT,50,outpatient,facility,140000,,Aetna,PPO,38000,,,,\n"
    "ASPIRIN,,,00000000001,NDC,,outpatient,facility,5,2,Aetna,PPO,1,,,1,3\n"
)

WIDE_CSV = GENERAL + (
    "description,code|1,code|1|type,setting,standard_charge|gross,standard_charge|discounted_cash,"
    "standard_charge|min,standard_charge|max,"
    "standard_charge|Cigna|Open Access|negotiated_dollar,standard_charge|Cigna|Open Access|negotiated_percentage,"
    "median_amount|Cigna|Open Access,"
    "standard_charge | United Healthcare | Choice Plus | negotiated_dollar,median_amount | United Healthcare | Choice Plus\n"
    "KNEE ARTHROSCOPY,29881,CPT,outpatient,12000,,3000,9000,4000,,,,5200.50\n"
)

JSON_FILE = {
    "hospital_name": "Test Hospital",
    "last_updated_on": "01/15/2026",
    "version": "3.0.0",
    "hospital_address": ["1 Main St, Hayward, CA 94545"],
    "standard_charge_information": [
        {
            "description": "KNEE REPLACEMENT",
            "code_information": [{"code": "470", "type": "MS-DRG"}, {"code": "27447", "type": "CPT"}],
            "standard_charges": [
                {
                    "setting": "inpatient", "gross_charge": 90000, "discounted_cash": 45000,
                    "minimum": 18000, "maximum": 61000,
                    "payers_information": [
                        {"payer_name": "Aetna", "plan_name": "PPO", "standard_charge_dollar": 25000},
                        {"payer_name": "Medi-Cal Managed Care", "plan_name": "HMO",
                         "standard_charge_percentage": 40, "median_amount": 16000.0},
                    ],
                },
                {"setting": "outpatient", "gross_charge": 70000, "modifier_code": ["50"],
                 "payers_information": [{"payer_name": "Aetna", "plan_name": "PPO", "standard_charge_dollar": 1}]},
            ],
        },
        {"description": "ASPIRIN", "code_information": [{"code": "1", "type": "NDC"}],
         "standard_charges": [{"setting": "both", "gross_charge": 5}]},
    ],
}


@pytest.fixture
def payers() -> PayerNames:
    return PayerNames(load_payer_aliases())


def parse(tmp_path, content: bytes, payers, plan_detail=False, name="file") -> list[dict]:
    path = tmp_path / name
    path.write_bytes(content)
    book = PriceBook("050002", payers, plan_detail)
    parse_file(path, book, TARGETS)
    return sorted(book.records(), key=lambda r: (r["cpt"], r["setting"], r["payer"], r["plan_name"]))


def by_key(records):
    return {(r["cpt"], r["setting"], r["payer"], r["plan_name"]): r for r in records}


def test_tall_csv(tmp_path, payers):
    records = by_key(parse(tmp_path, TALL_CSV.encode(), payers))
    assert set(records) == {
        ("27447", "inpatient", "Aetna", ""),
        ("27447", "inpatient", "Blue Shield of California", ""),
        ("27447", "inpatient", "CASH", ""),
        ("27447", "outpatient", "Aetna", ""),
        ("27447", "outpatient", "CASH", ""),
    }
    aetna = records[("27447", "inpatient", "Aetna", "")]
    assert aetna["negotiated_rate"] == 23000.0  # median of the PPO and HMO rates
    assert (aetna["negotiated_min"], aetna["negotiated_max"]) == (18000.0, 61000.0)
    assert (aetna["cash_price"], aetna["gross_charge"]) == (45000.0, 90000.0)
    assert aetna["billing_class"] == "facility" and aetna["source"] == "mrf_parsed"
    assert aetna["measure_date"] == date(2026, 2, 23)
    # A percentage contract has no dollar rate: the hospital's median allowed amount is used.
    assert records[("27447", "inpatient", "Blue Shield of California", "")]["negotiated_rate"] == 30000.0


def test_inpatient_and_outpatient_are_kept_apart(tmp_path, payers):
    records = by_key(parse(tmp_path, TALL_CSV.encode(), payers))
    assert records[("27447", "inpatient", "Aetna", "")]["negotiated_rate"] == 23000.0
    # The bilateral (modifier 50) row is not averaged into the outpatient price.
    assert records[("27447", "outpatient", "Aetna", "")]["negotiated_rate"] == 19000.0


def test_cash_price_is_never_the_gross_charge(tmp_path, payers):
    outpatient_cash = by_key(parse(tmp_path, TALL_CSV.encode(), payers))[("27447", "outpatient", "CASH", "")]
    assert outpatient_cash["cash_price"] is None
    assert outpatient_cash["gross_charge"] == 70000.0
    assert outpatient_cash["negotiated_rate"] is None


def test_plan_detail_keeps_one_row_per_plan(tmp_path, payers):
    records = by_key(parse(tmp_path, TALL_CSV.encode(), payers, plan_detail=True))
    assert records[("27447", "inpatient", "Aetna", "PPO")]["negotiated_rate"] == 25000.0
    assert records[("27447", "inpatient", "Aetna", "HMO")]["negotiated_rate"] == 21000.0


def test_wide_csv(tmp_path, payers):
    records = by_key(parse(tmp_path, WIDE_CSV.encode("utf-8-sig"), payers, plan_detail=True))
    assert records[("29881", "outpatient", "Cigna", "Open Access")]["negotiated_rate"] == 4000.0
    united = records[("29881", "outpatient", "UnitedHealthcare", "Choice Plus")]
    assert united["negotiated_rate"] == 5200.5  # from median_amount; spaces around | are tolerated
    assert (united["negotiated_min"], united["negotiated_max"], united["gross_charge"]) == (3000.0, 9000.0, 12000.0)
    assert records[("29881", "outpatient", "CASH", "")]["cash_price"] is None


def test_hospital_json_format(tmp_path, payers):
    records = by_key(parse(tmp_path, json.dumps(JSON_FILE).encode(), payers))
    assert set(records) == {
        ("27447", "inpatient", "Aetna", ""),
        ("27447", "inpatient", "Medi-Cal", ""),
        ("27447", "inpatient", "CASH", ""),
    }
    assert records[("27447", "inpatient", "Aetna", "")]["negotiated_rate"] == 25000.0
    assert records[("27447", "inpatient", "Medi-Cal", "")]["negotiated_rate"] == 16000.0
    assert records[("27447", "inpatient", "CASH", "")]["cash_price"] == 45000.0
    assert records[("27447", "inpatient", "CASH", "")]["measure_date"] == date(2026, 1, 15)


def test_byte_order_mark_is_skipped(tmp_path, payers):
    # Stanford and UCI Health publish JSON that starts with a UTF-8 byte-order mark.
    plain = parse(tmp_path, json.dumps(JSON_FILE).encode(), payers)
    assert parse(tmp_path, b"\xef\xbb\xbf" + json.dumps(JSON_FILE).encode(), payers, name="bom.json") == plain
    assert parse(tmp_path, b"\xef\xbb\xbf" + TALL_CSV.encode(), payers, name="bom.csv") == parse(
        tmp_path, TALL_CSV.encode(), payers, name="plain.csv"
    )


def test_gzip_and_zip_are_detected_by_content(tmp_path, payers):
    plain = parse(tmp_path, TALL_CSV.encode(), payers)
    assert parse(tmp_path, gzip.compress(TALL_CSV.encode()), payers, name="prices.csv") == plain

    archive = tmp_path / "prices.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "see the csv")
        zf.writestr("standardcharges.json", json.dumps(JSON_FILE))
    book = PriceBook("050002", payers, False)
    parse_file(archive, book, TARGETS)
    assert len(book.records()) == 3


def test_insurer_format_and_empty_files_are_errors(tmp_path, payers):
    # Transparency in Coverage (insurer) files have in_network, not standard_charge_information.
    with pytest.raises(ValueError, match="no standard charges"):
        parse(tmp_path, json.dumps({"in_network": [{"billing_code": "27447"}]}).encode(), payers)
    with pytest.raises(ValueError, match="no charge header row"):
        parse(tmp_path, b"<html>Access denied</html>\n", payers)
    with pytest.raises(ValueError, match="unreadable json"):
        parse(tmp_path, b'{"standard_charge_information": [{"description": ', payers)


def test_unusual_dates_do_not_raise():
    assert parse_measure_date("    2026-02-23") == date(2026, 2, 23)
    assert parse_measure_date("1/1/2026") == date(2026, 1, 1)
    assert parse_measure_date("2026-03-29T00:00:00") == date(2026, 3, 29)
    for junk in ("", None, "Q1 2026", "13/45/2026", "0001-01-01", "2026.1"):
        assert parse_measure_date(junk) is None


def test_payer_names_are_normalized(payers):
    assert payers.normalize("AETNA HEALTH OF CALIFORNIA INC") == "Aetna"
    assert payers.normalize("Anthem Blue Cross - PPO") == "Anthem Blue Cross"
    assert payers.normalize("BLUE SHIELD OF CA") == "Blue Shield of California"
    assert payers.normalize("Aetna Medicare Advantage") == "Aetna"
    assert payers.normalize("MEDI-CAL FFS") == "Medi-Cal"
    assert payers.normalize("Medicare Part A") == "Medicare"
    assert payers.normalize("united  healthcare") == "UnitedHealthcare"
    assert payers.normalize("SOME LOCAL UNION TRUST") == "Some Local Union Trust"
    assert payers.normalize("United") == "UnitedHealthcare"
    assert payers.normalize("Interplan-All Plans") == payers.normalize("Interplan") == "Interplan"


def test_amounts():
    assert mrf_parser.to_amount("$1,234.50") == 1234.5
    for junk in (None, "", "N/A", "0", "-5", "nan", True, float("nan")):
        assert mrf_parser.to_amount(junk) is None


def test_shards_partition_the_hospitals():
    ccns = [f"05{i:04d}" for i in range(200)]
    shards = [[c for c in ccns if mrf_parser.in_shard(c, f"{i}/4")] for i in range(4)]
    assert sorted(sum(shards, [])) == ccns
    assert all(len(s) > 20 for s in shards)
    assert all(mrf_parser.in_shard(c, None) for c in ccns)
