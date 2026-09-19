"""
Test Suite for Problem 2 - MONITOR (ReviewCrew & Six-Node Pipeline).
Member 1: Detection + Orchestration Engineer.

Covers:
1. Empty cycle handling
2. Normal cycle execution (Cut 5 & Cut 12)
3. Strict six-node ordering in trace
4. Stage 1 detection integration and RecordRef verification
5. ReviewReport generation and JSON serialization
6. Idempotency across repeat cycles (zero new queries / escalations)
7. Handling of all three medical monitor replies (APPROVED, REJECTED, CLARIFY)
"""
from __future__ import annotations

import json
import os
import sys
import unittest

# Ensure parent directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stage1.atlas import Atlas, StudyGraph, RecordRef, Domain
from stage2.crew import ReviewCrew
from stage2.schemas import (
    Finding,
    Escalation,
    Query,
    ComplianceDeviation,
    TraceEntry,
    ReviewReport,
)
from stage2.nodes.detect import run_detect_node
from stage2.nodes.human_gate import run_human_gate_node


def get_data_dir() -> str:
    """Find the study data directory."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "hackathon-data", "hackathon-data"),
        os.path.join(os.path.dirname(__file__), "..", "hackathon-data"),
        "hackathon-data/hackathon-data",
        "hackathon-data",
    ]
    for c in candidates:
        if os.path.exists(os.path.join(c, "data")):
            return os.path.abspath(c)
    return "hackathon-data"


class TestStage2Monitor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = get_data_dir()
        cls.graph = StudyGraph(cls.data_dir)
        cls.graph.build(cut=5)
        cls.atlas = Atlas(cls.graph)

    def test_01_six_node_ordering(self):
        """Verify report trace strictly follows the required 6-node execution order."""
        crew = ReviewCrew(
            hub_url="http://localhost:8000",
            gateway_url="http://localhost:8001",
            team_key="TEAM-TEST",
            atlas=self.atlas,
        )
        report = crew.run_cycle(cut=5, protocol_version=2)

        expected_order = [
            "detect",
            "medical_review",
            "data_manager",
            "compliance",
            "human_gate",
            "execute",
        ]
        trace_nodes = [t.node for t in report.trace]
        self.assertEqual(
            trace_nodes,
            expected_order,
            f"Trace nodes must strictly follow {expected_order}, got {trace_nodes}",
        )

        # Confirm every node wrote a trace entry naming itself and its evidence
        for t in report.trace:
            self.assertIn(t.node, expected_order)
            self.assertIsInstance(t.evidence, list)
            self.assertEqual(t.status, "SUCCESS")

    def test_02_empty_cycle(self):
        """Verify an empty cycle executes cleanly across all 6 nodes with valid trace."""
        class MockEmptyAtlas:
            def __init__(self, real_atlas):
                self.graph = MockEmptyGraph(real_atlas.graph)

        class MockEmptyGraph:
            def __init__(self, real_graph):
                self._real = real_graph
                self.records_by_subject = {}
                self.records_by_domain = {}
                self.monitor_decisions = {}
                self._build_cut = 999
            def build(self, cut=None):
                pass
            def get_subjects(self):
                return []
            def get_protocol_version(self, cut=None):
                return 1
            def _extract_site(self, subj):
                return "S00"

        mock_atlas = MockEmptyAtlas(self.atlas)
        crew = ReviewCrew("", "", "", mock_atlas)
        report = crew.run_cycle(cut=999, protocol_version=1)

        self.assertEqual(len(report.findings), 0)
        self.assertEqual(len(report.escalations), 0)
        self.assertEqual(len(report.queries), 0)
        self.assertEqual(len(report.compliance_deviations), 0)
        self.assertEqual(len(report.trace), 6)
        expected_order = [
            "detect",
            "medical_review",
            "data_manager",
            "compliance",
            "human_gate",
            "execute",
        ]
        self.assertEqual([t.node for t in report.trace], expected_order)
        self.assertEqual(report.summary["total_findings"], 0)
        self.assertEqual(report.summary["new_escalations"], 0)
        self.assertEqual(report.summary["new_queries"], 0)

    def test_03_stage1_detection_integration(self):
        """Verify detect node safely uses Stage 1 Atlas without breaking its interface."""
        findings, trace_entry = run_detect_node(
            atlas=self.atlas,
            cut=5,
            protocol_version=2,
        )

        self.assertGreater(len(findings), 0, "Detect node should identify findings at Cut 5")
        self.assertEqual(trace_entry.node, "detect")
        self.assertEqual(trace_entry.status, "SUCCESS")
        self.assertGreater(len(trace_entry.evidence), 0)

        # Check evidence citation validity
        for f in findings:
            self.assertIsInstance(f, Finding)
            self.assertGreater(len(f.evidence), 0, f"Finding {f.finding_id} missing evidence")
            for ref in f.evidence:
                self.assertIsInstance(ref, RecordRef)
                # Confirm record exists in the underlying knowledge graph
                rec_str = str(ref)
                self.assertIn(
                    rec_str,
                    self.atlas.graph._by_ref,
                    f"Cited evidence {rec_str} must exist in Stage 1 graph",
                )

    def test_04_normal_cycle_cut5(self):
        """Verify a standard monitoring cycle at Cut 5 produces structured findings & report."""
        crew = ReviewCrew("http://hub", "http://gateway", "TEAM-ATLAS", self.atlas)
        report = crew.run_cycle(cut=5, protocol_version=2)

        self.assertIsInstance(report, ReviewReport)
        self.assertEqual(report.cut, 5)
        self.assertEqual(report.protocol_version, 2)
        self.assertGreater(len(report.findings), 0)
        self.assertGreater(len(report.queries), 0)
        self.assertGreater(len(report.compliance_deviations), 0)

        # Confirm Hy's Law candidate 042-S07-001 is found
        hys_findings = [f for f in report.findings if f.finding_type == "HYS_LAW_CANDIDATE"]
        self.assertGreaterEqual(len(hys_findings), 1)
        s07_findings = [f for f in hys_findings if f.usubjid == "042-S07-001"]
        self.assertEqual(len(s07_findings), 1, "042-S07-001 must be detected at Cut 5")

    def test_05_idempotence_running_same_cut_twice(self):
        """Verify running the same cut twice raises ZERO new queries and ZERO new escalations."""
        crew = ReviewCrew("http://hub", "http://gateway", "TEAM-ATLAS", self.atlas)
        crew.reset_memory()

        report1 = crew.run_cycle(cut=5, protocol_version=2)
        self.assertGreater(report1.summary["new_escalations"], 0)
        self.assertGreater(report1.summary["new_queries"], 0)

        # Second run on the same cut
        report2 = crew.run_cycle(cut=5, protocol_version=2)
        self.assertEqual(
            report2.summary["new_escalations"],
            0,
            "Repeat cycle must produce 0 new escalations",
        )
        self.assertEqual(
            report2.summary["new_queries"],
            0,
            "Repeat cycle must produce 0 new queries",
        )
        self.assertTrue(report2.summary["is_repeat_cut"])

    def test_06_three_monitor_replies(self):
        """Verify APPROVED, REJECTED, and CLARIFY are all handled per challenge requirements."""
        # Create test escalations covering each reply
        esc_approved = Escalation(
            escalation_id="TEST-ESC-1",
            finding_id="FIND-1",
            usubjid="042-S02-001",  # APPROVED in monitor_decisions.json
            site_id="S02",
            finding_type="HYS_LAW_CANDIDATE",
            urgency="IMMEDIATE",
            reason="Test Hy's law approved",
            evidence=[],
        )
        esc_rejected = Escalation(
            escalation_id="TEST-ESC-2",
            finding_id="FIND-2",
            usubjid="042-S07-001",  # REJECTED in monitor_decisions.json
            site_id="S07",
            finding_type="HYS_LAW_CANDIDATE",
            urgency="IMMEDIATE",
            reason="Test Hy's law rejected",
            evidence=[],
        )
        esc_clarify = Escalation(
            escalation_id="TEST-ESC-3",
            finding_id="FIND-3",
            usubjid="042-S01-001",  # CLARIFY in monitor_decisions.json
            site_id="S01",
            finding_type="HYS_LAW_CANDIDATE",
            urgency="IMMEDIATE",
            reason="Test Hy's law clarify",
            evidence=[],
        )

        test_escalations = [esc_approved, esc_rejected, esc_clarify]
        evaluated, trace = run_human_gate_node(self.atlas, test_escalations)

        # 1. APPROVED handling
        res_approved = next(e for e in evaluated if e.usubjid == "042-S02-001")
        self.assertEqual(res_approved.status, "APPROVED")
        self.assertEqual(res_approved.monitor_decision, "APPROVED")
        self.assertIn("Consistent with Hy's law", res_approved.monitor_reason)

        # 2. REJECTED handling
        res_rejected = next(e for e in evaluated if e.usubjid == "042-S07-001")
        self.assertEqual(res_rejected.status, "REJECTED")
        self.assertEqual(res_rejected.monitor_decision, "REJECTED")
        self.assertIn("Baseline transaminases", res_rejected.monitor_reason)

        # 3. CLARIFY handling (clarified from study data and resubmitted as APPROVED)
        res_clarify = next(e for e in evaluated if e.usubjid == "042-S01-001")
        self.assertEqual(res_clarify.status, "APPROVED")
        self.assertEqual(res_clarify.monitor_decision, "CLARIFY")
        self.assertIsNotNone(res_clarify.clarification_response)
        self.assertIn("Data clarification provided", res_clarify.clarification_response)

        # Verify gate trace details
        self.assertEqual(trace.details["approved"], 1)
        self.assertEqual(trace.details["rejected"], 1)
        self.assertEqual(trace.details["clarified_and_approved"], 1)

    def test_07_review_report_serialization(self):
        """Verify ReviewReport produces valid, parseable JSON."""
        crew = ReviewCrew("", "", "", self.atlas)
        report = crew.run_cycle(cut=5, protocol_version=2)

        report_dict = report.to_dict()
        self.assertIsInstance(report_dict, dict)
        self.assertIn("cut", report_dict)
        self.assertIn("protocol_version", report_dict)
        self.assertIn("trace", report_dict)
        self.assertIn("findings", report_dict)
        self.assertIn("escalations", report_dict)
        self.assertIn("queries", report_dict)
        self.assertIn("compliance_deviations", report_dict)

        # Test JSON serialization
        report_json = report.to_json(indent=2)
        parsed = json.loads(report_json)
        self.assertEqual(parsed["cut"], 5)
        self.assertEqual(parsed["protocol_version"], 2)
        self.assertEqual(len(parsed["trace"]), 6)


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestStage2Monitor)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        # Allow passing custom data dir: python test_stage2.py path/to/data
        custom_data = sys.argv[1]
        TestStage2Monitor.data_dir = custom_data
        TestStage2Monitor.graph = StudyGraph(custom_data)
        TestStage2Monitor.graph.build(cut=5)
        TestStage2Monitor.atlas = Atlas(TestStage2Monitor.graph)
    sys.exit(run_tests())
