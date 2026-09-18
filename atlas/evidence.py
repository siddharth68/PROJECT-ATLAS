"""
Evidence validation layer for ATLAS.

Ensures every RecordRef:
- Actually exists in the study graph
- Corresponds to the correct subject, domain, and sequence
- Directly supports the claim being made (no neighbouring or fabricated records)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from atlas.schemas import Domain, RecordRef, Answer
from atlas.graph import StudyGraph, NormalizedRecord


class EvidenceValidator:
    """Validates and filters evidence citations for truth and relevance."""

    def __init__(self, graph: StudyGraph):
        self.graph = graph

    def record_exists(self, ref: RecordRef) -> bool:
        """Check if a RecordRef exists in the study graph."""
        if not isinstance(ref, RecordRef):
            return False
        return self.graph.get_record(str(ref)) is not None

    def get_record(self, ref: RecordRef) -> Optional[NormalizedRecord]:
        """Retrieve the NormalizedRecord for a RecordRef, if it exists."""
        if not isinstance(ref, RecordRef):
            return None
        return self.graph.get_record(str(ref))

    def validate_ref(
        self,
        ref: RecordRef,
        expected_domain: Optional[Domain] = None,
        expected_usubjid: Optional[str] = None,
    ) -> bool:
        """Check that ref is a valid RecordRef that exists and matches domain/subject."""
        if not isinstance(ref, RecordRef):
            return False
        if expected_domain and ref.domain != expected_domain:
            return False
        if expected_usubjid and ref.usubjid != expected_usubjid:
            return False
        rec = self.graph.get_record(str(ref))
        if rec is None:
            return False
        return True

    def validate_claim_evidence(
        self,
        claim_type: str,
        ref: RecordRef,
        expected_usubjid: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Verify that a RecordRef directly supports the specified claim.

        Rejects:
        - Non-existent records
        - Mismatched subjects
        - Irrelevant domains (e.g. DM when claiming Hy's law)
        - Records that do not meet the condition of the claim
        """
        if not self.validate_ref(ref, expected_usubjid=expected_usubjid):
            return False

        rec = self.graph.get_record(str(ref))
        if rec is None:
            return False

        claim = claim_type.upper()

        if claim == "HYS_LAW":
            # Must be an LB record for ALT, AST, or BILI
            if ref.domain != Domain.LB:
                return False
            testcd = rec.parsed.get("lbtestcd")
            if testcd not in ("ALT", "AST", "BILI"):
                return False
            val = rec.parsed.get("lborres_std")
            uln = rec.parsed.get("ref_high_std")
            if val is None or uln is None or uln <= 0:
                return False
            if testcd in ("ALT", "AST") and val > 3.0 * uln:
                return True
            if testcd == "BILI" and val > 2.0 * uln:
                return True
            return False

        elif claim == "PROHIBITED_MED":
            # Must be a CM record with a prohibited medication class
            if ref.domain != Domain.CM:
                return False
            cmclas = (rec.parsed.get("cmclas") or rec.data.get("CMCLAS") or "").upper()
            prohibited = {
                "SULFONYLUREA",
                "SYSTEMIC_GLUCOCORTICOID",
                "SYSTEMIC GLUCOCORTICOID",
            }
            return cmclas in prohibited

        elif claim == "DOSING_ERROR":
            # Must be an EX record with dose != 10 (drug) or dose != 0 (placebo)
            if ref.domain != Domain.EX:
                return False
            dose = rec.parsed.get("exdose")
            if dose is None:
                return False
            dm_records = self.graph.records_by_subject.get(ref.usubjid, {}).get(Domain.DM, [])
            if not dm_records:
                return False
            arm = dm_records[0].parsed.get("arm", "").upper()
            if arm == "DRUG" and dose != 10:
                return True
            if arm == "PLACEBO" and dose != 0:
                return True
            return False

        elif claim == "SAE":
            # Must be an AE record with serious flag
            if ref.domain != Domain.AE:
                return False
            if rec.parsed.get("is_serious"):
                return True
            aeser = (rec.parsed.get("aeser") or rec.data.get("AESER") or "").upper()
            aeshosp = (rec.parsed.get("aeshosp") or rec.data.get("AESHOSP") or "").upper()
            return aeser == "Y" or aeshosp == "Y"

        elif claim == "VISIT_DEVIATION":
            # Must be a visit record in VS, LB, EG, or EX
            if ref.domain not in (Domain.VS, Domain.LB, Domain.EG, Domain.EX):
                return False
            return True

        elif claim == "DISCONTINUATION":
            # Must be a DS record with DISCONTINUED
            if ref.domain != Domain.DS:
                return False
            dsdecod = (rec.parsed.get("dsdecod") or rec.data.get("DSDECOD") or "").upper()
            return dsdecod == "DISCONTINUED"

        elif claim == "LAB_THRESHOLD":
            # Must be an LB record
            if ref.domain != Domain.LB:
                return False
            if context and "testcd" in context:
                if rec.parsed.get("lbtestcd") != context["testcd"]:
                    return False
            return True

        elif claim == "SUBJECT_COUNT":
            # Must be a DM record
            if ref.domain != Domain.DM:
                return False
            if context and "arm" in context:
                arm = rec.parsed.get("arm", "").upper()
                if arm != context["arm"].upper():
                    return False
            return True

        elif claim == "LOOKUP":
            if context and "domain" in context:
                expected = context["domain"]
                if isinstance(expected, str):
                    if ref.domain.value != expected:
                        return False
                elif ref.domain != expected:
                    return False
            return True

        # Default fallback: ref exists and matches subject
        return True

    def filter_valid_evidence(
        self,
        evidence: List[RecordRef],
        claim_type: Optional[str] = None,
        expected_usubjid: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[RecordRef]:
        """Filter out non-existent, irrelevant, or fabricated RecordRefs.

        Preserves order and deduplicates citations.
        """
        seen: Set[str] = set()
        valid: List[RecordRef] = []

        for ref in evidence:
            if not isinstance(ref, RecordRef):
                continue
            ref_str = str(ref)
            if ref_str in seen:
                continue
            if not self.record_exists(ref):
                continue
            if expected_usubjid and ref.usubjid != expected_usubjid:
                continue
            if claim_type and not self.validate_claim_evidence(
                claim_type, ref, expected_usubjid, context
            ):
                continue
            seen.add(ref_str)
            valid.append(ref)

        return valid

    def validate_answer_evidence(
        self,
        answer: Answer,
        claim_type: Optional[str] = None,
        expected_usubjid: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Answer:
        """Sanitize an Answer object's evidence in-place and return it."""
        answer.evidence = self.filter_valid_evidence(
            answer.evidence,
            claim_type=claim_type,
            expected_usubjid=expected_usubjid,
            context=context,
        )
        return answer


def validate_evidence(
    graph: StudyGraph,
    evidence: List[RecordRef],
    claim_type: Optional[str] = None,
    expected_usubjid: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> List[RecordRef]:
    """Functional interface for evidence filtering and validation."""
    validator = EvidenceValidator(graph)
    return validator.filter_valid_evidence(
        evidence,
        claim_type=claim_type,
        expected_usubjid=expected_usubjid,
        context=context,
    )
