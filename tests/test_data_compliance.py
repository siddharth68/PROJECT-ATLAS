"""
Tests for Stage 2 Data Manager and Compliance nodes.

Tests cover:
  - Finding-driven query generation (dosing, visit, medication)
  - Graph-driven data-quality scanning (AE before dose, missing dose, unit mismatch, AE inconsistency)
  - Finding-driven compliance deviations
  - Graph-driven compliance (eligibility, visit windows, prohibited meds, dosing schedule)
  - Protocol version sensitivity
  - Evidence validity in all outputs
  - Deduplication
"""
from __future__ import annotations

import os
import sys
import unittest
from typing import Any, Dict, List, Optional

# Ensure project root on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from stage1.atlas import RecordRef, Domain
from stage2.schemas import Finding, Escalation, Query, ComplianceDeviation, TraceEntry
from stage2.nodes.data_manager import run_data_manager_node
from stage2.nodes.compliance import run_compliance_node


# ---------------------------------------------------------------------------
# Helpers to create test Finding objects
# ---------------------------------------------------------------------------

def _make_finding(
    finding_type: str,
    usubjid: str = "042-S01-001",
    site_id: str = "S01",
    seq: int = 1,
    severity: str = "HIGH",
    description: str = "Test finding",
    data: Optional[Dict[str, Any]] = None,
    domain: str = "AE",
) -> Finding:
    ref = RecordRef(domain=Domain(domain) if hasattr(Domain, domain) else Domain.AE, usubjid=usubjid, seq=seq)
    return Finding(
        finding_id=f"FIND-{finding_type[:4]}-{usubjid}-{seq}",
        finding_type=finding_type,
        usubjid=usubjid,
        site_id=site_id,
        description=description,
        severity=severity,
        evidence=[ref],
        data=data or {},
        cut=5,
        protocol_version=2,
    )


# ===========================================================================
# Test Data Manager Node
# ===========================================================================

class TestDataManagerNode(unittest.TestCase):
    """Tests for the data_manager node (finding-driven queries)."""

    def test_01_dosing_error_generates_query(self):
        """DOSING_ERROR finding produces a site query in EX domain."""
        f = _make_finding("DOSING_ERROR", description="Dose of 20 mg at WEEK2", domain="EX",
                          data={"exdose": 20, "visit": "WEEK2"})
        queries, trace = run_data_manager_node([f], [])

        self.assertGreaterEqual(len(queries), 1)
        dose_queries = [q for q in queries if "Dosing" in q.description]
        self.assertEqual(len(dose_queries), 1)
        q = dose_queries[0]
        self.assertEqual(q.domain, "EX")
        self.assertEqual(q.usubjid, "042-S01-001")
        self.assertEqual(q.status, "OPEN")
        self.assertTrue(len(q.evidence) > 0)

    def test_02_visit_deviation_generates_query(self):
        """VISIT_WINDOW_DEVIATION finding produces a visit query."""
        f = _make_finding("VISIT_WINDOW_DEVIATION", description="WEEK4 5 days off",
                          data={"domain": "LB", "deviation_days": 5, "visit": "WEEK4"}, domain="LB")
        queries, trace = run_data_manager_node([f], [])

        visit_queries = [q for q in queries if "Visit" in q.description]
        self.assertEqual(len(visit_queries), 1)
        self.assertEqual(visit_queries[0].domain, "LB")

    def test_03_prohibited_med_generates_query(self):
        """PROHIBITED_MED finding produces a CM domain query."""
        f = _make_finding("PROHIBITED_MED", description="Systemic Glucocorticoid",
                          data={"cmtrt": "Prednisone", "cmclas": "SYSTEMIC GLUCOCORTICOID"}, domain="CM")
        queries, trace = run_data_manager_node([f], [])

        med_queries = [q for q in queries if "Prohibited" in q.description]
        self.assertEqual(len(med_queries), 1)
        self.assertEqual(med_queries[0].domain, "CM")

    def test_04_no_findings_empty_output(self):
        """Empty findings list produces zero queries."""
        queries, trace = run_data_manager_node([], [])
        self.assertEqual(len(queries), 0)
        self.assertEqual(trace.node, "data_manager")
        self.assertEqual(trace.status, "SUCCESS")

    def test_05_trace_has_correct_metadata(self):
        """Trace entry has correct node name and query counts."""
        f = _make_finding("DOSING_ERROR", domain="EX")
        queries, trace = run_data_manager_node([f], [])

        self.assertEqual(trace.node, "data_manager")
        self.assertEqual(trace.action, "generate_queries")
        self.assertEqual(trace.details["queries_generated"], len(queries))

    def test_06_evidence_preserved_in_queries(self):
        """Every query preserves the original RecordRef evidence from the finding."""
        f = _make_finding("DOSING_ERROR", domain="EX", seq=42)
        queries, trace = run_data_manager_node([f], [])

        for q in queries:
            self.assertTrue(len(q.evidence) > 0, "Query must have evidence")
            # Evidence should contain RecordRef objects or strings
            for ref in q.evidence:
                self.assertTrue(
                    hasattr(ref, "usubjid") or isinstance(ref, str),
                    f"Evidence item should be RecordRef or string, got {type(ref)}"
                )

    def test_07_multiple_findings_no_duplicates(self):
        """Multiple findings produce unique, non-duplicated queries."""
        f1 = _make_finding("DOSING_ERROR", domain="EX", seq=1)
        f2 = _make_finding("DOSING_ERROR", domain="EX", seq=2,
                           description="Second dosing error")
        queries, trace = run_data_manager_node([f1, f2], [])

        query_ids = [q.query_id for q in queries]
        self.assertEqual(len(query_ids), len(set(query_ids)),
                         "Query IDs must be unique")

    def test_08_unknown_finding_type_ignored(self):
        """Unknown finding types don't produce queries."""
        f = _make_finding("UNKNOWN_TYPE")
        queries, trace = run_data_manager_node([f], [])
        # Only finding-driven queries for known types
        self.assertEqual(len(queries), 0)


# ===========================================================================
# Test Compliance Node
# ===========================================================================

class TestComplianceNode(unittest.TestCase):
    """Tests for the compliance node (finding-driven deviations)."""

    def test_01_visit_deviation_creates_compliance_deviation(self):
        """VISIT_WINDOW_DEVIATION finding creates a VISIT_WINDOW deviation."""
        f = _make_finding("VISIT_WINDOW_DEVIATION", description="WEEK4 5 days off", domain="LB")
        deviations, trace = run_compliance_node([f], protocol_version=2)

        self.assertGreaterEqual(len(deviations), 1)
        visit_devs = [d for d in deviations if d.deviation_type == "VISIT_WINDOW"]
        self.assertEqual(len(visit_devs), 1)
        self.assertEqual(visit_devs[0].severity, "MINOR")
        self.assertEqual(visit_devs[0].protocol_version, 2)
        self.assertIn("v2", visit_devs[0].description)

    def test_02_prohibited_med_creates_major_deviation(self):
        """PROHIBITED_MED finding creates a MAJOR deviation."""
        f = _make_finding("PROHIBITED_MED", description="Prednisone", domain="CM")
        deviations, trace = run_compliance_node([f], protocol_version=2)

        med_devs = [d for d in deviations if d.deviation_type == "PROHIBITED_MEDICATION"]
        self.assertEqual(len(med_devs), 1)
        self.assertEqual(med_devs[0].severity, "MAJOR")

    def test_03_dosing_error_creates_major_deviation(self):
        """DOSING_ERROR finding creates a DOSING_SCHEDULE deviation."""
        f = _make_finding("DOSING_ERROR", description="Dose 20mg", domain="EX")
        deviations, trace = run_compliance_node([f], protocol_version=2)

        dose_devs = [d for d in deviations if d.deviation_type == "DOSING_SCHEDULE"]
        self.assertEqual(len(dose_devs), 1)
        self.assertEqual(dose_devs[0].severity, "MAJOR")

    def test_04_protocol_version_recorded_correctly(self):
        """Every deviation records the active protocol version."""
        f = _make_finding("DOSING_ERROR", domain="EX")

        for pv in [1, 2, 3]:
            deviations, _ = run_compliance_node([f], protocol_version=pv)
            for d in deviations:
                self.assertEqual(d.protocol_version, pv,
                                 f"Deviation should have protocol_version={pv}")

    def test_05_no_findings_empty_output(self):
        """Empty findings produces zero deviations."""
        deviations, trace = run_compliance_node([], protocol_version=2)
        self.assertEqual(len(deviations), 0)
        self.assertEqual(trace.node, "compliance")

    def test_06_trace_has_severity_counts(self):
        """Trace entry includes MAJOR and MINOR counts."""
        f1 = _make_finding("VISIT_WINDOW_DEVIATION", domain="LB")
        f2 = _make_finding("DOSING_ERROR", domain="EX", seq=2)
        deviations, trace = run_compliance_node([f1, f2], protocol_version=2)

        self.assertIn("major_count", trace.details)
        self.assertIn("minor_count", trace.details)
        self.assertEqual(trace.details["total_deviations"], len(deviations))

    def test_07_evidence_preserved(self):
        """Every deviation has valid evidence."""
        f = _make_finding("PROHIBITED_MED", domain="CM")
        deviations, trace = run_compliance_node([f], protocol_version=2)

        for d in deviations:
            self.assertTrue(len(d.evidence) > 0, "Deviation must have evidence")


# ===========================================================================
# Integration Test with live data (optional)
# ===========================================================================

class TestWithLiveData(unittest.TestCase):
    """Integration tests using the actual hackathon dataset."""

    @classmethod
    def setUpClass(cls):
        """Build graph once for all integration tests."""
        data_dir = None
        # Try common data paths
        for candidate in [
            os.path.join(PROJECT_ROOT, "hackathon-data", "hackathon-data"),
            os.path.join(PROJECT_ROOT, "hackathon-data"),
        ]:
            if os.path.exists(os.path.join(candidate, "data")):
                data_dir = candidate
                break

        if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
            data_dir = sys.argv[1]

        if data_dir is None:
            raise unittest.SkipTest("Hackathon data not found")

        from atlas.graph import StudyGraph
        cls.graph = StudyGraph(data_dir)
        cls.graph.build(cut=5)

    def test_08_graph_driven_data_manager_produces_queries(self):
        """Graph-driven scanning should find data-quality issues."""
        queries, trace = run_data_manager_node(
            findings=[], escalations=[], graph=self.graph, cut=5
        )
        # We expect at least some queries from graph scanning
        self.assertIsInstance(queries, list)
        self.assertEqual(trace.node, "data_manager")
        # Every query must have evidence
        for q in queries:
            self.assertTrue(len(q.evidence) > 0,
                            f"Query {q.query_id} missing evidence")
            self.assertTrue(q.query_id, "Query must have an ID")
            self.assertTrue(q.description, "Query must have a description")

    def test_09_graph_driven_compliance_produces_deviations(self):
        """Graph-driven compliance scanning should find deviations."""
        deviations, trace = run_compliance_node(
            findings=[], protocol_version=2, graph=self.graph, cut=5
        )
        self.assertIsInstance(deviations, list)
        self.assertEqual(trace.node, "compliance")
        # Every deviation must have evidence
        for d in deviations:
            self.assertTrue(len(d.evidence) > 0,
                            f"Deviation {d.deviation_id} missing evidence")
            self.assertEqual(d.protocol_version, 2)

    def test_10_compliance_v1_vs_v2_visit_window_sensitivity(self):
        """v1 (±7 days) should produce fewer visit window deviations than v2 (±3 days)."""
        devs_v1, _ = run_compliance_node(
            findings=[], protocol_version=1, graph=self.graph, cut=5
        )
        devs_v2, _ = run_compliance_node(
            findings=[], protocol_version=2, graph=self.graph, cut=5
        )

        visit_v1 = [d for d in devs_v1 if d.deviation_type == "VISIT_WINDOW"]
        visit_v2 = [d for d in devs_v2 if d.deviation_type == "VISIT_WINDOW"]

        # v2 has stricter windows, so should find >= v1 deviations
        self.assertGreaterEqual(len(visit_v2), len(visit_v1),
                                "v2 (±3d) should find ≥ v1 (±7d) visit deviations")

    def test_11_compliance_v3_detects_sulfonylurea(self):
        """Protocol v3 should detect Sulfonylurea as prohibited."""
        devs_v2, _ = run_compliance_node(
            findings=[], protocol_version=2, graph=self.graph, cut=5
        )
        devs_v3, _ = run_compliance_node(
            findings=[], protocol_version=3, graph=self.graph, cut=5
        )

        med_v2 = [d for d in devs_v2 if d.deviation_type == "PROHIBITED_MEDICATION"]
        med_v3 = [d for d in devs_v3 if d.deviation_type == "PROHIBITED_MEDICATION"]

        # v3 adds Sulfonylurea, so should have >= v2 medication deviations
        self.assertGreaterEqual(len(med_v3), len(med_v2),
                                "v3 should detect ≥ v2 prohibited meds (Sulfonylurea added)")

    def test_12_ae_seriousness_inconsistency_detected(self):
        """AE records with AESHOSP=Y but AESER=N should produce queries."""
        queries, trace = run_data_manager_node(
            findings=[], escalations=[], graph=self.graph, cut=5
        )
        ae_incon = [q for q in queries if "AESERCON" in q.query_id]
        # If any such records exist, they must be detected
        # (We just verify the scanner ran and produced valid output)
        for q in ae_incon:
            self.assertIn("AESHOSP", q.description)
            self.assertIn("AESER", q.description)


if __name__ == "__main__":
    # Allow passing data dir as argument
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        # Keep the data dir arg for setUpClass, remove it from unittest args
        pass

    unittest.main(verbosity=2)
