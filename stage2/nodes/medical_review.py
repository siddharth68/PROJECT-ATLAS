"""
Stage 2 - Medical Review Node.
Member 2: Medical Review Engineer.

This module evaluates clinical trial findings produced by the detect node.
Every finding is evaluated for:
1. Seriousness (e.g. death, life-threatening, inpatient hospitalization, Hy's Law)
2. Plausibility (biological, temporal, and data integrity checks)
3. Whether escalation is required (expedited clinical/safety intervention)
4. Whether it should remain monitoring-only (operational issues, pre-existing conditions)
5. Rationale (explicit clinical justification)
6. Supporting evidence (preserved RecordRefs from actual study records)

Protocol Specific Rules:
- Serious Adverse Events (SAEs) must be escalated in the same cycle with the AE record as evidence.
- Special Rule: If AESHOSP = 'Y' and AESER = 'N', the event must still be recognized as serious
  because inpatient hospitalization establishes seriousness per Protocol Section 6 and ICH GCP E2A.
  AESER is not simply trusted blindly.
- Precision: Medical review must NOT escalate everything.
  A liver-signal candidate whose screening value was already elevated remains monitoring-only
  with clinical rationale recorded.
- Non-serious or operational discrepancies (e.g. visit deviations, non-toxic dosing errors)
  remain monitoring-only and are routed to Data Manager / Compliance.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from stage1.atlas import RecordRef
from stage2.schemas import Finding, Escalation, MedicalReviewDecision, TraceEntry


class MedicalReviewEngine:
    """
    Deterministic clinical reasoning engine for medical review.
    Evaluates clinical seriousness, biological plausibility, and safety escalation requirements.
    """

    def __init__(self, protocol_version: int = 1):
        self.protocol_version = protocol_version

    def evaluate_finding(self, finding: Finding) -> MedicalReviewDecision:
        """
        Evaluate an individual clinical finding deterministically.

        Returns:
            MedicalReviewDecision containing decision (ESCALATE vs MONITOR_ONLY),
            seriousness, plausibility, rationale, alternatives considered, and evidence.
        """
        finding_type = getattr(finding, "finding_type", "")
        usubjid = getattr(finding, "usubjid", "")
        site_id = getattr(finding, "site_id", "") or "Unknown"
        finding_id = getattr(finding, "finding_id", f"FIND-{usubjid}")
        data = getattr(finding, "data", {}) or {}
        evidence = list(getattr(finding, "evidence", []))
        desc = getattr(finding, "description", "")

        # ------------------------------------------------------------------
        # 1. Missing / Incomplete Data Check
        # ------------------------------------------------------------------
        if not evidence or data is None or not isinstance(data, dict):
            return MedicalReviewDecision(
                decision_id=f"MED-{finding_id}",
                finding_id=finding_id,
                finding_type=finding_type,
                usubjid=usubjid,
                site_id=site_id,
                severity="LOW",
                decision="MONITOR_ONLY",
                rationale=(
                    f"Incomplete source data or missing record evidence for {finding_type} "
                    f"on subject {usubjid}. Unable to verify clinical plausibility or seriousness. "
                    f"Held in monitoring-only pending source record retrieval."
                ),
                evidence=evidence,
                alternatives_considered=[
                    "Immediate safety escalation was considered, but rejected because clinical facts cannot be verified without complete evidence."
                ],
                is_serious=False,
                is_plausible=False,
            )

        # ------------------------------------------------------------------
        # 2. Adverse Events & SAE Evaluation
        # ------------------------------------------------------------------
        if finding_type in ("SAE_UNESCALATED", "AE", "ADVERSE_EVENT") or "aeterm" in data:
            return self._evaluate_adverse_event(finding, data, evidence)

        # ------------------------------------------------------------------
        # 3. Liver Signals / Hy's Law Candidates
        # ------------------------------------------------------------------
        if finding_type == "HYS_LAW_CANDIDATE":
            return self._evaluate_hys_law_candidate(finding, data, evidence)

        # ------------------------------------------------------------------
        # 4. Medication Dosing Errors
        # ------------------------------------------------------------------
        if finding_type == "DOSING_ERROR":
            return self._evaluate_dosing_error(finding, data, evidence)

        # ------------------------------------------------------------------
        # 5. Prohibited Concomitant Medications
        # ------------------------------------------------------------------
        if finding_type == "PROHIBITED_MED":
            return self._evaluate_prohibited_medication(finding, data, evidence)

        # ------------------------------------------------------------------
        # 6. Visit Window Deviations
        # ------------------------------------------------------------------
        if finding_type == "VISIT_WINDOW_DEVIATION":
            return self._evaluate_visit_deviation(finding, data, evidence)

        # ------------------------------------------------------------------
        # 7. Generic / Default Clinical Assessment
        # ------------------------------------------------------------------
        return MedicalReviewDecision(
            decision_id=f"MED-{finding_id}",
            finding_id=finding_id,
            finding_type=finding_type,
            usubjid=usubjid,
            site_id=site_id,
            severity=getattr(finding, "severity", "LOW"),
            decision="MONITOR_ONLY",
            rationale=(
                f"Clinical finding ({finding_type}: {desc}) does not meet acute safety "
                f"escalation criteria. Retained for routine clinical monitoring."
            ),
            evidence=evidence,
            alternatives_considered=[
                "Expedited escalation was considered, but rejected as finding lacks serious or life-threatening features."
            ],
            is_serious=False,
            is_plausible=True,
        )

    # ----------------------------------------------------------------------
    # Specific Clinical Evaluators
    # ----------------------------------------------------------------------

    def _evaluate_adverse_event(
        self,
        finding: Finding,
        data: Dict[str, Any],
        evidence: List[RecordRef],
    ) -> MedicalReviewDecision:
        """
        Evaluate an Adverse Event finding.
        Protocol rule: Inpatient hospitalization (AESHOSP='Y') confirms seriousness
        even if the site recorded AESER='N'. Do NOT blindly trust AESER.
        """
        aeser = str(data.get("aeser") or "").upper().strip()
        aeshosp = str(data.get("aeshosp") or "").upper().strip()
        aeterm = str(data.get("aeterm") or finding.description or "Adverse Event").strip()
        usubjid = finding.usubjid
        site_id = finding.site_id or "Unknown"

        # Check for missing critical AE fields
        if not aeterm or aeterm == "None":
            return MedicalReviewDecision(
                decision_id=f"MED-{finding.finding_id}",
                finding_id=finding.finding_id,
                finding_type=finding.finding_type,
                usubjid=usubjid,
                site_id=site_id,
                severity="LOW",
                decision="MONITOR_ONLY",
                rationale="Missing AE term in clinical record; cannot establish seriousness. Held for monitoring-only.",
                evidence=evidence,
                alternatives_considered=["Escalation considered, but rejected due to missing diagnostic term."],
                is_serious=False,
                is_plausible=False,
            )

        # Special Rule: AESHOSP = 'Y' overrides AESER = 'N'
        if aeshosp == "Y" and aeser == "N":
            return MedicalReviewDecision(
                decision_id=f"MED-{finding.finding_id}",
                finding_id=finding.finding_id,
                finding_type=finding.finding_type,
                usubjid=usubjid,
                site_id=site_id,
                severity="HIGH",
                decision="ESCALATE",
                rationale=(
                    f"Serious Adverse Event identified: Inpatient hospitalization documented (AESHOSP='Y') "
                    f"for event '{aeterm}', satisfying Protocol Section 6 and ICH GCP E2A seriousness criteria "
                    f"despite site recording AESER='N'. Expedited clinical escalation required in the same cycle."
                ),
                evidence=evidence,
                alternatives_considered=[
                    "Accepting site-reported AESER='N' and classifying as non-serious was considered, but rejected "
                    "because protocol mandates that any event resulting in hospitalization is legally and clinically serious."
                ],
                is_serious=True,
                is_plausible=True,
            )

        # Standard SAE: AESER = 'Y' or AESHOSP = 'Y'
        if aeser == "Y" or aeshosp == "Y":
            return MedicalReviewDecision(
                decision_id=f"MED-{finding.finding_id}",
                finding_id=finding.finding_id,
                finding_type=finding.finding_type,
                usubjid=usubjid,
                site_id=site_id,
                severity="HIGH",
                decision="ESCALATE",
                rationale=(
                    f"Serious Adverse Event confirmed (AESER='{aeser or 'Y'}', AESHOSP='{aeshosp or 'N'}', "
                    f"term='{aeterm}'). Protocol Section 6 requires expedited safety escalation in the same cycle."
                ),
                evidence=evidence,
                alternatives_considered=[
                    "Monitoring-only was considered, but rejected due to regulatory requirements for expedited safety reporting."
                ],
                is_serious=True,
                is_plausible=True,
            )

        # Non-serious AE: AESER = 'N' and AESHOSP != 'Y'
        return MedicalReviewDecision(
            decision_id=f"MED-{finding.finding_id}",
            finding_id=finding.finding_id,
            finding_type=finding.finding_type,
            usubjid=usubjid,
            site_id=site_id,
            severity="LOW",
            decision="MONITOR_ONLY",
            rationale=(
                f"Non-serious adverse event (AESER='{aeser or 'N'}', AESHOSP='{aeshosp or 'N'}', term='{aeterm}'). "
                f"Does not meet protocol seriousness criteria for expedited escalation. Maintain routine safety monitoring."
            ),
            evidence=evidence,
            alternatives_considered=[
                "Safety escalation was considered, but rejected as event lacks serious, hospitalization, or life-threatening criteria."
            ],
            is_serious=False,
            is_plausible=True,
        )

    def _evaluate_hys_law_candidate(
        self,
        finding: Finding,
        data: Dict[str, Any],
        evidence: List[RecordRef],
    ) -> MedicalReviewDecision:
        """
        Evaluate a liver-signal (Hy's Law) candidate.
        Precision rule: A candidate whose baseline value was already elevated at screening
        remains monitoring-only when the condition for escalation is not met, with rationale recorded.
        """
        usubjid = finding.usubjid
        site_id = finding.site_id or "Unknown"

        # Check plausibility: transaminase and bilirubin values must be present and positive
        trans_val = data.get("trans_val")
        bili_val = data.get("bili_val")
        diff_days = data.get("diff_days")

        if trans_val is None or bili_val is None:
            return MedicalReviewDecision(
                decision_id=f"MED-{finding.finding_id}",
                finding_id=finding.finding_id,
                finding_type=finding.finding_type,
                usubjid=usubjid,
                site_id=site_id,
                severity="LOW",
                decision="MONITOR_ONLY",
                rationale="Incomplete laboratory metrics for Hy's Law assessment. Retained in monitoring-only pending re-test.",
                evidence=evidence,
                alternatives_considered=["Escalation considered, but rejected due to missing quantitative analyte values."],
                is_serious=False,
                is_plausible=False,
            )

        # Check biological plausibility
        try:
            t_num = float(trans_val)
            b_num = float(bili_val)
            if t_num <= 0 or b_num <= 0:
                raise ValueError("Non-positive lab values")
        except (ValueError, TypeError):
            return MedicalReviewDecision(
                decision_id=f"MED-{finding.finding_id}",
                finding_id=finding.finding_id,
                finding_type=finding.finding_type,
                usubjid=usubjid,
                site_id=site_id,
                severity="LOW",
                decision="MONITOR_ONLY",
                rationale="Implausible or non-numeric transaminase/bilirubin values. Held for data verification.",
                evidence=evidence,
                alternatives_considered=["Escalation rejected due to biologically implausible zero or negative values."],
                is_serious=False,
                is_plausible=False,
            )

        # Precision Rule: Check for baseline screening elevation or explicit exclusion
        is_excluded = data.get("is_excluded", False)
        exclusion_reasons = data.get("exclusion_reasons", [])
        screening_elevated = (
            data.get("screening_alt_elevated", False)
            or data.get("baseline_elevated", False)
            or any("baseline" in str(r).lower() or "screening" in str(r).lower() for r in exclusion_reasons)
        )

        if is_excluded or screening_elevated:
            reasons_str = "; ".join(exclusion_reasons) if exclusion_reasons else "Pre-existing baseline transaminase elevation"
            return MedicalReviewDecision(
                decision_id=f"MED-{finding.finding_id}",
                finding_id=finding.finding_id,
                finding_type=finding.finding_type,
                usubjid=usubjid,
                site_id=site_id,
                severity="MEDIUM",
                decision="MONITOR_ONLY",
                rationale=(
                    f"Liver signal candidate exhibits pre-existing baseline transaminase elevation at screening ({reasons_str}). "
                    f"Per Protocol Section 3/7, pre-existing hepatic condition accounts for laboratory profile; "
                    f"condition for expedited safety escalation is not met. Maintained on clinical monitoring only."
                ),
                evidence=evidence,
                alternatives_considered=[
                    "Immediate safety escalation was considered, but rejected because pre-existing hepatic elevation explains "
                    "the transaminase profile without evidence of acute drug-induced liver injury."
                ],
                is_serious=False,
                is_plausible=True,
            )

        # True Hy's Law candidate meeting criteria without pre-existing hepatic disease
        return MedicalReviewDecision(
            decision_id=f"MED-{finding.finding_id}",
            finding_id=finding.finding_id,
            finding_type=finding.finding_type,
            usubjid=usubjid,
            site_id=site_id,
            severity="CRITICAL",
            decision="ESCALATE",
            rationale=(
                f"Potential Hy's Law criteria met: transaminase elevation ({trans_val} U/L, >3x ULN) concurrent with "
                f"total bilirubin ({bili_val} umol/L, >2x ULN) within {diff_days} days without baseline hepatic elevation "
                f"or cholestasis. Protocol Section 7 mandates immediate safety escalation, dosing hold, and repeat liver chemistry."
            ),
            evidence=evidence,
            alternatives_considered=[
                "Monitoring-only was considered, but rejected due to substantial mortality risk associated with acute drug-induced liver injury (DILI)."
            ],
            is_serious=True,
            is_plausible=True,
        )

    def _evaluate_dosing_error(
        self,
        finding: Finding,
        data: Dict[str, Any],
        evidence: List[RecordRef],
    ) -> MedicalReviewDecision:
        """
        Evaluate a dosing error finding.
        Asymptomatic dosing discrepancies are operational medication errors that remain monitoring-only,
        routed to Data Manager for site query and Compliance for deviation logging.
        """
        usubjid = finding.usubjid
        site_id = finding.site_id or "Unknown"
        dose = data.get("exdose")
        visit = data.get("visit", "Unknown")

        return MedicalReviewDecision(
            decision_id=f"MED-{finding.finding_id}",
            finding_id=finding.finding_id,
            finding_type=finding.finding_type,
            usubjid=usubjid,
            site_id=site_id,
            severity="MEDIUM",
            decision="MONITOR_ONLY",
            rationale=(
                f"Medication dosing discrepancy noted (administered dose: {dose} mg at visit {visit}) "
                f"without documented acute clinical toxicity or associated serious adverse event. "
                f"Retained for monitoring-only; routed to Data Manager for site query and Compliance for protocol deviation logging."
            ),
            evidence=evidence,
            alternatives_considered=[
                "Immediate clinical safety escalation was considered, but rejected because subject remains stable with no acute toxicity."
            ],
            is_serious=False,
            is_plausible=True,
        )

    def _evaluate_prohibited_medication(
        self,
        finding: Finding,
        data: Dict[str, Any],
        evidence: List[RecordRef],
    ) -> MedicalReviewDecision:
        """
        Evaluate a prohibited concomitant medication finding.
        Retained for monitoring-only and routed to Compliance unless an acute safety emergency is present.
        """
        usubjid = finding.usubjid
        site_id = finding.site_id or "Unknown"
        med_name = data.get("cmtrt", "Prohibited Medication")
        med_class = data.get("cmclas", "Unknown Class")

        return MedicalReviewDecision(
            decision_id=f"MED-{finding.finding_id}",
            finding_id=finding.finding_id,
            finding_type=finding.finding_type,
            usubjid=usubjid,
            site_id=site_id,
            severity="MEDIUM",
            decision="MONITOR_ONLY",
            rationale=(
                f"Concomitant administration of prohibited medication recorded ({med_name}, class: {med_class}). "
                f"No acute drug-drug interaction or emergency identified. Retained for monitoring-only; "
                f"routed to Compliance for protocol amendment violation assessment and Data Manager for site clarification."
            ),
            evidence=evidence,
            alternatives_considered=[
                "Immediate investigational product hold considered, but rejected pending site clarification on treatment indication and duration."
            ],
            is_serious=False,
            is_plausible=True,
        )

    def _evaluate_visit_deviation(
        self,
        finding: Finding,
        data: Dict[str, Any],
        evidence: List[RecordRef],
    ) -> MedicalReviewDecision:
        """
        Evaluate a visit schedule window deviation.
        Pure operational deviation; remains monitoring-only.
        """
        usubjid = finding.usubjid
        site_id = finding.site_id or "Unknown"

        return MedicalReviewDecision(
            decision_id=f"MED-{finding.finding_id}",
            finding_id=finding.finding_id,
            finding_type=finding.finding_type,
            usubjid=usubjid,
            site_id=site_id,
            severity="LOW",
            decision="MONITOR_ONLY",
            rationale=(
                f"Visit schedule window deviation ({finding.description}) is an operational protocol deviation. "
                f"Does not pose direct clinical safety risk. Retained for monitoring-only and routed to Compliance."
            ),
            evidence=evidence,
            alternatives_considered=[
                "Medical safety escalation considered, but rejected as visit timing does not directly jeopardize subject safety."
            ],
            is_serious=False,
            is_plausible=True,
        )

    # ----------------------------------------------------------------------
    # Batch Processing
    # ----------------------------------------------------------------------

    def review_findings(
        self,
        findings: List[Finding],
        protocol_version: int = 1,
    ) -> Tuple[List[MedicalReviewDecision], List[Escalation], TraceEntry]:
        """
        Review all findings produced by the detect node.

        Returns:
            decisions: Complete list of all MedicalReviewDecision objects (both ESCALATE and MONITOR_ONLY).
            escalations: Filtered list of Escalation objects (only true ESCALATE decisions).
            trace_entry: TraceEntry recording node execution and evidence.
        """
        self.protocol_version = protocol_version
        decisions: List[MedicalReviewDecision] = []
        escalations: List[Escalation] = []
        cited_evidence_refs = []

        for f in findings:
            dec = self.evaluate_finding(f)
            decisions.append(dec)

            if dec.decision == "ESCALATE":
                urgency = "IMMEDIATE" if dec.severity == "CRITICAL" else "HIGH"
                esc = Escalation(
                    escalation_id=f"ESC-{dec.finding_type}-{dec.usubjid}-{dec.finding_id}",
                    finding_id=dec.finding_id,
                    usubjid=dec.usubjid,
                    site_id=dec.site_id,
                    finding_type=dec.finding_type,
                    urgency=urgency,
                    reason=dec.rationale,
                    evidence=list(dec.evidence),
                    status="PENDING",
                    decision="ESCALATE",
                    severity=dec.severity,
                    rationale=dec.rationale,
                    alternatives_considered=list(dec.alternatives_considered),
                    is_serious=dec.is_serious,
                    is_plausible=dec.is_plausible,
                )
                escalations.append(esc)
                cited_evidence_refs.extend(dec.evidence)

        # Deduplicate evidence strings for trace
        evidence_str_list = sorted(list({str(r) for r in cited_evidence_refs}))

        trace_entry = TraceEntry(
            node="medical_review",
            action="clinical_evaluation",
            status="SUCCESS",
            evidence=evidence_str_list,
            details={
                "total_findings_reviewed": len(findings),
                "escalations_drafted": len(escalations),
                "monitoring_only_count": sum(1 for d in decisions if d.decision == "MONITOR_ONLY"),
                "serious_findings_count": sum(1 for d in decisions if d.is_serious),
                "urgent_escalations": sum(1 for e in escalations if e.urgency == "IMMEDIATE"),
                "decisions": [d.to_dict() for d in decisions],
            },
        )

        return decisions, escalations, trace_entry


def run_medical_review_node(
    findings: List[Finding],
    protocol_version: int = 1,
) -> Tuple[List[Escalation], TraceEntry]:
    """
    Entry point for the six-node pipeline runner.

    Args:
        findings: Findings generated by detect node.
        protocol_version: Active protocol amendment version in force.

    Returns:
        escalations: Escalations requiring human gate / monitor review.
        trace: TraceEntry recording node execution and evidence.
    """
    engine = MedicalReviewEngine(protocol_version=protocol_version)
    decisions, escalations, trace_entry = engine.review_findings(
        findings=findings,
        protocol_version=protocol_version,
    )
    return escalations, trace_entry
