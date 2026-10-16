"""The fee-schedule step, on a trimmed copy of the 2026 PPRRVU CSV layout."""

import io
import zipfile

import pytest

from etl.extract.cpt_fee_schedule import extract_codes, parse_rvu_zip

# Rows 0-8 of the real file are a title and multi-row headers; row 9 has the column names.
# The facility and non-facility totals are both headed "TOTAL" there.
PREAMBLE = "\n".join(
    [",,2026 National Physician Fee Schedule Relative Value File January Release"] + [",,"] * 8
)
HEADER = (
    "HCPCS,MOD,DESCRIPTION,CODE,PAYMENT,RVU,PE RVU,INDICATOR,PE RVU,INDICATOR,RVU,TOTAL,TOTAL,"
    "IND,DAYS,FACTOR"
)
ROWS = [
    "27447,,Total knee arthroplasty,A,,19.11,11.58,NA,11.58,,4.02,34.71,34.71,0,090,33.4009",
    # Imaging is split into professional (26) and technical (TC) components.
    "70450,,Ct head/brain w/o dye,A,,0.85,2.00,,2.00,,0.04,3.19,3.19,1,XXX,33.4009",
    "70450,26,Ct head/brain w/o dye,A,,0.85,0.30,,0.30,,0.03,1.18,1.18,1,XXX,33.4009",
    "70450,TC,Ct head/brain w/o dye,A,,0.00,1.70,,1.70,,0.01,2.01,2.01,1,XXX,33.4009",
    # A discontinued-procedure row is a reduced payment, not a component.
    "44388,,Colonoscopy thru stoma spx,A,,2.72,5.00,,1.20,,0.30,8.00,4.22,0,000,33.4009",
    "44388,53,Colonoscopy thru stoma spx,A,,1.00,0.50,,0.50,,0.10,2.12,2.12,0,000,33.4009",
    # Carrier-priced: no national amount.
    "0232T,,Njx platelet plasma,C,,0.00,0.00,,0.00,,0.00,0.00,0.00,0,XXX,33.4009",
    "15877,,Suction lipectomy trunk,C,,0.00,0.00,,0.00,,0.00,0.00,0.00,0,090,33.4009",
]


def rvu_zip(header: str = HEADER) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("PPRRVU2026_Jan_nonQPP.csv", "\n".join([PREAMBLE, header, *ROWS]) + "\n")
        zf.writestr("PPRRVU2026_Jan_QPP.csv", "ignored")
    return buffer.getvalue()


def test_medicare_amount_is_the_facility_total_times_the_conversion_factor():
    records = {r["code"]: r for r in extract_codes(parse_rvu_zip(rvu_zip()))}
    assert records["27447"]["medicare_amount"] == round(34.71 * 33.4009, 2)
    assert records["27447"]["avg_work_rvu"] == 19.11
    assert records["27447"]["is_surgical"] is True


def test_split_codes_get_their_lowest_component():
    records = {r["code"]: r for r in extract_codes(parse_rvu_zip(rvu_zip()))}
    assert records["70450"]["medicare_amount"] == round(1.18 * 33.4009, 2)  # the professional component


def test_reduced_payment_modifiers_are_ignored():
    records = {r["code"]: r for r in extract_codes(parse_rvu_zip(rvu_zip()))}
    assert records["44388"]["medicare_amount"] == round(4.22 * 33.4009, 2)


def test_codes_without_a_national_amount_have_none():
    records = {r["code"]: r for r in extract_codes(parse_rvu_zip(rvu_zip()))}
    assert "15877" not in records  # status C is not loaded at all
    assert all(r["medicare_amount"] is None or r["medicare_amount"] > 0 for r in records.values())


def test_a_file_without_payment_columns_is_an_error():
    with pytest.raises(ValueError, match="conversion_factor"):
        extract_codes(parse_rvu_zip(rvu_zip(HEADER.replace(",FACTOR", ",OTHER"))))
