"""
Test Suite for Member 2 — Medical Review Engineer (Problem 2: MONITOR).

Covers all required areas:
1. Serious AE (AESER='Y')
2. Special Rule: AESHOSP='Y' + AESER='N' (hospitalization confirms seriousness)
3. Non-serious findings (routine AE, visit window deviations, asymptomatic dosing errors)
4. Liver signal / monitoring-only case (baseline transaminases already elevated)
5. True liver signal (Hy's law escalation)
6. Missing / corrupted data handling
7. Real record evidence preservation (no invented RecordRefs)
8. Mandatory decision attributes (finding_id, usubjid, site, severity, decision, rationale, evidence, alternatives_considered)
"""
from __future__ import annotations

import os
import sys
import unittest

# Ensure parent directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stage1.atlas import RecordRef, Domain, Atlas, StudyGraph
from stage2.schemas import Finding, Escalation, MedicalReviewDecision
from stage2.nodes.medical_review import MedicalReviewEngine, run_medical_review_node


def get_data_dir() -> str:
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


class TestMedicalReviewNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = get_data_dir()
        cls.graph = StudyGraph(cls.data_dir)
        cls.graph.build(cut=5)
        cls.atlas = Atlas(cls.graph)
        cls.engine = MedicalReviewEngine(protocol_version=2)

    def test_01_serious_ae(self):
        """Verify serious adverse event (AESER='Y') is escalated in the same cycle with evidence."""
        real_ref = RecordRef(domain=Domain.AE, usubjid="042-S01-001", seq=1)
        finding = Finding(
            finding_id="FIND-SAE-042-S01-001-1",
            finding_type="SAE_UNESCALATED",
            usubjid="042-S01-001",
            site_id="S01",
            description="Serious Adverse Event: Pyrexia requiring urgent intervention",
            severity="HIGH",
            evidence=[real_ref],
            data={
                "aeterm": "Pyrexia",
                "aeser": "Y",
                "aeshosp": "N",
                "aestdtc": "2026-02-10",
            },
        )

        dec = self.engine.evaluate_finding(finding)
        self.assertEqual(dec.decision, "ESCALATE")
        self.assertTrue(dec.is_serious)
        self.assertEqual(dec.severity, "HIGH")
        self.assertEqual(dec.evidence, [real_ref])
        self.assertIn("Serious Adverse Event", dec.rationale)
        self.assertGreater(len(dec.alternatives_considered), 0)

        # In batch execution, confirm an Escalation is drafted with the event record as evidence
        decisions, escalations, trace = self.engine.review_findings([finding], protocol_version=2)
        self.assertEqual(len(escalations), 1)
        esc = escalations[0]
        self.assertEqual(esc.finding_type, "SAE_UNESCALATED")
        self.assertEqual(esc.evidence, [real_ref])
        self.assertEqual(esc.urgency, "HIGH")

    def test_02_aeshosp_y_aeser_n_special_rule(self):
        """
        Special Protocol Rule:
        If AESHOSP = 'Y' and AESER = 'N', event must still be recognized as serious
        because hospitalization makes it serious according to protocol. Do NOT simply trust AESER.
        """
        real_ref = RecordRef(domain=Domain.AE, usubjid="042-S03-005", seq=2)
        finding = Finding(
            finding_id="FIND-SAE-042-S03-005-2",
            finding_type="SAE_UNESCALATED",
            usubjid="042-S03-005",
            site_id="S03",
            description="Adverse event with hospitalization recorded but AESER marked N",
            severity="HIGH",
            evidence=[real_ref],
            data={
                "aeterm": "Severe Dehydration",
                "aeser": "N",    # Site incorrectly marked non-serious
                "aeshosp": "Y",  # But patient was hospitalized
                "aestdtc": "2026-03-01",
            },
        )

        dec = self.engine.evaluate_finding(finding)
        self.assertEqual(dec.decision, "ESCALATE", "Must escalate when AESHOSP=Y even if AESER=N")
        self.assertTrue(dec.is_serious, "Must be classified as serious")
        self.assertEqual(dec.severity, "HIGH")
        self.assertIn("AESHOSP='Y'", dec.rationale)
        self.assertIn("AESER='N'", dec.rationale)
        self.assertTrue(any("AESER='N'" in alt for alt in dec.alternatives_considered))
        self.assertEqual(dec.evidence, [real_ref])

        # Confirm escalation drafted in the same cycle
        decisions, escalations, trace = self.engine.review_findings([finding])
        self.assertEqual(len(escalations), 1)
        self.assertEqual(escalations[0].evidence, [real_ref])

    def test_03_non_serious_finding(self):
        """Verify non-serious findings (routine AE or visit deviation) remain monitoring-only."""
        # 1. Non-serious AE
        ae_ref = RecordRef(domain=Domain.AE, usubjid="042-S01-001", seq=3)
        ns_ae = Finding(
            finding_id="FIND-AE-042-S01-001-3",
            finding_type="AE",
            usubjid="042-S01-001",
            site_id="S01",
            description="Mild Headache",
            severity="LOW",
            evidence=[ae_ref],
            data={
                "aeterm": "Headache",
                "aeser": "N",
                "aeshosp": "N",
            },
        )
        dec_ae = self.engine.evaluate_finding(ns_ae)
        self.assertEqual(dec_ae.decision, "MONITOR_ONLY")
        self.assertFalse(dec_ae.is_serious)
        self.assertIn("Non-serious", dec_ae.rationale)

        # 2. Visit window deviation
        vs_ref = RecordRef(domain=Domain.VS, usubjid="042-S02-004", seq=1)
        visit_dev = Finding(
            finding_id="FIND-VISIT-042-S02-004-WEEK4-1",
            finding_type="VISIT_WINDOW_DEVIATION",
            usubjid="042-S02-004",
            site_id="S02",
            description="Visit WEEK4 occurred 5 days late",
            severity="MEDIUM",
            evidence=[vs_ref],
            data={"visit": "WEEK4", "deviation_days": 5, "window_allowed": 3},
        )
        dec_dev = self.engine.evaluate_finding(visit_dev)
        self.assertEqual(dec_dev.decision, "MONITOR_ONLY")
        self.assertFalse(dec_dev.is_serious)

        # Confirm neither is escalated
        decisions, escalations, trace = self.engine.review_findings([ns_ae, visit_dev])
        self.assertEqual(len(escalations), 0, "Non-serious findings must NOT be escalated")

    def test_04_liver_signal_monitoring_only_baseline_elevated(self):
        """
        Precision Rule:
        A liver-signal candidate whose screening value was already elevated should remain
        monitoring-only when the required condition for escalation is not met, with reason recorded.
        """
        lb_ref1 = RecordRef(domain=Domain.LB, usubjid="042-S09-002", seq=10)
        lb_ref2 = RecordRef(domain=Domain.LB, usubjid="042-S09-002", seq=11)
        finding = Finding(
            finding_id="FIND-HYS-042-S09-002-5",
            finding_type="HYS_LAW_CANDIDATE",
            usubjid="042-S09-002",
            site_id="S09",
            description="Potential Hy's Law: ALT > 3xULN, BILI > 2xULN",
            severity="CRITICAL",
            evidence=[lb_ref1, lb_ref2],
            data={
                "trans_test": "ALT",
                "trans_val": 180.0,
                "bili_val": 35.0,
                "diff_days": 2,
                "is_excluded": True,  # Pre-existing hepatic elevation
                "exclusion_reasons": ["Baseline transaminases elevated at screening (ALT=125 > 2xULN 90)"],
                "baseline_elevated": True,
            },
        )

        dec = self.engine.evaluate_finding(finding)
        self.assertEqual(dec.decision, "MONITOR_ONLY", "Pre-existing baseline elevation must NOT escalate")
        self.assertFalse(dec.is_serious)
        self.assertIn("baseline transaminase elevation", dec.rationale.lower())
        self.assertIn("monitoring only", dec.rationale.lower())
        self.assertGreater(len(dec.alternatives_considered), 0)

        # Confirm it is NOT escalated in batch review
        decisions, escalations, trace = self.engine.review_findings([finding])
        self.assertEqual(len(escalations), 0, "Must remain monitoring-only and not produce an escalation")

    def test_05_liver_signal_escalation_true_candidate(self):
        """Verify true Hy's law case without baseline elevation is escalated as CRITICAL."""
        lb_ref1 = RecordRef(domain=Domain.LB, usubjid="042-S05-003", seq=20)
        lb_ref2 = RecordRef(domain=Domain.LB, usubjid="042-S05-003", seq=21)
        finding = Finding(
            finding_id="FIND-HYS-042-S05-003-5",
            finding_type="HYS_LAW_CANDIDATE",
            usubjid="042-S05-003",
            site_id="S05",
            description="Potential Hy's Law: ALT 238.9 U/L (>3xULN), BILI 4.66 umol/L (>2xULN) within 0 days",
            severity="CRITICAL",
            evidence=[lb_ref1, lb_ref2],
            data={
                "trans_test": "ALT",
                "trans_val": 238.9,
                "bili_val": 4.66,
                "diff_days": 0,
                "is_excluded": False,  # No exclusions!
                "exclusion_reasons": [],
            },
        )

        dec = self.engine.evaluate_finding(finding)
        self.assertEqual(dec.decision, "ESCALATE")
        self.assertTrue(dec.is_serious)
        self.assertEqual(dec.severity, "CRITICAL")
        self.assertIn("Potential Hy's Law criteria met", dec.rationale)

        decisions, escalations, trace = self.engine.review_findings([finding])
        self.assertEqual(len(escalations), 1)
        self.assertEqual(escalations[0].urgency, "IMMEDIATE")

    def test_06_missing_data_handling(self):
        """Verify missing data, None values, or empty evidence are handled gracefully without errors."""
        # 1. Finding with empty data dictionary
        f_empty_data = Finding(
            finding_id="FIND-EMPTY-1",
            finding_type="SAE_UNESCALATED",
            usubjid="042-S01-999",
            site_id="S01",
            description="Missing data payload",
            severity="HIGH",
            evidence=[],  # No evidence
            data={},
        )
        dec1 = self.engine.evaluate_finding(f_empty_data)
        self.assertEqual(dec1.decision, "MONITOR_ONLY")
        self.assertFalse(dec1.is_plausible)
        self.assertIn("missing", dec1.rationale.lower())

        # 2. Finding with None fields
        f_none_data = Finding(
            finding_id="FIND-NONE-2",
            finding_type="HYS_LAW_CANDIDATE",
            usubjid="042-S01-998",
            site_id="S01",
            description="None lab values",
            severity="CRITICAL",
            evidence=[RecordRef(domain=Domain.LB, usubjid="042-S01-998", seq=1)],
            data={"trans_val": None, "bili_val": None},
        )
        dec2 = self.engine.evaluate_finding(f_none_data)
        self.assertEqual(dec2.decision, "MONITOR_ONLY")
        self.assertFalse(dec2.is_plausible)

    def test_07_evidence_preservation(self):
        """Verify that real record references are strictly preserved and never fabricated."""
        real_ae_ref = RecordRef(domain=Domain.AE, usubjid="042-S02-001", seq=1)
        self.assertIn(str(real_ae_ref), self.graph._by_ref, "Evidence must be an actual record in graph")

        finding = Finding(
            finding_id="FIND-TEST-EV",
            finding_type="SAE_UNESCALATED",
            usubjid="042-S02-001",
            site_id="S02",
            description="Test real evidence preservation",
            severity="HIGH",
            evidence=[real_ae_ref],
            data={"aeterm": "Chest Pain", "aeser": "Y", "aeshosp": "N"},
        )

        decisions, escalations, trace = self.engine.review_findings([finding])
        self.assertEqual(len(escalations), 1)
        self.assertEqual(escalations[0].evidence, [real_ae_ref])
        self.assertEqual(decisions[0].evidence, [real_ae_ref])
        self.assertEqual(trace.evidence, [str(real_ae_ref)])

    def test_08_every_medical_decision_has_all_required_attributes(self):
        """
        Verify every medical decision contains all required fields:
        - finding identifier/code
        - subject
        - site if available
        - severity
        - decision
        - rationale
        - evidence
        - alternatives considered where appropriate
        """
        test_findings = [
            Finding(
                finding_id="F1",
                finding_type="SAE_UNESCALATED",
                usubjid="042-S01-001",
                site_id="S01",
                description="SAE test",
                severity="HIGH",
                evidence=[RecordRef(Domain.AE, "042-S01-001", 1)],
                data={"aeterm": "Asthma Attack", "aeser": "Y", "aeshosp": "Y"},
            ),
            Finding(
                finding_id="F2",
                finding_type="DOSING_ERROR",
                usubjid="042-S02-002",
                site_id="S02",
                description="Dose 20mg test",
                severity="MEDIUM",
                evidence=[RecordRef(Domain.EX, "042-S02-002", 1)],
                data={"exdose": 20, "visit": "WEEK2"},
            ),
            Finding(
                finding_id="F3",
                finding_type="HYS_LAW_CANDIDATE",
                usubjid="042-S03-003",
                site_id="S03",
                description="Liver signal baseline high",
                severity="CRITICAL",
                evidence=[RecordRef(Domain.LB, "042-S03-003", 1)],
                data={"trans_val": 200, "bili_val": 40, "is_excluded": True, "exclusion_reasons": ["Baseline elevated"]},
            ),
        ]

        decisions, escalations, trace = self.engine.review_findings(test_findings)
        self.assertEqual(len(decisions), 3)

        for d in decisions:
            self.assertTrue(d.finding_id)
            self.assertTrue(d.finding_type)
            self.assertTrue(d.usubjid)
            self.assertTrue(d.site_id)
            self.assertIn(d.severity, ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
            self.assertIn(d.decision, ["ESCALATE", "MONITOR_ONLY"])
            self.assertTrue(d.rationale)
            self.assertIsInstance(d.evidence, list)
            self.assertIsInstance(d.alternatives_considered, list)
            self.assertGreater(len(d.alternatives_considered), 0)


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMedicalReviewNode)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
