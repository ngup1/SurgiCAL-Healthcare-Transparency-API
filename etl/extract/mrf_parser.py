"""
Hospital MRF (Machine Readable File) Price Parser

Downloads and parses hospital price transparency files (the CMS "standard
charges" template) listed in the hospital_mrf_links table, which mrf_links.py
fills.

Supports the three layouts of the CMS template, versions 2 and 3:
  - JSON: charges under standard_charge_information
  - CSV tall: one row per code × payer
  - CSV wide: one row per code, with payer and plan in the column headers
  - any of them gzip-compressed or inside a zip archive

Files are downloaded to disk and read as a stream, so a multi-gigabyte file
costs disk space, not memory. Files over MRF_MAX_FILE_MB are skipped.

Each hospital is loaded on its own: its prices are replaced in one transaction,
and a failure is recorded on its link and does not stop the other hospitals.

Populates the `prices` table with source='mrf_parsed'.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import re
import statistics
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import ijson

from etl.common.config import ETL_DIR, get_logger, get_settings
from etl.common.db import execute, execute_query, replace_rows
from etl.common.http import RateLimitedClient

logger = get_logger(__name__)

SOURCE = "mrf_parsed"
CASH_PAYER = "CASH"
PAYER_ALIASES_FILE = ETL_DIR / "reference" / "payer_aliases.csv"

CODE_TYPES = {"CPT", "HCPCS"}
SETTINGS = {"inpatient", "outpatient", "both"}
BILLING_CLASSES = {"facility", "professional", "both"}
# A hospital's file lists what the hospital charges; without a billing class that is the facility fee.
DEFAULT_BILLING_CLASS = "facility"

csv.field_size_limit(16 * 1024 * 1024)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_links_by_hospital(state: str = "CA") -> dict[str, list[dict]]:
    """Price-file links grouped by CCN (a hospital can publish one file per campus)."""
    rows = execute_query(
        """
        SELECT id, ccn, hospital_name, machine_readable_url
        FROM hospital_mrf_links
        WHERE ccn IS NOT NULL AND state = %s
        ORDER BY ccn, machine_readable_url
        """,
        (state,),
    )
    by_ccn: dict[str, list[dict]] = {}
    for row in rows:
        by_ccn.setdefault(row["ccn"], []).append(row)
    return by_ccn


def get_target_cpts(limit: int | None = None) -> set[str]:
    """
    CPT codes to keep prices for.

    Every hospital prices thousands of codes for dozens of payers; keeping all of
    them is what makes the prices table large. With a limit, the codes are those
    in the curated device map plus the `limit` surgical and imaging codes that
    the state's surgeons bill most (by Medicare volume). 0 keeps every code.
    """
    if limit is None:
        limit = get_settings().mrf_cpt_limit
    if limit <= 0:
        return {r["code"] for r in execute_query("SELECT code FROM cpt_codes")}

    rows = execute_query(
        """
        SELECT cpt AS code FROM procedure_product_codes
        UNION
        (SELECT v.cpt FROM provider_procedure_volumes v
         JOIN cpt_codes c ON c.code = v.cpt
         WHERE c.is_surgical OR c.category = 'scans'
         GROUP BY v.cpt
         ORDER BY sum(v.total_services) DESC
         LIMIT %s)
        """,
        (limit,),
    )
    return {r["code"] for r in rows}


def _set_link_status(link_id: str, status: str, error: str | None = None, price_rows: int | None = None) -> None:
    """Record the outcome of loading one hospital_mrf_links row."""
    execute(
        """
        UPDATE hospital_mrf_links
        SET url_status = %s, last_error = %s, price_rows = %s, last_checked = %s
        WHERE id = %s
        """,
        (status, error[:500] if error else None, price_rows, datetime.now(UTC), link_id),
    )


# ---------------------------------------------------------------------------
# Payer names
# ---------------------------------------------------------------------------

def load_payer_aliases() -> list[tuple[re.Pattern, str]]:
    """(pattern, payer) pairs from payer_aliases.csv, in priority order."""
    with open(PAYER_ALIASES_FILE, newline="") as fh:
        rows = sorted(csv.DictReader(fh), key=lambda r: int(r["priority"]))
    return [(re.compile(r["pattern"], re.I), r["payer"]) for r in rows]


def sync_payer_aliases() -> None:
    """Copy payer_aliases.csv into the payer_aliases table, so the rules in use are queryable."""
    with open(PAYER_ALIASES_FILE, newline="") as fh:
        rows = [
            {"pattern": r["pattern"], "payer": r["payer"], "priority": int(r["priority"])}
            for r in csv.DictReader(fh)
        ]
    replace_rows("payer_aliases", rows, "TRUE", ())


_ALL_PLANS_RE = re.compile(r"\s*[-|:(]?\s*all (other )?plans\)?$", re.I)


class PayerNames:
    """Maps payer names as hospitals write them to one name per insurer."""

    def __init__(self, aliases: list[tuple[re.Pattern, str]]):
        self._aliases = aliases
        self._cache: dict[str, str] = {}

    def normalize(self, raw: str) -> str:
        name = self._cache.get(raw)
        if name is None:
            # "Interplan-All Plans" and "Interplan" are one payer.
            cleaned = _ALL_PLANS_RE.sub("", " ".join(raw.split()))
            name = next((payer for pattern, payer in self._aliases if pattern.search(cleaned)), None)
            if name is None:
                # No alias: keep the hospital's name, in title case if it shouts or whispers.
                name = cleaned.title() if cleaned.isupper() or cleaned.islower() else cleaned
            self._cache[raw] = name = name[:200]
        return name


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def to_amount(val) -> float | None:
    """A positive dollar amount from a cell or JSON value; None for anything else."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        amount = float(val)
    else:
        s = str(val).replace(",", "").replace("$", "").strip()
        if not s:
            return None
        try:
            amount = float(s)
        except ValueError:
            return None
    # NaN fails both comparisons; a price of zero or less is a placeholder.
    return amount if 0 < amount < 1e9 else None


def parse_measure_date(raw) -> date | None:
    """The file's last_updated_on as a date. Hospitals write it many ways; None if unreadable."""
    s = str(raw or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%m/%d/%y", "%Y%m%d"):
        try:
            parsed = datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
        return parsed if 2015 <= parsed.year <= date.today().year + 1 else None
    return None


def match_code(code, code_type, target_cpts: set[str]) -> str | None:
    """The code if it is a CPT/HCPCS code we keep, else None."""
    if code is None or str(code_type or "").strip().upper() not in CODE_TYPES:
        return None
    code = str(code).strip().upper()
    return code if code in target_cpts else None


def _choice(raw, allowed: set[str], default: str) -> str:
    value = str(raw or "").strip().lower()
    return value if value in allowed else default


# ---------------------------------------------------------------------------
# Collecting charges into price records
# ---------------------------------------------------------------------------

class PriceBook:
    """
    Collects the charges read from one hospital's files and turns them into
    prices rows.

    A file can list one procedure many times (per description, per plan), so
    values are gathered per (code, setting, billing class) and per payer, and
    summarised once at the end: the median for rates, cash and gross prices, and
    the extremes for the minimum and maximum.
    """

    def __init__(self, ccn: str, payers: PayerNames, plan_detail: bool):
        self.ccn = ccn
        self._payers = payers
        self._plan_detail = plan_detail
        self._items: dict[tuple[str, str, str], dict[str, list[float]]] = {}
        self._rates: dict[tuple[str, str, str, str, str], list[float]] = {}
        self.measure_date: date | None = None

    def add_item(self, cpt: str, setting: str, billing_class: str, *, gross=None, cash=None, low=None, high=None):
        """Charges that apply to every payer: gross, discounted cash, and the min/max negotiated."""
        item = self._items.setdefault((cpt, setting, billing_class), {"gross": [], "cash": [], "low": [], "high": []})
        for key, value in (("gross", gross), ("cash", cash), ("low", low), ("high", high)):
            amount = to_amount(value)
            if amount is not None:
                item[key].append(amount)

    def add_rate(self, cpt: str, setting: str, billing_class: str, payer, plan, rate):
        """One payer's negotiated rate."""
        amount = to_amount(rate)
        payer = " ".join(str(payer or "").split())
        if amount is None or not payer:
            return
        plan_name = " ".join(str(plan or "").split())[:200] if self._plan_detail else ""
        self._rates.setdefault(
            (cpt, setting, billing_class, self._payers.normalize(payer), plan_name), []
        ).append(amount)

    def records(self) -> list[dict]:
        """prices rows: one per payer (and plan, with plan detail), plus a CASH row per item."""
        def item_values(key: tuple[str, str, str]) -> dict:
            item = self._items.get(key, {})
            return {
                "cash_price": round(statistics.median(item["cash"]), 2) if item.get("cash") else None,
                "gross_charge": round(statistics.median(item["gross"]), 2) if item.get("gross") else None,
                "negotiated_min": round(min(item["low"]), 2) if item.get("low") else None,
                "negotiated_max": round(max(item["high"]), 2) if item.get("high") else None,
            }

        def row(key: tuple[str, str, str], payer: str, plan: str, values: dict) -> dict:
            cpt, setting, billing_class = key
            return {
                "cpt": cpt, "ccn": self.ccn, "payer": payer, "plan_name": plan,
                "billing_class": billing_class, "setting": setting,
                **values, "source": SOURCE, "measure_date": self.measure_date,
            }

        # A hospital that lists self-pay as if it were a payer: that is the cash price.
        for (cpt, setting, billing_class, payer, _), rates in self._rates.items():
            if payer == CASH_PAYER:
                self.add_item(cpt, setting, billing_class, cash=statistics.median(rates))

        records = []
        for (cpt, setting, billing_class, payer, plan), rates in self._rates.items():
            if payer == CASH_PAYER:
                continue
            key = (cpt, setting, billing_class)
            values = item_values(key)
            values["negotiated_rate"] = round(statistics.median(rates), 2)
            # Without the file's own min/max, the spread of this payer's rates.
            if values["negotiated_min"] is None:
                values["negotiated_min"] = round(min(rates), 2)
            if values["negotiated_max"] is None:
                values["negotiated_max"] = round(max(rates), 2)
            records.append(row(key, payer, plan, values))

        for key in self._items:
            values = item_values(key)
            if values["cash_price"] is not None or values["gross_charge"] is not None:
                records.append(row(key, CASH_PAYER, "", {**values, "negotiated_rate": None}))
        return records


# ---------------------------------------------------------------------------
# Opening downloaded files
# ---------------------------------------------------------------------------

@contextmanager
def open_price_file(path: Path) -> Iterator[tuple[io.BufferedIOBase, str]]:
    """
    Open a downloaded price file as (binary stream, 'json' | 'csv'), looking at
    its content, not its name: unwraps gzip and zip (the largest member).
    """
    with open(path, "rb") as fh:
        magic = fh.read(4)

    if magic[:2] == b"PK":
        with zipfile.ZipFile(path) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            if not members:
                raise ValueError("zip archive is empty")
            with zf.open(max(members, key=lambda m: m.file_size)) as stream:
                yield _prepare(io.BufferedReader(stream, buffer_size=_BUFFER_SIZE))
        return

    opener = gzip.open if magic[:2] == b"\x1f\x8b" else io.FileIO
    with opener(path, "rb") as stream:
        yield _prepare(io.BufferedReader(stream, buffer_size=_BUFFER_SIZE))


# Large enough that peek() sees the general fields at the top of a JSON file.
_BUFFER_SIZE = 64 * 1024

_BOM = b"\xef\xbb\xbf"


def _prepare(buffered: io.BufferedReader) -> tuple[io.BufferedReader, str]:
    """Sniff the format and skip a UTF-8 byte-order mark, which the JSON parser rejects."""
    head = buffered.peek(2048)
    if head.startswith(_BOM):
        buffered.read(len(_BOM))
        head = head[len(_BOM):]
    return buffered, _sniff_format(head)


def _sniff_format(head: bytes) -> str:
    return "json" if head.lstrip(b" \t\r\n")[:1] in (b"{", b"[") else "csv"


# ---------------------------------------------------------------------------
# JSON (standard_charge_information)
# ---------------------------------------------------------------------------

_JSON_DATE_RE = re.compile(rb'"last_updated_on"\s*:\s*"([^"]*)"')


def parse_json(stream: io.BufferedIOBase, book: PriceBook, target_cpts: set[str]) -> int:
    """
    Stream-parse the hospital JSON template:

    {
      "hospital_name": ..., "last_updated_on": "2026-01-01", "version": "3.0.0",
      "standard_charge_information": [
        {
          "description": ...,
          "code_information": [{"code": "27447", "type": "CPT"}],
          "standard_charges": [
            {
              "setting": "inpatient", "billing_class": "facility",
              "gross_charge": 90000, "discounted_cash": 45000, "minimum": 18000, "maximum": 61000,
              "payers_information": [
                {"payer_name": "Aetna", "plan_name": "PPO", "standard_charge_dollar": 25000,
                 "median_amount": ...}
              ]
            }
          ]
        }
      ]
    }

    Returns the number of items read (all codes), to tell an empty file from one
    that simply has none of our codes.
    """
    match = _JSON_DATE_RE.search(stream.peek(_BUFFER_SIZE))
    if match and book.measure_date is None:
        book.measure_date = parse_measure_date(match.group(1).decode("utf-8", "replace"))

    items = 0
    for item in ijson.items(stream, "standard_charge_information.item", use_float=True):
        items += 1
        cpt = next(
            (c for c in (match_code(ci.get("code"), ci.get("type"), target_cpts)
                         for ci in item.get("code_information") or [] if isinstance(ci, dict)) if c),
            None,
        )
        if cpt is None:
            continue
        for charge in item.get("standard_charges") or []:
            # A modifier changes the price (bilateral, assistant surgeon...).
            if charge.get("modifier_code") or charge.get("modifiers"):
                continue
            setting = _choice(charge.get("setting"), SETTINGS, "")
            billing_class = _choice(charge.get("billing_class"), BILLING_CLASSES, DEFAULT_BILLING_CLASS)
            book.add_item(
                cpt, setting, billing_class,
                gross=charge.get("gross_charge"), cash=charge.get("discounted_cash"),
                low=charge.get("minimum"), high=charge.get("maximum"),
            )
            for payer in charge.get("payers_information") or []:
                # A dollar rate if the contract has one; otherwise the hospital's
                # median allowed amount (v3) or estimate (v2) for a percentage or formula.
                rate = (
                    to_amount(payer.get("standard_charge_dollar"))
                    or to_amount(payer.get("median_amount"))
                    or to_amount(payer.get("estimated_amount"))
                )
                book.add_rate(cpt, setting, billing_class, payer.get("payer_name"), payer.get("plan_name"), rate)
    return items


# ---------------------------------------------------------------------------
# CSV (tall and wide)
#
# Layout of the CMS template:
#   Row 1 : general field names  (hospital_name, last_updated_on, version, ...)
#   Row 2 : general field values
#   Row 3 : charge column headers (description, code|1, code|1|type, setting, ...)
#   Row 4+: charge rows
#
# Tall: one row per code × payer, with payer_name and plan_name columns.
# Wide: one row per code, with a group of columns per payer and plan:
#   standard_charge|<payer>|<plan>|negotiated_dollar, median_amount|<payer>|<plan>, ...
# ---------------------------------------------------------------------------

_PIPE_RE = re.compile(r"\s*\|\s*")


def _normalize_header(cell: str) -> str:
    """'Standard_Charge | Gross ' -> 'standard_charge|gross'."""
    return _PIPE_RE.sub("|", cell.strip().lower())


def parse_csv(stream: io.BufferedIOBase, book: PriceBook, target_cpts: set[str]) -> int:
    """Stream-parse the tall or wide CSV template. Returns the number of charge rows read."""
    text = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline="")
    reader = csv.reader(text)

    # General rows, then the charge header: the first row with a description and a code column.
    header: list[str] | None = None
    header_raw: list[str] = []
    preamble: list[list[str]] = []
    for row in reader:
        cells = [_normalize_header(c) for c in row]
        if "description" in cells and any(c.startswith("code|") for c in cells):
            header, header_raw = cells, row
            break
        preamble.append(row)
        if len(preamble) >= 10:
            break
    if header is None:
        raise ValueError("no charge header row (description, code|1, ...) in the first rows of the CSV")

    if len(preamble) >= 2 and book.measure_date is None:
        general = dict(zip((_normalize_header(c) for c in preamble[0]), preamble[1], strict=False))
        book.measure_date = parse_measure_date(general.get("last_updated_on"))

    col = {name: i for i, name in reversed(list(enumerate(header)))}  # first column wins on repeats
    code_cols = [
        (i, col.get(f"{name}|type"))
        for i, name in enumerate(header)
        if re.fullmatch(r"code\|\d+", name)
    ]
    i_setting, i_billing, i_modifiers = col.get("setting"), col.get("billing_class"), col.get("modifiers")
    i_gross, i_cash = col.get("standard_charge|gross"), col.get("standard_charge|discounted_cash")
    i_min, i_max = col.get("standard_charge|min"), col.get("standard_charge|max")

    tall = "payer_name" in col
    if tall:
        i_payer, i_plan = col["payer_name"], col.get("plan_name")
        rate_columns = ("standard_charge|negotiated_dollar", "median_amount", "estimated_amount")
        i_rates = [col[n] for n in rate_columns if n in col]
    else:
        # (payer, plan) -> columns to try for its rate, in order of preference
        wide: dict[tuple[str, str], dict[str, int]] = {}
        for i, name in enumerate(header):
            parts = name.split("|")
            raw_parts = [p.strip() for p in header_raw[i].split("|")]  # payer and plan as written
            if len(parts) == 4 and parts[0] == "standard_charge" and parts[3] == "negotiated_dollar":
                wide.setdefault((raw_parts[1], raw_parts[2]), {})["dollar"] = i
            elif len(parts) == 3 and parts[0] in ("median_amount", "estimated_amount"):
                wide.setdefault((raw_parts[1], raw_parts[2]), {})["estimate"] = i
        if not wide:
            raise ValueError("CSV has neither a payer_name column nor per-payer negotiated_dollar columns")

    def cell(row: list[str], i: int | None) -> str | None:
        return row[i] if i is not None and i < len(row) else None

    rows = 0
    for row in reader:
        rows += 1
        cpt = None
        for i_code, i_type in code_cols:
            cpt = match_code(cell(row, i_code), cell(row, i_type), target_cpts)
            if cpt:
                break
        if cpt is None or (cell(row, i_modifiers) or "").strip():
            continue

        setting = _choice(cell(row, i_setting), SETTINGS, "")
        billing_class = _choice(cell(row, i_billing), BILLING_CLASSES, DEFAULT_BILLING_CLASS)
        book.add_item(
            cpt, setting, billing_class,
            gross=cell(row, i_gross), cash=cell(row, i_cash), low=cell(row, i_min), high=cell(row, i_max),
        )
        if tall:
            rate = next((a for a in (to_amount(cell(row, i)) for i in i_rates) if a is not None), None)
            book.add_rate(cpt, setting, billing_class, cell(row, i_payer), cell(row, i_plan), rate)
        else:
            for (payer, plan), columns in wide.items():
                rate = to_amount(cell(row, columns.get("dollar"))) or to_amount(cell(row, columns.get("estimate")))
                if rate is not None:
                    book.add_rate(cpt, setting, billing_class, payer, plan, rate)
    return rows


# ---------------------------------------------------------------------------
# Per-hospital processing
# ---------------------------------------------------------------------------

def parse_file(path: Path, book: PriceBook, target_cpts: set[str]) -> int:
    """Parse one downloaded file into the book. Returns rows/items read; raises if it has none."""
    with open_price_file(path) as (stream, fmt):
        try:
            count = parse_json(stream, book, target_cpts) if fmt == "json" else parse_csv(stream, book, target_cpts)
        except (ijson.JSONError, csv.Error, zlib.error, EOFError, UnicodeError) as exc:
            raise ValueError(f"unreadable {fmt} file: {exc}") from exc
    if count == 0:
        raise ValueError(f"{fmt} file has no standard charges")
    return count


def load_hospital(
    client: RateLimitedClient,
    ccn: str,
    links: list[dict],
    target_cpts: set[str],
    payers: PayerNames,
    plan_detail: bool,
) -> int:
    """
    Download and parse every price file of one hospital, then replace its prices.

    A file that fails is marked broken on its link; the hospital's prices are
    replaced only if at least one file produced prices, so a bad download never
    wipes what an earlier run loaded.

    Returns the number of price rows loaded.
    """
    settings = get_settings()
    data_dir = Path(settings.data_dir) / "mrf"
    max_bytes = settings.mrf_max_file_mb * 1024 * 1024
    book = PriceBook(ccn, payers, plan_detail)
    errors: dict[str, str | None] = {}  # url -> why it failed, or None

    # Several locations of one hospital often point at the same file: read it once.
    for url in dict.fromkeys(link["machine_readable_url"] for link in links):
        path = data_dir / f"{ccn}-{hashlib.sha1(url.encode()).hexdigest()[:12]}"
        try:
            client.download(url, path, max_bytes=max_bytes)
            rows = parse_file(path, book, target_cpts)
            logger.info(f"  Read {rows} rows from {url[:90]}")
            errors[url] = None
        except Exception as exc:  # one bad file must not stop the hospital or the run
            logger.warning(f"  Failed {url[:90]}: {type(exc).__name__}: {exc}")
            errors[url] = f"{type(exc).__name__}: {exc}"
        finally:
            path.unlink(missing_ok=True)

    records = book.records()
    if records:
        replace_rows("prices", records, "ccn = %s AND source = %s", (ccn, SOURCE), batch_size=5000)
    for link in links:
        error = errors[link["machine_readable_url"]]
        if error:
            _set_link_status(str(link["id"]), "broken", error)
        else:
            note = None if records else "no prices for the tracked codes"
            _set_link_status(str(link["id"]), "active", note, len(records))
    return len(records)


def in_shard(ccn: str, shard: str | None) -> bool:
    """Whether a hospital belongs to shard 'I/N' (0-based I). Stable across runs."""
    if not shard:
        return True
    index, total = (int(x) for x in shard.split("/"))
    return zlib.crc32(ccn.encode()) % total == index


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(state: str = "CA", limit: int | None = None, shard: str | None = None) -> int:
    """
    Parse price files for the state's hospitals and load them into prices.

    Args:
        state: State filter for hospital_mrf_links (default: CA).
        limit: Max number of hospitals to process in this run (default: all).
        shard: 'I/N' to process only the I-th of N stable groups of hospitals.

    Returns:
        Total number of price records loaded.
    """
    logger.info(f"Starting MRF price extraction (state={state})")
    settings = get_settings()

    by_hospital = {c: links for c, links in get_links_by_hospital(state).items() if in_shard(c, shard)}
    if limit:
        by_hospital = dict(list(by_hospital.items())[:limit])
    if not by_hospital:
        logger.warning("No price-file links to parse. Run mrf_links.py first.")
        return 0

    target_cpts = get_target_cpts()
    if not target_cpts:
        raise RuntimeError("No target CPT codes: load cpt_codes (and utilization, for the volume ranking) first")
    payers = PayerNames(load_payer_aliases())
    plan_detail = settings.mrf_plan_detail

    logger.info(
        f"Processing {len(by_hospital)} hospitals | {len(target_cpts)} target CPT codes | "
        f"one row per {'plan' if plan_detail else 'payer'}"
    )

    total_loaded = 0
    failed = 0
    with RateLimitedClient(delay_ms=0, timeout=120.0) as client:
        for i, (ccn, links) in enumerate(by_hospital.items(), 1):
            logger.info(f"[{i}/{len(by_hospital)}] {links[0]['hospital_name']} ({ccn}), {len(links)} file(s)")
            try:
                count = load_hospital(client, ccn, links, target_cpts, payers, plan_detail)
            except Exception as exc:  # e.g. the database write; the next hospital still runs
                logger.error(f"  Hospital {ccn} failed: {type(exc).__name__}: {exc}")
                failed += 1
                continue
            total_loaded += count
            if count == 0:
                failed += 1
            logger.info(f"  Loaded {count} price records")

    logger.info(
        f"MRF extraction complete -- {total_loaded} price records for "
        f"{len(by_hospital) - failed} of {len(by_hospital)} hospitals"
    )
    return total_loaded


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse hospital MRF price files")
    parser.add_argument("--state", default="CA", help="State filter (default: CA)")
    parser.add_argument("--limit", type=int, default=None, help="Max hospitals to process")
    parser.add_argument("--shard", default=None, metavar="I/N", help="Process shard I of N")
    args = parser.parse_args()
    run(state=args.state, limit=args.limit, shard=args.shard)
