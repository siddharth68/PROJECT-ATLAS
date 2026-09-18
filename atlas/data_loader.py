"""
Data loading and normalization for ATLAS.

Loads all domain CSVs, reference ranges, corrections, cuts, site replies
and monitor decisions.  Every public function is designed to be called from
StudyGraph.build() -- nothing here does clinical reasoning.
"""

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from atlas.schemas import Domain

# ---------------------------------------------------------------------------
# Data record
# ---------------------------------------------------------------------------

@dataclass
class RawRecord:
    """One row from a domain CSV, ready for the graph layer."""
    domain: Domain
    usubjid: str
    seq: int
    data: Dict[str, Any]
    cut_available: int
    corrected_at_cut: Optional[int]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

_MONTH = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_DATE_FMTS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d.%m.%Y",
)


def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse a date string.  Handles ISO, DD-MON-YYYY, and several others."""
    if not date_str or not isinstance(date_str, str):
        return None
    s = date_str.strip()
    if not s:
        return None

    # Fast path: ISO YYYY-MM-DD (most common after cleaning)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            pass

    # DD-MON-YYYY  e.g. 28-JAN-2026  or  28/JAN/2026
    parts = s.replace("/", "-").split("-")
    if len(parts) == 3 and parts[1].upper() in _MONTH:
        try:
            day = int(parts[0])
            month = _MONTH[parts[1].upper()]
            year = int(parts[2])
            return datetime(year, month, day)
        except (ValueError, TypeError):
            pass

    # Try remaining formats
    upper = s.upper()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(upper, fmt)
        except ValueError:
            continue

    return None


def parse_numeric(val: Any) -> Optional[float]:
    """Parse a numeric value.

    Returns None for blanks, ND, <5, N/A, NA, etc. -- these are
    *not zero* and must not be treated as zero.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    upper = s.upper()
    if upper in ("ND", "N/A", "NA", "NULL", "UNKNOWN", "."):
        return None
    if s.startswith("<") or s.startswith(">"):
        return None  # censored -- keep None so callers don't treat as 0
    # Handle comma as decimal separator
    s = s.replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

# Map domain enum → sequence-number column name
_SEQ_COL: Dict[Domain, str] = {
    Domain.DM: "",          # DM has no sequence col; will default to 1
    Domain.AE: "AESEQ",
    Domain.LB: "LBSEQ",
    Domain.VS: "VSSEQ",
    Domain.EX: "EXSEQ",
    Domain.CM: "CMSEQ",
    Domain.DS: "DSSEQ",
    Domain.MH: "MHSEQ",
    Domain.EG: "EGSEQ",
}


def load_csv(filepath: str, domain: Domain) -> List[RawRecord]:
    """Load a domain CSV into a list of :class:`RawRecord`.

    * Handles UTF-8 BOM (``utf-8-sig``).
    * Skips rows that lack USUBJID or whose SEQ is unparseable.
    * Stores every original column (except the two metadata cols) in *data*.
    """
    records: List[RawRecord] = []
    if not os.path.exists(filepath):
        return records

    seq_col = _SEQ_COL.get(domain, "")

    with open(filepath, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return records

        # Auto-detect seq column if the expected one isn't present
        if seq_col and seq_col not in reader.fieldnames:
            for fn in reader.fieldnames:
                if fn.endswith("SEQ"):
                    seq_col = fn
                    break

        for row in reader:
            # --- USUBJID is required for every domain record ---
            usubjid = (row.get("USUBJID") or "").strip()
            if not usubjid:
                continue  # skip malformed

            # --- Sequence number ---
            seq = 1
            if seq_col and seq_col in row:
                try:
                    seq = int(row[seq_col])
                except (ValueError, TypeError):
                    seq = 1  # keep row, default seq

            # --- cut_available / corrected_at_cut ---
            try:
                cut_available = int(row.get("cut_available") or 1)
            except (ValueError, TypeError):
                cut_available = 1
            try:
                cac = row.get("corrected_at_cut", "")
                corrected_at_cut = int(cac) if cac and cac.strip() else None
            except (ValueError, TypeError):
                corrected_at_cut = None

            # --- data dict: every column except metadata & seq ---
            data: Dict[str, Any] = {}
            for k, v in row.items():
                if k in ("cut_available", "corrected_at_cut"):
                    continue
                # keep SEQ cols in data so downstream can see them
                data[k] = v

            records.append(
                RawRecord(
                    domain=domain,
                    usubjid=usubjid,
                    seq=seq,
                    data=data,
                    cut_available=cut_available,
                    corrected_at_cut=corrected_at_cut,
                )
            )
    return records


# ---------------------------------------------------------------------------
# Reference data loaders
# ---------------------------------------------------------------------------

def load_reference_ranges(filepath: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Load reference_ranges.csv, keyed by *(LBTESTCD, LAB)*."""
    ranges: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not os.path.exists(filepath):
        return ranges
    with open(filepath, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            testcd = (row.get("LBTESTCD") or "").strip()
            lab = (row.get("LAB") or "").strip()
            unit = (row.get("UNIT") or "").strip()
            if not testcd or not lab:
                continue
            try:
                low = float(row["LOW"])
                high = float(row["HIGH"])
            except (ValueError, KeyError, TypeError):
                continue
            ranges[(testcd, lab)] = {"low": low, "high": high, "unit": unit}
    return ranges


def load_cuts(filepath: str) -> Dict[int, Dict[str, Any]]:
    """Load cuts.csv → ``{cut_number: {protocol_version, new_records, corrections}}``."""
    cuts: Dict[int, Dict[str, Any]] = {}
    if not os.path.exists(filepath):
        return cuts
    with open(filepath, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                cut = int(row["cut"])
            except (ValueError, KeyError, TypeError):
                continue
            cuts[cut] = {
                "protocol_version": int(row.get("protocol_version", 1)),
                "new_records": int(row.get("new_records", 0)),
                "corrections": int(row.get("corrections", 0)),
            }
    return cuts


def load_corrections(filepath: str) -> Dict[Tuple[int, str, str, int, str], str]:
    """Load corrections.csv → ``{(cut, domain, usubjid, seq, field): new_value}``."""
    corrections: Dict[Tuple[int, str, str, int, str], str] = {}
    if not os.path.exists(filepath):
        return corrections
    with open(filepath, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                cut = int(row["cut"])
                domain = row["domain"].strip()
                usubjid = row["usubjid"].strip()
                seq = int(row["seq"])
                field = row["field"].strip()
                new_value = row["new_value"]
            except (ValueError, KeyError, TypeError):
                continue
            corrections[(cut, domain, usubjid, seq, field)] = new_value
    return corrections


# ---------------------------------------------------------------------------
# Response loaders
# ---------------------------------------------------------------------------

def load_site_replies(filepath: str) -> Tuple[Dict[str, Any], list]:
    """Return *(replies_dict, default_list)* from site_replies.json."""
    default = ["ANSWERED", "Data verified against source documents. No change."]
    replies: Dict[str, Any] = {}
    if not os.path.exists(filepath):
        return replies, default
    with open(filepath, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    replies = data.get("replies", data)
    default = data.get("_default", default)
    return replies, default


def load_monitor_decisions(filepath: str) -> Dict[str, Any]:
    """Return the decisions dict from monitor_decisions.json."""
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("decisions", data)


# ---------------------------------------------------------------------------
# Bulk loader
# ---------------------------------------------------------------------------

_DOMAIN_FILES: Dict[Domain, str] = {
    Domain.DM: "DM.csv",
    Domain.AE: "AE.csv",
    Domain.LB: "LB.csv",
    Domain.VS: "VS.csv",
    Domain.EX: "EX.csv",
    Domain.CM: "CM.csv",
    Domain.DS: "DS.csv",
    Domain.MH: "MH.csv",
    Domain.EG: "EG.csv",
}


def load_all_data(
    data_dir: str, cut: Optional[int] = None
) -> Dict[Domain, List[RawRecord]]:
    """Load every domain CSV from *data_dir*/data/ and filter to *cut*."""
    all_data: Dict[Domain, List[RawRecord]] = {}

    for domain, filename in _DOMAIN_FILES.items():
        filepath = os.path.join(data_dir, "data", filename)
        records = load_csv(filepath, domain)
        if cut is not None:
            records = [r for r in records if r.cut_available <= cut]
        all_data[domain] = records

    return all_data


# ---------------------------------------------------------------------------
# Corrections applier
# ---------------------------------------------------------------------------

def apply_corrections(
    records: List[RawRecord],
    corrections: Dict[Tuple[int, str, str, int, str], str],
    cut: int,
) -> List[RawRecord]:
    """Apply corrections that are effective at or before *cut*.

    The corrections dict maps *(corr_cut, domain, usubjid, seq, field) -> new_value*.
    For each matching record we overwrite ``record.data[field]`` with the
    corrected value.  We do **not** remove the record.
    """
    # Build a fast lookup: (domain, usubjid, seq) -> list of (field, new_value)
    applicable: Dict[Tuple[str, str, int], List[Tuple[str, str]]] = {}
    for (c_cut, c_domain, c_usubjid, c_seq, c_field), new_val in corrections.items():
        if c_cut <= cut:
            key = (c_domain, c_usubjid, c_seq)
            applicable.setdefault(key, []).append((c_field, new_val))

    if not applicable:
        return records

    for rec in records:
        key = (rec.domain.value, rec.usubjid, rec.seq)
        fixes = applicable.get(key)
        if fixes:
            for field, new_val in fixes:
                rec.data[field] = new_val

    return records