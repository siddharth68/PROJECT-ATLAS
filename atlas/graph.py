"""
Study Graph and Patient360 implementation for ATLAS.

This module provides:
* ``StudyGraph``   -- builds an in-memory knowledge graph from the study
                      CSV files, with efficient indexes for fast look-ups.
* ``NormalizedRecord`` -- a single graph node holding raw + parsed data.

Downstream code (reasoning, atlas) imports ``StudyGraph`` and uses
``patient360()``, ``query()``, ``get_subjects()``, and direct index access.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from atlas.schemas import Domain, RecordRef, Patient360, GraphStats
from atlas.data_loader import (
    RawRecord,
    apply_corrections,
    load_all_data,
    load_corrections,
    load_cuts,
    load_monitor_decisions,
    load_reference_ranges,
    load_site_replies,
    parse_date,
    parse_numeric,
)


# ---------------------------------------------------------------------------
# NormalizedRecord -- one node in the graph
# ---------------------------------------------------------------------------

@dataclass
class NormalizedRecord:
    """A single study record (graph node) with parsed / enriched fields."""
    ref: RecordRef
    data: Dict[str, Any]
    cut_available: int
    corrected_at_cut: Optional[int]
    parsed: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# StudyGraph
# ---------------------------------------------------------------------------

class StudyGraph:
    """Study knowledge graph / Patient 360 builder.

    Usage::

        graph = StudyGraph("path/to/hackathon-data")
        graph.build()              # full dataset
        graph.build(cut=5)         # as-of cut 5

        p360 = graph.patient360("042-S01-001")
        stats = graph.get_stats()
    """

    def __init__(self, data_dir: str):
        self.data_dir: str = data_dir
        # --- primary stores ---
        self.records_by_domain: Dict[Domain, List[NormalizedRecord]] = {}
        self.records_by_subject: Dict[str, Dict[Domain, List[NormalizedRecord]]] = {}
        # --- secondary indexes (populated in _build_indexes) ---
        self._by_ref: Dict[str, NormalizedRecord] = {}           # "DM|042-S01-001|1"
        self._by_site: Dict[str, Set[str]] = defaultdict(set)    # site -> {subjects}
        self._by_visit: Dict[str, Dict[str, List[NormalizedRecord]]] = defaultdict(lambda: defaultdict(list))
        self._subjects_list: List[str] = []
        # --- reference data ---
        self.reference_ranges: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.cuts: Dict[int, Dict[str, Any]] = {}
        self.corrections: Dict[Tuple[int, str, str, int, str], str] = {}
        self.site_replies: Dict[str, Any] = {}
        self.site_default: list = []
        self.monitor_decisions: Dict[str, Any] = {}
        # --- bookkeeping ---
        self._built: bool = False
        self._build_cut: Optional[int] = None
        self._build_time: float = 0.0
        self._node_count: int = 0
        self._edge_count: int = 0

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def build(self, cut: Optional[int] = None) -> None:
        """(Re-)build the graph, optionally filtered to *cut*."""
        t0 = time.time()
        self._build_cut = cut
        max_cut = cut if cut is not None else 999

        # ---- 1. reference data -----------------------------------------
        data_sub = os.path.join(self.data_dir, "data")
        resp_sub = os.path.join(self.data_dir, "responses")

        self.reference_ranges = load_reference_ranges(
            os.path.join(data_sub, "reference_ranges.csv")
        )
        self.cuts = load_cuts(os.path.join(data_sub, "cuts.csv"))
        self.corrections = load_corrections(
            os.path.join(data_sub, "corrections.csv")
        )
        self.site_replies, self.site_default = load_site_replies(
            os.path.join(resp_sub, "site_replies.json")
        )
        self.monitor_decisions = load_monitor_decisions(
            os.path.join(resp_sub, "monitor_decisions.json")
        )

        # ---- 2. domain data --------------------------------------------
        raw_data = load_all_data(self.data_dir, cut)

        # apply corrections
        for domain in raw_data:
            raw_data[domain] = apply_corrections(
                raw_data[domain], self.corrections, max_cut
            )

        # ---- 3. normalize -----------------------------------------------
        self.records_by_domain = {}
        self.records_by_subject = defaultdict(lambda: defaultdict(list))

        for domain, records in raw_data.items():
            normalized: List[NormalizedRecord] = []
            for r in records:
                try:
                    ref = RecordRef(domain=r.domain, usubjid=r.usubjid, seq=r.seq)
                except Exception:
                    continue  # skip malformed
                parsed = self._parse_record(r)
                norm = NormalizedRecord(
                    ref=ref,
                    data=r.data,
                    cut_available=r.cut_available,
                    corrected_at_cut=r.corrected_at_cut,
                    parsed=parsed,
                )
                normalized.append(norm)
                self.records_by_subject[r.usubjid][domain].append(norm)
            self.records_by_domain[domain] = normalized

        # ---- 4. build indexes -------------------------------------------
        self._build_indexes()

        self._build_time = time.time() - t0
        self._built = True

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------

    def _build_indexes(self) -> None:
        """Create secondary indexes for fast look-up."""
        self._by_ref = {}
        self._by_site = defaultdict(set)
        self._by_visit = defaultdict(lambda: defaultdict(list))
        self._subjects_list = sorted(self.records_by_subject.keys())

        node_count = 0
        edge_count = 0

        for domain, records in self.records_by_domain.items():
            node_count += len(records)
            for rec in records:
                ref_str = str(rec.ref)
                self._by_ref[ref_str] = rec

                # site index (from USUBJID)
                site = self._extract_site(rec.ref.usubjid)
                if site:
                    self._by_site[site].add(rec.ref.usubjid)

                # visit index (for domains that have one)
                visit = rec.parsed.get("visit") or rec.data.get("VISIT")
                if visit:
                    self._by_visit[rec.ref.usubjid][visit].append(rec)

        # Edge count: each non-DM record has an implicit edge to its
        # subject DM node, plus edges between records sharing a visit.
        for subj, domains in self.records_by_subject.items():
            n_records = sum(len(recs) for recs in domains.values())
            # subject -> record edges
            edge_count += n_records
            # DM -> each domain group
            edge_count += len(domains)

        self._node_count = node_count + len(self._subjects_list)  # subject nodes
        self._edge_count = edge_count

    # ------------------------------------------------------------------
    # Record parser (domain-aware)
    # ------------------------------------------------------------------

    def _parse_record(self, record: RawRecord) -> Dict[str, Any]:
        """Parse record fields based on domain."""
        parsed: Dict[str, Any] = {}
        data = record.data

        if record.domain == Domain.DM:
            parsed["age"] = parse_numeric(data.get("AGE"))
            parsed["sex"] = data.get("SEX", "")
            parsed["arm"] = data.get("ARM", "")
            parsed["rfstdtc"] = parse_date(data.get("RFSTDTC"))
            parsed["brthdtc"] = parse_date(data.get("BRTHDTC"))
            parsed["scr_hba1c"] = parse_numeric(data.get("SCR_HBA1C"))
            parsed["siteid"] = data.get("SITEID", "")
            parsed["country"] = data.get("COUNTRY", "")
            parsed["dminit"] = data.get("DMINIT", "")

        elif record.domain == Domain.LB:
            parsed["visit"] = data.get("VISIT", "")
            parsed["lbdtc"] = parse_date(data.get("LBDTC"))
            parsed["lbtestcd"] = data.get("LBTESTCD", "")
            parsed["lborres"] = data.get("LBORRES", "")
            parsed["lborresu"] = data.get("LBORRESU", "")
            parsed["lborres_numeric"] = parse_numeric(data.get("LBORRES"))

            # Determine lab (S07 uses local lab)
            siteid = self._extract_site(record.usubjid)
            parsed["lab"] = "S07" if siteid == "S07" else "CENTRAL"

            # Reference range
            key = (parsed["lbtestcd"], parsed["lab"])
            ref = self.reference_ranges.get(key)
            if ref:
                parsed["ref_low"] = ref["low"]
                parsed["ref_high"] = ref["high"]
                parsed["ref_unit"] = ref["unit"]
                # Standard-unit reference range (convert S07 µkat/L -> U/L)
                if parsed["lbtestcd"] in ("ALT", "AST") and parsed["lab"] == "S07":
                    parsed["ref_low_std"] = ref["low"] * 60
                    parsed["ref_high_std"] = ref["high"] * 60
                else:
                    parsed["ref_low_std"] = ref["low"]
                    parsed["ref_high_std"] = ref["high"]
            else:
                # Fallback to CENTRAL ranges for tests without S07-specific range
                central_ref = self.reference_ranges.get((parsed["lbtestcd"], "CENTRAL"))
                if central_ref:
                    parsed["ref_low"] = central_ref["low"]
                    parsed["ref_high"] = central_ref["high"]
                    parsed["ref_unit"] = central_ref["unit"]
                    parsed["ref_low_std"] = central_ref["low"]
                    parsed["ref_high_std"] = central_ref["high"]

            # Standardised numeric value
            if parsed["lborres_numeric"] is not None:
                if parsed["lbtestcd"] in ("ALT", "AST") and parsed["lab"] == "S07":
                    parsed["lborres_std"] = parsed["lborres_numeric"] * 60
                else:
                    parsed["lborres_std"] = parsed["lborres_numeric"]

        elif record.domain == Domain.AE:
            parsed["aeterm"] = data.get("AETERM", "")
            parsed["aesev"] = data.get("AESEV", "")
            parsed["aeser"] = data.get("AESER", "")
            parsed["aeshosp"] = data.get("AESHOSP", "")
            parsed["aestdtc"] = parse_date(data.get("AESTDTC"))
            parsed["aeendtc"] = parse_date(data.get("AEENDTC"))
            parsed["aeout"] = data.get("AEOUT", "")
            parsed["aenarr"] = data.get("AENARR", "")
            # Serious if AESER=Y or AESHOSP=Y
            parsed["is_serious"] = (
                parsed["aeser"] == "Y" or parsed["aeshosp"] == "Y"
            )

        elif record.domain == Domain.VS:
            parsed["visit"] = data.get("VISIT", "")
            parsed["vsdtc"] = parse_date(data.get("VSDTC"))
            parsed["vstestcd"] = data.get("VSTESTCD", "")
            parsed["vsorres"] = parse_numeric(data.get("VSORRES"))
            parsed["vsorresu"] = data.get("VSORRESU", "")

        elif record.domain == Domain.EX:
            parsed["visit"] = data.get("VISIT", "")
            parsed["exstdtc"] = parse_date(data.get("EXSTDTC"))
            parsed["exdose"] = parse_numeric(data.get("EXDOSE"))
            parsed["exdosu"] = data.get("EXDOSU", "")
            parsed["extrt"] = data.get("EXTRT", "")

        elif record.domain == Domain.CM:
            parsed["cmtrt"] = data.get("CMTRT", "")
            parsed["cmclas"] = data.get("CMCLAS", "")
            parsed["cmindc"] = data.get("CMINDC", "")
            parsed["cmstdtc"] = parse_date(data.get("CMSTDTC"))
            parsed["cmdose"] = parse_numeric(data.get("CMDOSE"))

        elif record.domain == Domain.DS:
            parsed["dsdecod"] = data.get("DSDECOD", "")
            parsed["dsstdtc"] = parse_date(data.get("DSSTDTC"))
            parsed["dsterm"] = data.get("DSTERM", "")

        elif record.domain == Domain.MH:
            parsed["mhterm"] = data.get("MHTERM", "")

        elif record.domain == Domain.EG:
            parsed["visit"] = data.get("VISIT", "")
            parsed["egdtc"] = parse_date(data.get("EGDTC"))
            parsed["egtestcd"] = data.get("EGTESTCD", "")
            parsed["egorres"] = parse_numeric(data.get("EGORRES"))
            parsed["egorresu"] = data.get("EGORRESU", "")

        return parsed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_site(usubjid: str) -> str:
        """``'042-S07-001'  ->  'S07'``"""
        parts = usubjid.split("-")
        return parts[1] if len(parts) >= 2 else ""

    def _get_siteid(self, usubjid: str) -> str:
        """Alias kept for backward compat with reasoning module."""
        return self._extract_site(usubjid)

    # ------------------------------------------------------------------
    # Patient 360
    # ------------------------------------------------------------------

    def patient360(self, usubjid: str) -> Patient360:
        """Return a ``Patient360`` dataclass for *usubjid*.

        Runs in O(records-for-subject) -- no full-dataset scan.
        """
        if not self._built:
            self.build()

        subject_data = self.records_by_subject.get(usubjid, {})

        def _to_dicts(recs: List[NormalizedRecord]) -> List[Dict[str, Any]]:
            return [
                {
                    **r.data,
                    **r.parsed,
                    "ref": r.ref,
                    "cut_available": r.cut_available,
                }
                for r in recs
            ]

        # DM (single record per subject)
        dm_recs = subject_data.get(Domain.DM, [])
        dm_dict: Dict[str, Any] = {}
        if dm_recs:
            r0 = dm_recs[0]
            dm_dict = {**r0.data, **r0.parsed, "ref": r0.ref}

        return Patient360(
            usubjid=usubjid,
            dm=dm_dict,
            ae=_to_dicts(subject_data.get(Domain.AE, [])),
            lb=_to_dicts(subject_data.get(Domain.LB, [])),
            vs=_to_dicts(subject_data.get(Domain.VS, [])),
            ex=_to_dicts(subject_data.get(Domain.EX, [])),
            cm=_to_dicts(subject_data.get(Domain.CM, [])),
            ds=_to_dicts(subject_data.get(Domain.DS, [])),
            mh=_to_dicts(subject_data.get(Domain.MH, [])),
            eg=_to_dicts(subject_data.get(Domain.EG, [])),
        )

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> GraphStats:
        """Return a ``GraphStats`` dataclass."""
        if not self._built:
            self.build()
        return GraphStats(
            nodes=self._node_count,
            edges=self._edge_count,
            subjects=len(self.records_by_subject),
            records_by_domain={
                d.value: len(r) for d, r in self.records_by_domain.items()
            },
            cuts=len(self.cuts),
        )

    # ------------------------------------------------------------------
    # Query helpers (used by reasoning / atlas)
    # ------------------------------------------------------------------

    def get_subjects(self) -> List[str]:
        """Return sorted list of all USUBJIDs."""
        if not self._built:
            self.build()
        return list(self._subjects_list)

    def get_record(self, ref_str: str) -> Optional[NormalizedRecord]:
        """Look up a single record by its string ref (``'LB|042-S01-001|12'``)."""
        return self._by_ref.get(ref_str)

    def get_subjects_at_site(self, site: str) -> Set[str]:
        """Return subjects enrolled at *site* (e.g. ``'S07'``)."""
        return self._by_site.get(site, set())

    def get_protocol_version(self, cut: Optional[int] = None) -> int:
        """Return the protocol version in force at *cut* (default: build cut)."""
        c = cut if cut is not None else (self._build_cut or max(self.cuts, default=1))
        # Find the highest cut <= c
        best = 1
        for k, v in self.cuts.items():
            if k <= c:
                best = v.get("protocol_version", best)
        return best

    def query(
        self,
        domain: Domain,
        usubjid: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[NormalizedRecord]:
        """Query records by domain with optional subject + field filters."""
        if not self._built:
            self.build()

        if usubjid:
            records = self.records_by_subject.get(usubjid, {}).get(domain, [])
        else:
            records = self.records_by_domain.get(domain, [])

        if not filters:
            return records

        result: List[NormalizedRecord] = []
        for r in records:
            match = True
            for key, value in filters.items():
                actual = r.parsed.get(key, r.data.get(key))
                if actual != value:
                    match = False
                    break
            if match:
                result.append(r)
        return result