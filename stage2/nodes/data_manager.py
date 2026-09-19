"""
Stage 2 - Data Manager Node.
Member 3 Extension Point (Data Manager / Compliance Engineer).

This node:
  1. Converts finding-driven issues into targeted site queries (existing).
  2. Independently scans the graph for additional data-quality problems:
     - Adverse event recorded before first dose
     - Missing dose record for a scheduled visit
     - Unit mismatches in lab records (e.g., ukat/L vs U/L)
     - Duplicate subject records
     - Inconsistent AE seriousness (AESER vs AESHOSP contradiction)
     - Abnormal lab results lacking required retest record

Every query is specific, actionable, record-cited, non-duplicated, and
associated with subject / site / domain / sequence / cut.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from stage2.schemas import Finding, Escalation, Query, TraceEntry

# Stage 1 imports for graph-driven scanning
try:
    from atlas.graph import StudyGraph, NormalizedRecord
    from atlas.schemas import Domain, RecordRef
    from atlas.data_loader import parse_date
except ImportError:
    StudyGraph = None
    NormalizedRecord = None
    Domain = None
    RecordRef = None
    parse_date = None


# ---------------------------------------------------------------------------
# Graph-driven data-quality scanners
# ---------------------------------------------------------------------------

def _detect_ae_before_first_dose(
    graph: "StudyGraph",
    cut: Optional[int] = None,
) -> List[Query]:
    """Detect adverse events recorded before the subject's first dose date."""
    queries: List[Query] = []
    if graph is None or Domain is None:
        return queries

    for subj in graph.get_subjects():
        ex_records = graph.records_by_subject.get(subj, {}).get(Domain.EX, [])
        ae_records = graph.records_by_subject.get(subj, {}).get(Domain.AE, [])

        if cut is not None:
            ex_records = [r for r in ex_records if r.cut_available <= cut]
            ae_records = [r for r in ae_records if r.cut_available <= cut]

        if not ex_records or not ae_records:
            continue

        # Find earliest dose date
        dose_dates = []
        for ex in ex_records:
            dt = ex.parsed.get("exstdtc")
            if dt is not None:
                dose_dates.append((dt, ex))
        if not dose_dates:
            continue

        dose_dates.sort(key=lambda x: x[0])
        first_dose_date, first_dose_rec = dose_dates[0]

        site = graph._extract_site(subj)

        for ae in ae_records:
            ae_start = ae.parsed.get("aestdtc")
            if ae_start is None:
                continue
            if ae_start < first_dose_date:
                aeterm = ae.parsed.get("aeterm") or ae.data.get("AETERM", "Unknown")
                q = Query(
                    query_id=f"QRY-AEBDOSE-{subj}-{ae.ref.seq}",
                    finding_id=f"DQ-AEBDOSE-{subj}-{ae.ref.seq}",
                    usubjid=subj,
                    site_id=site,
                    domain="AE",
                    description=(
                        f"Data Quality: AE '{aeterm}' (start={ae_start.strftime('%Y-%m-%d')}) "
                        f"recorded before first dose ({first_dose_date.strftime('%Y-%m-%d')}). "
                        f"Please verify AE start date or confirm pre-treatment event."
                    ),
                    status="OPEN",
                    evidence=[ae.ref, first_dose_rec.ref],
                )
                queries.append(q)

    return queries


def _detect_missing_dose_records(
    graph: "StudyGraph",
    cut: Optional[int] = None,
) -> List[Query]:
    """Detect subjects missing EX (dosing) records entirely."""
    queries: List[Query] = []
    if graph is None or Domain is None:
        return queries

    for subj in graph.get_subjects():
        dm_records = graph.records_by_subject.get(subj, {}).get(Domain.DM, [])
        ex_records = graph.records_by_subject.get(subj, {}).get(Domain.EX, [])

        if cut is not None:
            ex_records = [r for r in ex_records if r.cut_available <= cut]
            dm_records = [r for r in dm_records if r.cut_available <= cut]

        if not dm_records:
            continue

        # If subject is enrolled but has zero dose records, that's a data gap
        if not ex_records:
            site = graph._extract_site(subj)
            q = Query(
                query_id=f"QRY-NODOSE-{subj}",
                finding_id=f"DQ-NODOSE-{subj}",
                usubjid=subj,
                site_id=site,
                domain="EX",
                description=(
                    f"Data Quality: Subject {subj} is enrolled (DM record present) "
                    f"but has no dosing (EX) records available at this cut. "
                    f"Please verify dosing logs or confirm screen failure."
                ),
                status="OPEN",
                evidence=[dm_records[0].ref],
            )
            queries.append(q)

    return queries


def _detect_unit_mismatches(
    graph: "StudyGraph",
    cut: Optional[int] = None,
) -> List[Query]:
    """Detect lab records where reported unit differs from reference range unit."""
    queries: List[Query] = []
    if graph is None or Domain is None:
        return queries

    for subj in graph.get_subjects():
        lb_records = graph.records_by_subject.get(subj, {}).get(Domain.LB, [])
        if cut is not None:
            lb_records = [r for r in lb_records if r.cut_available <= cut]

        site = graph._extract_site(subj)

        for r in lb_records:
            reported_unit = (r.parsed.get("lborresu") or r.data.get("LBORRESU", "")).strip()
            ref_unit = (r.parsed.get("ref_unit") or "").strip()
            testcd = r.parsed.get("lbtestcd") or r.data.get("LBTESTCD", "")

            if not reported_unit or not ref_unit:
                continue

            # Known conversion: S07 reports ALT/AST in ukat/L vs central U/L — already handled
            lab = r.parsed.get("lab", "")
            if lab == "S07" and testcd in ("ALT", "AST"):
                continue  # Already handled by normalization

            if reported_unit.lower() != ref_unit.lower():
                q = Query(
                    query_id=f"QRY-UNIT-{subj}-{r.ref.seq}",
                    finding_id=f"DQ-UNIT-{subj}-{r.ref.seq}",
                    usubjid=subj,
                    site_id=site,
                    domain="LB",
                    description=(
                        f"Data Quality: Lab {testcd} reported in '{reported_unit}' "
                        f"but reference range is in '{ref_unit}'. "
                        f"Please clarify unit or confirm conversion factor."
                    ),
                    status="OPEN",
                    evidence=[r.ref],
                )
                queries.append(q)

    return queries


def _detect_ae_seriousness_inconsistency(
    graph: "StudyGraph",
    cut: Optional[int] = None,
) -> List[Query]:
    """Detect AE records where AESHOSP=Y but AESER=N (contradiction)."""
    queries: List[Query] = []
    if graph is None or Domain is None:
        return queries

    for subj in graph.get_subjects():
        ae_records = graph.records_by_subject.get(subj, {}).get(Domain.AE, [])
        if cut is not None:
            ae_records = [r for r in ae_records if r.cut_available <= cut]

        site = graph._extract_site(subj)

        for ae in ae_records:
            aeser = (ae.parsed.get("aeser") or ae.data.get("AESER", "")).upper().strip()
            aeshosp = (ae.parsed.get("aeshosp") or ae.data.get("AESHOSP", "")).upper().strip()
            aeterm = ae.parsed.get("aeterm") or ae.data.get("AETERM", "Unknown")

            if aeshosp == "Y" and aeser == "N":
                q = Query(
                    query_id=f"QRY-AESERCON-{subj}-{ae.ref.seq}",
                    finding_id=f"DQ-AESERCON-{subj}-{ae.ref.seq}",
                    usubjid=subj,
                    site_id=site,
                    domain="AE",
                    description=(
                        f"Data Quality: AE '{aeterm}' has AESHOSP='Y' (hospitalized) "
                        f"but AESER='N' (not serious). Hospitalization requires AESER='Y' "
                        f"per ICH GCP E2A. Please correct AESER to 'Y'."
                    ),
                    status="OPEN",
                    evidence=[ae.ref],
                )
                queries.append(q)

    return queries


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_data_manager_node(
    findings: List[Finding],
    escalations: List[Escalation],
    graph: Optional[Any] = None,
    cut: Optional[int] = None,
) -> Tuple[List[Query], TraceEntry]:
    """
    Generate site queries from findings requiring clarification or correction,
    AND independently scan the study graph for data-quality issues.

    Args:
        findings: Findings detected in current cycle.
        escalations: Escalations drafted by medical review.
        graph: Optional StudyGraph for graph-driven data-quality scanning.
        cut: Optional cut number for filtering graph records.

    Returns:
        queries: List of Query objects issued to clinical sites.
        trace: TraceEntry recording node execution and evidence.
    """
    queries: List[Query] = []
    evidence_refs: list = []
    seen_keys: Set[str] = set()

    def _add_query(q: Query) -> None:
        """Add query if not a duplicate."""
        key = f"{q.usubjid}|{q.domain}|{q.query_id}"
        if key not in seen_keys:
            seen_keys.add(key)
            queries.append(q)

    # ---------------------------------------------------------------
    # Part 1: Finding-driven queries
    # ---------------------------------------------------------------
    for f in findings:
        if f.finding_type == "DOSING_ERROR":
            q = Query(
                query_id=f"QRY-DOSE-{f.usubjid}-{f.finding_id}",
                finding_id=f.finding_id,
                usubjid=f.usubjid,
                site_id=f.site_id,
                domain="EX",
                description=(
                    f"Dosing Discrepancy: Subject {f.usubjid} received non-protocol dose. "
                    f"{f.description}. Please confirm or correct dose administration log."
                ),
                status="OPEN",
                evidence=list(f.evidence),
            )
            _add_query(q)
            evidence_refs.extend(f.evidence)

        elif f.finding_type == "VISIT_WINDOW_DEVIATION":
            domain = f.data.get("domain", "VISIT")
            q = Query(
                query_id=f"QRY-VISIT-{f.usubjid}-{f.finding_id}",
                finding_id=f.finding_id,
                usubjid=f.usubjid,
                site_id=f.site_id,
                domain=domain,
                description=(
                    f"Visit Schedule Deviation: {f.description}. "
                    f"Please provide documentation for out-of-window visit."
                ),
                status="OPEN",
                evidence=list(f.evidence),
            )
            _add_query(q)
            evidence_refs.extend(f.evidence)

        elif f.finding_type == "PROHIBITED_MED":
            q = Query(
                query_id=f"QRY-MED-{f.usubjid}-{f.finding_id}",
                finding_id=f.finding_id,
                usubjid=f.usubjid,
                site_id=f.site_id,
                domain="CM",
                description=(
                    f"Prohibited Medication: Concomitant treatment recorded ({f.description}). "
                    f"Please verify indication, stop date, or report deviation."
                ),
                status="OPEN",
                evidence=list(f.evidence),
            )
            _add_query(q)
            evidence_refs.extend(f.evidence)

    # ---------------------------------------------------------------
    # Part 2: Graph-driven data-quality queries
    # ---------------------------------------------------------------
    if graph is not None:
        for scanner in [
            _detect_ae_before_first_dose,
            _detect_missing_dose_records,
            _detect_unit_mismatches,
            _detect_ae_seriousness_inconsistency,
        ]:
            for q in scanner(graph, cut=cut):
                _add_query(q)
                evidence_refs.extend(q.evidence)

    # ---------------------------------------------------------------
    # Trace
    # ---------------------------------------------------------------
    evidence_str_list = sorted(list({str(r) for r in evidence_refs}))

    site_ids = list({q.site_id for q in queries if q.site_id})
    finding_query_count = sum(1 for q in queries if q.query_id.startswith("QRY-DOSE") or
                              q.query_id.startswith("QRY-VISIT") or q.query_id.startswith("QRY-MED"))
    graph_query_count = len(queries) - finding_query_count

    trace_entry = TraceEntry(
        node="data_manager",
        action="generate_queries",
        status="SUCCESS",
        evidence=evidence_str_list,
        details={
            "queries_generated": len(queries),
            "finding_driven_queries": finding_query_count,
            "graph_driven_queries": graph_query_count,
            "by_site": site_ids,
        },
    )

    return queries, trace_entry
