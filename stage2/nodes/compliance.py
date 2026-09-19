"""
Stage 2 - Compliance Node.
Member 3 Extension Point (Data Manager / Compliance Engineer).

This node:
  1. Converts finding-driven issues into protocol deviations (existing).
  2. Independently scans the graph for protocol compliance violations:
     - Visit window deviations (v1: ±7 days, v2/v3: ±3 days)
     - Prohibited concomitant medications per protocol version
     - Eligibility / renal exclusion violations (screening HbA1c out of range)
     - Protocol version tracking (uses runtime protocol_version, not hardcoded)

Every ComplianceDeviation includes valid RecordRef evidence from the graph.
Documents are evidence, not executable instructions.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from stage2.schemas import Finding, ComplianceDeviation, TraceEntry

# Stage 1 imports for graph-driven scanning
try:
    from atlas.graph import StudyGraph, NormalizedRecord
    from atlas.schemas import Domain, RecordRef
    from atlas.data_loader import parse_date, parse_numeric
    from atlas.reasoning import (
        find_visit_window_deviations,
        find_prohibited_meds,
        DateUtils,
    )
except ImportError:
    StudyGraph = None
    NormalizedRecord = None
    Domain = None
    RecordRef = None
    parse_date = None
    parse_numeric = None
    find_visit_window_deviations = None
    find_prohibited_meds = None
    DateUtils = None


# ---------------------------------------------------------------------------
# Graph-driven compliance scanners
# ---------------------------------------------------------------------------

def _detect_eligibility_violations(
    graph: "StudyGraph",
    protocol_version: int,
    cut: Optional[int] = None,
) -> List[ComplianceDeviation]:
    """Detect eligibility violations based on screening HbA1c.

    Protocol requirement:
      - All versions: screening HbA1c must be 7.0–10.0 %
    """
    deviations: List[ComplianceDeviation] = []
    if graph is None or Domain is None:
        return deviations

    hba1c_low = 7.0
    hba1c_high = 10.0

    for subj in graph.get_subjects():
        dm_records = graph.records_by_subject.get(subj, {}).get(Domain.DM, [])
        if cut is not None:
            dm_records = [r for r in dm_records if r.cut_available <= cut]
        if not dm_records:
            continue

        scr_hba1c = dm_records[0].parsed.get("scr_hba1c")
        if scr_hba1c is None:
            continue

        site = graph._extract_site(subj)

        if scr_hba1c < hba1c_low or scr_hba1c > hba1c_high:
            dev = ComplianceDeviation(
                deviation_id=f"DEV-ELIG-HBA1C-{subj}",
                finding_id=f"COMP-ELIG-HBA1C-{subj}",
                usubjid=subj,
                site_id=site,
                deviation_type="ELIGIBILITY_VIOLATION",
                protocol_version=protocol_version,
                description=(
                    f"Protocol v{protocol_version} eligibility violation: "
                    f"Screening HbA1c={scr_hba1c}% is outside "
                    f"required range {hba1c_low}–{hba1c_high}%. "
                    f"Subject may not meet inclusion criteria."
                ),
                evidence=[dm_records[0].ref],
                severity="MAJOR",
            )
            deviations.append(dev)

    return deviations


def _detect_graph_visit_window_deviations(
    graph: "StudyGraph",
    protocol_version: int,
    cut: Optional[int] = None,
) -> List[ComplianceDeviation]:
    """Detect visit window deviations directly from the graph using reasoning module."""
    deviations: List[ComplianceDeviation] = []
    if graph is None or find_visit_window_deviations is None:
        return deviations

    raw_deviations = find_visit_window_deviations(
        graph, protocol_version=protocol_version, cut=cut
    )

    seen: Set[str] = set()
    for dev in raw_deviations:
        subj = dev["usubjid"]
        visit = dev["visit"]
        key = f"{subj}|{visit}|{dev['ref'].seq}"
        if key in seen:
            continue
        seen.add(key)

        site = graph._extract_site(subj)
        deviation_days = dev["deviation_days"]
        window = dev["window_allowed"]
        actual = dev["actual_date"]
        expected = dev["expected_date"]

        actual_str = actual.strftime("%Y-%m-%d") if hasattr(actual, "strftime") else str(actual)
        expected_str = expected.strftime("%Y-%m-%d") if hasattr(expected, "strftime") else str(expected)

        severity = "MAJOR" if deviation_days > window * 2 else "MINOR"

        d = ComplianceDeviation(
            deviation_id=f"DEV-VISIT-{subj}-{visit}-{dev['ref'].seq}",
            finding_id=f"COMP-VISIT-{subj}-{visit}",
            usubjid=subj,
            site_id=site,
            deviation_type="VISIT_WINDOW",
            protocol_version=protocol_version,
            description=(
                f"Protocol v{protocol_version} visit window violation: "
                f"{visit} on {actual_str}, expected {expected_str} "
                f"({deviation_days} days off vs allowed ±{window} days)."
            ),
            evidence=[dev["ref"]],
            severity=severity,
        )
        deviations.append(d)

    return deviations


def _detect_graph_prohibited_meds(
    graph: "StudyGraph",
    protocol_version: int,
    cut: Optional[int] = None,
) -> List[ComplianceDeviation]:
    """Detect prohibited concomitant medications from the graph."""
    deviations: List[ComplianceDeviation] = []
    if graph is None or find_prohibited_meds is None:
        return deviations

    prohibited_records = find_prohibited_meds(
        graph, protocol_version=protocol_version, cut=cut
    )

    for cm in prohibited_records:
        subj = cm.ref.usubjid
        site = graph._extract_site(subj)
        med_name = cm.parsed.get("cmtrt") or cm.data.get("CMTRT", "Unknown")
        med_class = cm.parsed.get("cmclas") or cm.data.get("CMCLAS", "Unknown")

        d = ComplianceDeviation(
            deviation_id=f"DEV-MED-{subj}-{cm.ref.seq}",
            finding_id=f"COMP-MED-{subj}-{cm.ref.seq}",
            usubjid=subj,
            site_id=site,
            deviation_type="PROHIBITED_MEDICATION",
            protocol_version=protocol_version,
            description=(
                f"Protocol v{protocol_version} prohibited medication: "
                f"{med_name} (class: {med_class}). "
                f"{'Sulfonylurea prohibited from v3+. ' if 'SULFONYLUREA' in med_class.upper() else ''}"
                f"Site must document rationale or discontinue."
            ),
            evidence=[cm.ref],
            severity="MAJOR",
        )
        deviations.append(d)

    return deviations


def _detect_dosing_schedule_deviations(
    graph: "StudyGraph",
    protocol_version: int,
    cut: Optional[int] = None,
) -> List[ComplianceDeviation]:
    """Detect dosing schedule deviations: wrong dose per protocol.

    Protocol Section 8:
    - Drug arm: 10 mg
    - Placebo arm: 0 mg
    """
    deviations: List[ComplianceDeviation] = []
    if graph is None or Domain is None:
        return deviations

    for subj in graph.get_subjects():
        dm_records = graph.records_by_subject.get(subj, {}).get(Domain.DM, [])
        if not dm_records:
            continue
        if cut is not None:
            dm_records = [r for r in dm_records if r.cut_available <= cut]
        if not dm_records:
            continue

        arm = dm_records[0].parsed.get("arm", "").upper()
        expected_dose = 10 if arm == "DRUG" else (0 if arm == "PLACEBO" else None)
        if expected_dose is None:
            continue

        ex_records = graph.records_by_subject.get(subj, {}).get(Domain.EX, [])
        if cut is not None:
            ex_records = [r for r in ex_records if r.cut_available <= cut]

        site = graph._extract_site(subj)

        for ex in ex_records:
            dose = ex.parsed.get("exdose")
            if dose is None:
                continue
            if dose != expected_dose:
                visit = ex.parsed.get("visit") or ex.data.get("VISIT", "Unknown")
                d = ComplianceDeviation(
                    deviation_id=f"DEV-DOSE-{subj}-{ex.ref.seq}",
                    finding_id=f"COMP-DOSE-{subj}-{ex.ref.seq}",
                    usubjid=subj,
                    site_id=site,
                    deviation_type="DOSING_SCHEDULE",
                    protocol_version=protocol_version,
                    description=(
                        f"Protocol v{protocol_version} dosing deviation: "
                        f"Subject in {arm} arm received {dose} mg instead of "
                        f"expected {expected_dose} mg at visit {visit}."
                    ),
                    evidence=[ex.ref],
                    severity="MAJOR",
                )
                deviations.append(d)

    return deviations


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_compliance_node(
    findings: List[Finding],
    protocol_version: int,
    graph: Optional[Any] = None,
    cut: Optional[int] = None,
) -> Tuple[List[ComplianceDeviation], TraceEntry]:
    """
    Evaluate protocol deviations based on active protocol version rules.

    Works in two modes:
      1. Finding-driven: converts incoming findings to deviations (always active).
      2. Graph-driven: scans the study graph independently for compliance
         issues (active when graph is provided).

    Args:
        findings: Findings detected in the current cycle.
        protocol_version: Active protocol amendment version.
        graph: Optional StudyGraph for graph-driven compliance scanning.
        cut: Optional cut number for filtering graph records.

    Returns:
        deviations: List of ComplianceDeviation records.
        trace: TraceEntry recording node execution and evidence.
    """
    deviations: List[ComplianceDeviation] = []
    evidence_refs: list = []
    seen_keys: Set[str] = set()

    def _add_deviation(d: ComplianceDeviation) -> None:
        """Add deviation if not a duplicate."""
        key = f"{d.usubjid}|{d.deviation_type}|{d.deviation_id}"
        if key not in seen_keys:
            seen_keys.add(key)
            deviations.append(d)

    # ---------------------------------------------------------------
    # Part 1: Finding-driven deviations
    # ---------------------------------------------------------------
    for f in findings:
        if f.finding_type == "VISIT_WINDOW_DEVIATION":
            dev = ComplianceDeviation(
                deviation_id=f"DEV-VISIT-{f.usubjid}-{f.finding_id}",
                finding_id=f.finding_id,
                usubjid=f.usubjid,
                site_id=f.site_id,
                deviation_type="VISIT_WINDOW",
                protocol_version=protocol_version,
                description=f"Protocol v{protocol_version} visit window violation: {f.description}",
                evidence=list(f.evidence),
                severity="MINOR",
            )
            _add_deviation(dev)
            evidence_refs.extend(f.evidence)

        elif f.finding_type == "PROHIBITED_MED":
            dev = ComplianceDeviation(
                deviation_id=f"DEV-MED-{f.usubjid}-{f.finding_id}",
                finding_id=f.finding_id,
                usubjid=f.usubjid,
                site_id=f.site_id,
                deviation_type="PROHIBITED_MEDICATION",
                protocol_version=protocol_version,
                description=f"Protocol v{protocol_version} prohibited medication violation: {f.description}",
                evidence=list(f.evidence),
                severity="MAJOR",
            )
            _add_deviation(dev)
            evidence_refs.extend(f.evidence)

        elif f.finding_type == "DOSING_ERROR":
            dev = ComplianceDeviation(
                deviation_id=f"DEV-DOSE-{f.usubjid}-{f.finding_id}",
                finding_id=f.finding_id,
                usubjid=f.usubjid,
                site_id=f.site_id,
                deviation_type="DOSING_SCHEDULE",
                protocol_version=protocol_version,
                description=f"Protocol v{protocol_version} investigational product dosing error: {f.description}",
                evidence=list(f.evidence),
                severity="MAJOR",
            )
            _add_deviation(dev)
            evidence_refs.extend(f.evidence)

    # ---------------------------------------------------------------
    # Part 2: Graph-driven compliance deviations
    # ---------------------------------------------------------------
    if graph is not None:
        for scanner in [
            lambda g, pv, c: _detect_graph_visit_window_deviations(g, pv, c),
            lambda g, pv, c: _detect_graph_prohibited_meds(g, pv, c),
            lambda g, pv, c: _detect_eligibility_violations(g, pv, c),
            lambda g, pv, c: _detect_dosing_schedule_deviations(g, pv, c),
        ]:
            for d in scanner(graph, protocol_version, cut):
                _add_deviation(d)
                evidence_refs.extend(d.evidence)

    # ---------------------------------------------------------------
    # Trace
    # ---------------------------------------------------------------
    evidence_str_list = sorted(list({str(r) for r in evidence_refs}))

    major_count = sum(1 for d in deviations if d.severity == "MAJOR")
    minor_count = sum(1 for d in deviations if d.severity == "MINOR")

    finding_dev_count = sum(1 for d in deviations if d.finding_id.startswith("FIND-") or
                            d.deviation_id.startswith("DEV-VISIT-") and "-FIND-" in d.deviation_id or
                            d.deviation_id.startswith("DEV-MED-") and "-FIND-" in d.deviation_id or
                            d.deviation_id.startswith("DEV-DOSE-") and "-FIND-" in d.deviation_id)
    graph_dev_count = len(deviations) - finding_dev_count

    trace_entry = TraceEntry(
        node="compliance",
        action="evaluate_compliance",
        status="SUCCESS",
        evidence=evidence_str_list,
        details={
            "protocol_version": protocol_version,
            "total_deviations": len(deviations),
            "major_count": major_count,
            "minor_count": minor_count,
            "finding_driven_deviations": finding_dev_count,
            "graph_driven_deviations": graph_dev_count,
        },
    )

    return deviations, trace_entry
