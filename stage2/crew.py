"""
Stage 2 - ReviewCrew Orchestrator.
Member 1: Detection + Orchestration Engineer.

Implements the deterministic six-node execution cycle for Problem 2 - MONITOR:
    cut -> detect -> medical_review -> data_manager -> compliance -> human_gate -> execute -> ReviewReport
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from stage1.atlas import Atlas, StudyGraph
from stage2.schemas import (
    Finding,
    Escalation,
    Query,
    ComplianceDeviation,
    TraceEntry,
    ReviewReport,
)
from stage2.nodes.detect import run_detect_node
from stage2.nodes.medical_review import run_medical_review_node
from stage2.nodes.data_manager import run_data_manager_node
from stage2.nodes.compliance import run_compliance_node
from stage2.nodes.human_gate import run_human_gate_node
from stage2.nodes.execute import run_execute_node


class ReviewCrew:
    """
    Deterministic six-node orchestration engine for clinical trial monitoring cycles.
    """

    def __init__(
        self,
        hub_url: str,
        gateway_url: str,
        team_key: str,
        atlas: Atlas,
    ):
        """
        Initialize the ReviewCrew with gateway credentials and Stage 1 Atlas instance.

        Args:
            hub_url: URL for the study hub.
            gateway_url: URL for the communication gateway.
            team_key: Authentication key for the team.
            atlas: Stage 1 Atlas instance containing the knowledge graph.
        """
        self.hub_url: str = hub_url
        self.gateway_url: str = gateway_url
        self.team_key: str = team_key
        self.atlas: Atlas = atlas

        # Persistent memory across cycles to support idempotency and history tracking
        self.memory: Dict[str, Any] = {
            "seen_queries": set(),
            "seen_escalations": set(),
            "seen_cuts": set(),
            "history": [],
        }

    def reset_memory(self) -> None:
        """Clear cycle memory (useful between test runs)."""
        self.memory = {
            "seen_queries": set(),
            "seen_escalations": set(),
            "seen_cuts": set(),
            "history": [],
        }

    def run_cycle(self, cut: int, protocol_version: int) -> ReviewReport:
        """
        Execute a complete six-node monitoring cycle at the specified cut and protocol version.

        Execution Pipeline:
            1. detect         - Uses Stage 1 Atlas to detect clinical & protocol findings
            2. medical_review - Evaluates clinical severity and drafts escalations
            3. data_manager   - Identifies data discrepancies and generates site queries
            4. compliance     - Evaluates protocol deviations against protocol_version
            5. human_gate     - Evaluates monitor decisions (APPROVED, REJECTED, CLARIFY)
            6. execute        - Deduplicates via memory, commits actions, and produces ReviewReport

        Args:
            cut: Cut index (e.g., 5 or 12).
            protocol_version: Protocol amendment version in force (e.g., 1, 2, or 3).

        Returns:
            ReviewReport: The validated cycle review report.
        """
        trace: List[TraceEntry] = []

        # ------------------------------------------------------------------
        # Node 1: DETECT (Member 1)
        # ------------------------------------------------------------------
        findings, detect_trace = run_detect_node(
            atlas=self.atlas,
            cut=cut,
            protocol_version=protocol_version,
        )
        trace.append(detect_trace)

        # ------------------------------------------------------------------
        # Node 2: MEDICAL REVIEW (Member 2)
        # ------------------------------------------------------------------
        draft_escalations, med_trace = run_medical_review_node(
            findings=findings,
            protocol_version=protocol_version,
        )
        trace.append(med_trace)

        # ------------------------------------------------------------------
        # Node 3: DATA MANAGER (Member 3)
        # ------------------------------------------------------------------
        queries, dm_trace = run_data_manager_node(
            findings=findings,
            escalations=draft_escalations,
            graph=self.atlas.graph,
            cut=cut,
        )
        trace.append(dm_trace)

        # ------------------------------------------------------------------
        # Node 4: COMPLIANCE (Member 3)
        # ------------------------------------------------------------------
        deviations, comp_trace = run_compliance_node(
            findings=findings,
            protocol_version=protocol_version,
            graph=self.atlas.graph,
            cut=cut,
        )
        trace.append(comp_trace)

        # ------------------------------------------------------------------
        # Node 5: HUMAN GATE (Member 4)
        # ------------------------------------------------------------------
        evaluated_escalations, gate_trace = run_human_gate_node(
            atlas=self.atlas,
            escalations=draft_escalations,
        )
        trace.append(gate_trace)

        # ------------------------------------------------------------------
        # Node 6: EXECUTE (Member 1 / Lead Integrator)
        # ------------------------------------------------------------------
        report = run_execute_node(
            cut=cut,
            protocol_version=protocol_version,
            findings=findings,
            escalations=evaluated_escalations,
            queries=queries,
            compliance_deviations=deviations,
            cycle_trace=trace,
            memory=self.memory,
        )

        # Store report in crew history
        self.memory["history"].append(report)

        return report


def main() -> int:
    """CLI runner for Stage 2 ReviewCrew."""
    parser = argparse.ArgumentParser(description="Stage 2 ReviewCrew Runner")
    parser.add_argument("--data", default="hackathon-data", help="Path to study data directory")
    parser.add_argument("--cut", type=int, default=5, help="Data cut index (default: 5)")
    parser.add_argument("--protocol", type=int, default=2, help="Protocol version (default: 2)")
    parser.add_argument("--hub-url", default="http://localhost:8000", help="Hub URL")
    parser.add_argument("--gateway-url", default="http://localhost:8001", help="Gateway URL")
    parser.add_argument("--team-key", default="TEAM-ATLAS", help="Team Key")
    parser.add_argument("--export", default="stage2_public.json", help="Path to export report JSON")
    args = parser.parse_args()

    import os
    data_dir = args.data
    if os.path.exists(os.path.join(data_dir, "hackathon-data")):
        data_dir = os.path.join(data_dir, "hackathon-data")

    print(f"Building Stage 1 Knowledge Graph at cut {args.cut}...")
    graph = StudyGraph(data_dir)
    graph.build(cut=args.cut)
    atlas = Atlas(graph)

    crew = ReviewCrew(
        hub_url=args.hub_url,
        gateway_url=args.gateway_url,
        team_key=args.team_key,
        atlas=atlas,
    )

    print(f"Running Stage 2 monitoring cycle: Cut {args.cut}, Protocol v{args.protocol}...")
    report = crew.run_cycle(cut=args.cut, protocol_version=args.protocol)

    print("\n" + "=" * 60)
    print("           STAGE 2 MONITOR REVIEW REPORT SUMMARY")
    print("=" * 60)
    print(f"Cut:                   {report.cut}")
    print(f"Protocol Version:      v{report.protocol_version}")
    print(f"Total Findings:        {report.summary.get('total_findings', 0)}")
    print(f"New Escalations:       {report.summary.get('new_escalations', 0)}")
    print(f"New Queries:           {report.summary.get('new_queries', 0)}")
    print(f"Compliance Deviations: {report.summary.get('compliance_deviations', 0)}")
    print(f"Trace Sequence:        {' -> '.join(t.node for t in report.trace)}")
    print("=" * 60)

    if args.export:
        with open(args.export, "w", encoding="utf-8") as f:
            f.write(report.to_json(indent=2))
        print(f"Report exported to {args.export}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
