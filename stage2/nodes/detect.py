"""
Stage 2 - Detect Node.
Member 1: Detection + Orchestration Engineer.

This node inspects the clinical trial data at the given cut and protocol version
using the existing Stage 1 Atlas knowledge graph and reasoning engines.
It produces a structured list of Finding objects, each backed by valid RecordRef evidence.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from stage1.atlas import Atlas, RecordRef
from atlas.reasoning import (
    find_hys_law_candidates,
    find_dosing_errors,
    find_prohibited_meds,
    find_visit_window_deviations,
    find_saes,
)
from stage2.schemas import Finding, TraceEntry


def run_detect_node(
    atlas: Atlas,
    cut: int,
    protocol_version: int,
) -> Tuple[List[Finding], TraceEntry]:
    """
    Execute detection over the study knowledge graph at the specified cut and protocol version.

    Returns:
        findings: List of clinical/protocol/data findings with verified RecordRef citations.
        trace: TraceEntry recording node execution, counts, and cited evidence.
    """
    graph = atlas.graph
    # Align graph to specified cut if needed
    if getattr(graph, "_build_cut", None) != cut:
        graph.build(cut=cut)

    findings: List[Finding] = []
    all_evidence_refs: List[RecordRef] = []

    # 1. Hy's Law Candidates
    hys_candidates = find_hys_law_candidates(graph, cut=cut, apply_exclusions=False)
    for c in hys_candidates:
        subj = c["usubjid"]
        site = graph._extract_site(subj)
        trans_val = c.get("alt_val") or c.get("ast_val") or "elevated"
        bili_val = c.get("bili_val") or "elevated"
        diff_days = c.get("diff_days", 0)
        desc = (
            f"Potential Hy's Law: transaminase={trans_val} U/L (>3xULN), "
            f"bilirubin={bili_val} umol/L (>2xULN) within {diff_days} days."
        )
        if c.get("is_excluded"):
            desc += f" (Exclusion noted: {'; '.join(c.get('exclusion_reasons', []))})"

        ev_refs = c.get("evidence", [])
        finding = Finding(
            finding_id=f"FIND-HYS-{subj}-{cut}",
            finding_type="HYS_LAW_CANDIDATE",
            usubjid=subj,
            site_id=site,
            description=desc,
            severity="CRITICAL",
            evidence=list(ev_refs),
            data={
                "trans_test": c.get("trans_rec").parsed.get("lbtestcd") if c.get("trans_rec") else None,
                "trans_val": trans_val,
                "bili_val": bili_val,
                "diff_days": diff_days,
                "is_excluded": c.get("is_excluded", False),
                "exclusion_reasons": c.get("exclusion_reasons", []),
                "event_date": str(c.get("event_date")),
            },
            cut=cut,
            protocol_version=protocol_version,
        )
        findings.append(finding)
        all_evidence_refs.extend(ev_refs)

    # 2. Dosing Errors
    dosing_errors = find_dosing_errors(graph, cut=cut)
    for ex in dosing_errors:
        subj = ex.ref.usubjid
        site = graph._extract_site(subj)
        dose = ex.parsed.get("exdose")
        visit = ex.parsed.get("visit") or ex.data.get("VISIT") or "Unknown"
        desc = f"Dosing error: dose of {dose} mg administered at visit {visit}."
        finding = Finding(
            finding_id=f"FIND-DOSE-{subj}-{ex.ref.seq}",
            finding_type="DOSING_ERROR",
            usubjid=subj,
            site_id=site,
            description=desc,
            severity="HIGH",
            evidence=[ex.ref],
            data={
                "exdose": dose,
                "visit": visit,
                "exstdtc": str(ex.parsed.get("exstdtc")),
            },
            cut=cut,
            protocol_version=protocol_version,
        )
        findings.append(finding)
        all_evidence_refs.append(ex.ref)

    # 3. Prohibited Concomitant Medications
    prohibited_meds = find_prohibited_meds(graph, protocol_version=protocol_version, cut=cut)
    for cm in prohibited_meds:
        subj = cm.ref.usubjid
        site = graph._extract_site(subj)
        med_name = cm.parsed.get("cmtrt") or cm.data.get("CMTRT") or "Unknown"
        med_class = cm.parsed.get("cmclas") or cm.data.get("CMCLAS") or "Unknown"
        desc = f"Prohibited concomitant medication: {med_name} (Class: {med_class}) per Protocol v{protocol_version}."
        finding = Finding(
            finding_id=f"FIND-MED-{subj}-{cm.ref.seq}",
            finding_type="PROHIBITED_MED",
            usubjid=subj,
            site_id=site,
            description=desc,
            severity="HIGH",
            evidence=[cm.ref],
            data={
                "cmtrt": med_name,
                "cmclas": med_class,
                "cmstdtc": str(cm.parsed.get("cmstdtc")),
            },
            cut=cut,
            protocol_version=protocol_version,
        )
        findings.append(finding)
        all_evidence_refs.append(cm.ref)

    # 4. Visit Window Deviations
    visit_deviations = find_visit_window_deviations(
        graph, protocol_version=protocol_version, cut=cut
    )
    for dev in visit_deviations:
        subj = dev["usubjid"]
        site = graph._extract_site(subj)
        visit_name = dev["visit"]
        dev_days = dev["deviation_days"]
        win = dev["window_allowed"]
        act_date = dev["actual_date"].strftime("%Y-%m-%d") if hasattr(dev["actual_date"], "strftime") else str(dev["actual_date"])
        exp_date = dev["expected_date"].strftime("%Y-%m-%d") if hasattr(dev["expected_date"], "strftime") else str(dev["expected_date"])
        desc = (
            f"Visit window deviation: {visit_name} on {act_date}, expected {exp_date} "
            f"({dev_days} days off vs allowed +/-{win} days)."
        )
        ref = dev["ref"]
        finding = Finding(
            finding_id=f"FIND-VISIT-{subj}-{visit_name}-{ref.seq}",
            finding_type="VISIT_WINDOW_DEVIATION",
            usubjid=subj,
            site_id=site,
            description=desc,
            severity="MEDIUM",
            evidence=[ref],
            data={
                "visit": visit_name,
                "domain": dev.get("domain"),
                "deviation_days": dev_days,
                "window_allowed": win,
                "actual_date": act_date,
                "expected_date": exp_date,
            },
            cut=cut,
            protocol_version=protocol_version,
        )
        findings.append(finding)
        all_evidence_refs.append(ref)

    # 5. Serious Adverse Events
    saes = find_saes(graph, cut=cut)
    for ae in saes:
        subj = ae.ref.usubjid
        site = graph._extract_site(subj)
        term = ae.parsed.get("aeterm") or ae.data.get("AETERM") or "Adverse Event"
        ser = ae.parsed.get("aeser") or ae.data.get("AESER") or "N"
        hosp = ae.parsed.get("aeshosp") or ae.data.get("AESHOSP") or "N"
        desc = f"Serious Adverse Event: {term} (Serious={ser}, Hospitalized={hosp})."
        finding = Finding(
            finding_id=f"FIND-SAE-{subj}-{ae.ref.seq}",
            finding_type="SAE_UNESCALATED",
            usubjid=subj,
            site_id=site,
            description=desc,
            severity="HIGH",
            evidence=[ae.ref],
            data={
                "aeterm": term,
                "aeser": ser,
                "aeshosp": hosp,
                "aestdtc": str(ae.parsed.get("aestdtc")),
            },
            cut=cut,
            protocol_version=protocol_version,
        )
        findings.append(finding)
        all_evidence_refs.append(ae.ref)

    # Deduplicate cited evidence strings for trace
    evidence_str_list = sorted(list({str(r) for r in all_evidence_refs}))

    trace_entry = TraceEntry(
        node="detect",
        action="run_detection",
        status="SUCCESS",
        evidence=evidence_str_list,
        details={
            "cut": cut,
            "protocol_version": protocol_version,
            "total_findings": len(findings),
            "hys_law_candidates": len(hys_candidates),
            "dosing_errors": len(dosing_errors),
            "prohibited_meds": len(prohibited_meds),
            "visit_window_deviations": len(visit_deviations),
            "saes": len(saes),
        },
    )

    return findings, trace_entry
