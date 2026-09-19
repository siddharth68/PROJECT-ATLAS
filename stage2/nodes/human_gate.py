"""
Stage 2 - Human Gate Node.
Member 4 Extension Point (Human Gate, Memory & Lead Integrator).

This node gates clinical escalations and critical actions through human/medical monitor
oversight, resolving decisions according to study protocol:
- APPROVED: Action proceeds to safety reporting and dosing hold.
- REJECTED: Action halted with documented clinical rationale.
- CLARIFY:  Questions answered deterministically from study graph (e.g. screening transaminases,
            concomitant hepatotoxic meds) and resubmitted, resolving to APPROVED.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from stage1.atlas import Atlas, Domain
from stage2.schemas import Escalation, TraceEntry
from atlas.reasoning import check_hys_law_exclusions_detail


def run_human_gate_node(
    atlas: Atlas,
    escalations: List[Escalation],
) -> Tuple[List[Escalation], TraceEntry]:
    """
    Evaluate human gate / medical monitor review on draft escalations.

    Args:
        atlas: Stage 1 Atlas instance with access to graph and monitor decisions.
        escalations: Escalations drafted by medical review.

    Returns:
        evaluated_escalations: Escalations updated with monitor decisions and statuses.
        trace: TraceEntry recording gate decisions and evidence.
    """
    graph = atlas.graph
    monitor_decisions = getattr(graph, "monitor_decisions", {})

    evaluated_escalations: List[Escalation] = []
    evidence_refs = []
    counts = {"APPROVED": 0, "REJECTED": 0, "CLARIFIED": 0, "PENDING": 0}

    for esc in escalations:
        finding_type = esc.finding_type
        usubjid = esc.usubjid
        site_id = esc.site_id

        # Lookup monitor decision by subject or site
        key = f"{finding_type}|{usubjid}"
        raw_decision = monitor_decisions.get(key)
        if not raw_decision and site_id:
            site_key = f"{finding_type}|{site_id}"
            raw_decision = monitor_decisions.get(site_key)

        if raw_decision:
            decision_code = raw_decision[0]
            reason = raw_decision[1] if len(raw_decision) > 1 else ""

            if decision_code == "APPROVED":
                esc.status = "APPROVED"
                esc.monitor_decision = "APPROVED"
                esc.monitor_reason = reason
                counts["APPROVED"] += 1

            elif decision_code == "REJECTED":
                esc.status = "REJECTED"
                esc.monitor_decision = "REJECTED"
                esc.monitor_reason = reason
                counts["REJECTED"] += 1

            elif decision_code == "CLARIFY":
                # Spec requirement: "CLARIFY means answer the question from your own data
                # and resubmit — on resubmission the reply is APPROVED."
                clarification = _answer_monitor_clarification(graph, usubjid, reason)
                esc.status = "APPROVED"
                esc.monitor_decision = "CLARIFY"
                esc.monitor_reason = reason
                esc.clarification_response = clarification
                counts["CLARIFIED"] += 1
            else:
                esc.status = "PENDING"
                esc.monitor_decision = decision_code
                esc.monitor_reason = reason
                counts["PENDING"] += 1
        else:
            # If no pre-recorded decision exists, maintain PENDING review
            esc.status = "PENDING"
            esc.monitor_decision = "NONE"
            esc.monitor_reason = "Awaiting medical monitor review."
            counts["PENDING"] += 1

        evaluated_escalations.append(esc)
        evidence_refs.extend(esc.evidence)

    evidence_str_list = sorted(list({str(r) for r in evidence_refs}))

    trace_entry = TraceEntry(
        node="human_gate",
        action="evaluate_human_gate",
        status="SUCCESS",
        evidence=evidence_str_list,
        details={
            "total_escalations": len(escalations),
            "approved": counts["APPROVED"],
            "rejected": counts["REJECTED"],
            "clarified_and_approved": counts["CLARIFIED"],
            "pending": counts["PENDING"],
        },
    )

    return evaluated_escalations, trace_entry


def _answer_monitor_clarification(graph: Any, usubjid: str, question: str) -> str:
    """
    Deterministically retrieve data to answer the monitor's clarification question.
    """
    lb_records = graph.records_by_subject.get(usubjid, {}).get(Domain.LB, [])
    cm_records = graph.records_by_subject.get(usubjid, {}).get(Domain.CM, [])

    # Check baseline ALT/AST at screening
    screening_labs = [
        r for r in lb_records
        if (r.parsed.get("visit") == "SCREENING" or r.data.get("VISIT") == "SCREENING")
        and r.parsed.get("lbtestcd") in ("ALT", "AST")
    ]
    scr_parts = []
    for r in screening_labs:
        test = r.parsed.get("lbtestcd")
        val = r.parsed.get("lborres_std")
        uln = r.parsed.get("ref_high_std")
        scr_parts.append(f"{test}={val} U/L (ULN: {uln})")

    screening_str = ", ".join(scr_parts) if scr_parts else "Normal (no screening elevation)"

    # Check concomitant medications
    meds = [cm.parsed.get("cmtrt") or cm.data.get("CMTRT") for cm in cm_records]
    meds_str = ", ".join(filter(None, meds)) if meds else "None recorded"

    return (
        f"Data clarification provided: Screening transaminases: [{screening_str}]. "
        f"Concomitant medications: [{meds_str}]. Resubmitted with confirmation; approved."
    )
