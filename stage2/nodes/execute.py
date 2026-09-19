"""
Stage 2 - Execute Node.
Member 1 / Lead Integrator.

This node dispatches approved actions, filters duplicate queries and escalations
against cycle memory (ensuring idempotency across duplicate cuts), compiles
the final execution trace, and produces the ReviewReport.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
from stage2.schemas import (
    Finding,
    Escalation,
    Query,
    ComplianceDeviation,
    TraceEntry,
    ReviewReport,
)


def run_execute_node(
    cut: int,
    protocol_version: int,
    findings: List[Finding],
    escalations: List[Escalation],
    queries: List[Query],
    compliance_deviations: List[ComplianceDeviation],
    cycle_trace: List[TraceEntry],
    memory: Dict[str, Any],
) -> ReviewReport:
    """
    Execute actions, apply memory deduplication, record audit trace, and compile ReviewReport.

    Args:
        cut: Cut number being reviewed.
        protocol_version: Protocol amendment version.
        findings: All findings detected in this cycle.
        escalations: Evaluated escalations from human gate.
        queries: Generated queries from data manager.
        compliance_deviations: Logged compliance deviations.
        cycle_trace: Prior trace entries from nodes 1 through 5.
        memory: Persistent memory dictionary across cycles.

    Returns:
        ReviewReport: The completed cycle review report.
    """
    seen_query_keys: Set[str] = memory.setdefault("seen_queries", set())
    seen_escalation_keys: Set[str] = memory.setdefault("seen_escalations", set())
    seen_cuts: Set[int] = memory.setdefault("seen_cuts", set())

    is_repeat_cut = cut in seen_cuts

    # 1. Deduplicate Queries against persistent memory
    # A query is identified by (usubjid, domain, evidence)
    active_queries: List[Query] = []
    for q in queries:
        ev_key = ",".join(sorted(str(r) for r in q.evidence))
        q_key = f"{q.usubjid}|{q.domain}|{ev_key}"
        if is_repeat_cut or q_key in seen_query_keys:
            continue
        seen_query_keys.add(q_key)
        active_queries.append(q)

    # 2. Deduplicate Escalations against persistent memory
    active_escalations: List[Escalation] = []
    for esc in escalations:
        ev_key = ",".join(sorted(str(r) for r in esc.evidence))
        esc_key = f"{esc.finding_type}|{esc.usubjid}|{ev_key}"
        if is_repeat_cut or esc_key in seen_escalation_keys:
            continue
        seen_escalation_keys.add(esc_key)
        active_escalations.append(esc)

    seen_cuts.add(cut)

    # 3. Emit execute trace entry
    evidence_str_list = sorted(list({
        str(r)
        for item in (active_escalations + active_queries)
        for r in item.evidence
    }))

    execute_trace = TraceEntry(
        node="execute",
        action="commit_cycle_actions",
        status="SUCCESS",
        evidence=evidence_str_list,
        details={
            "cut": cut,
            "protocol_version": protocol_version,
            "is_repeat_cut": is_repeat_cut,
            "new_queries_dispatched": len(active_queries),
            "new_escalations_executed": len(active_escalations),
            "compliance_deviations_logged": len(compliance_deviations),
        },
    )

    full_trace = list(cycle_trace) + [execute_trace]

    summary = {
        "cut": cut,
        "protocol_version": protocol_version,
        "is_repeat_cut": is_repeat_cut,
        "total_findings": len(findings),
        "new_escalations": len(active_escalations),
        "new_queries": len(active_queries),
        "compliance_deviations": len(compliance_deviations),
        "trace_steps": len(full_trace),
    }

    report = ReviewReport(
        cut=cut,
        protocol_version=protocol_version,
        findings=findings,
        escalations=active_escalations,
        queries=active_queries,
        compliance_deviations=compliance_deviations,
        trace=full_trace,
        summary=summary,
    )

    return report
