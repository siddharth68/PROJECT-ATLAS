"""
Schema definitions for Problem 2 - MONITOR.

These dataclasses define the data contracts exchanged between the six execution nodes:
1. detect
2. medical_review
3. data_manager
4. compliance
5. human_gate
6. execute
and returned in the final ReviewReport.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

# Stage 2 imports Stage 1 as required by challenge specification
from stage1.atlas import RecordRef, Domain


@dataclass
class Finding:
    """A clinical, protocol, or data anomaly detected by the detect node."""
    finding_id: str
    finding_type: str  # e.g., HYS_LAW_CANDIDATE, DOSING_ERROR, PROHIBITED_MED, VISIT_WINDOW_DEVIATION, SAE_UNESCALATED
    usubjid: str
    site_id: str
    description: str
    severity: str      # CRITICAL, HIGH, MEDIUM, LOW
    evidence: List[RecordRef] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    cut: Optional[int] = None
    protocol_version: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "finding_type": self.finding_type,
            "usubjid": self.usubjid,
            "site_id": self.site_id,
            "description": self.description,
            "severity": self.severity,
            "evidence": [str(r) for r in self.evidence],
            "data": self.data,
            "cut": self.cut,
            "protocol_version": self.protocol_version,
        }


@dataclass
class MedicalReviewDecision:
    """Structured clinical decision produced by the medical_review node."""
    decision_id: str
    finding_id: str
    finding_type: str
    usubjid: str
    site_id: str
    severity: str                     # CRITICAL, HIGH, MEDIUM, LOW
    decision: str                     # ESCALATE, MONITOR_ONLY
    rationale: str
    evidence: List[RecordRef] = field(default_factory=list)
    alternatives_considered: List[str] = field(default_factory=list)
    is_serious: bool = False
    is_plausible: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "finding_id": self.finding_id,
            "finding_type": self.finding_type,
            "usubjid": self.usubjid,
            "site_id": self.site_id,
            "severity": self.severity,
            "decision": self.decision,
            "rationale": self.rationale,
            "evidence": [str(r) for r in self.evidence],
            "alternatives_considered": self.alternatives_considered,
            "is_serious": self.is_serious,
            "is_plausible": self.is_plausible,
        }


@dataclass
class Escalation:
    """A clinical safety escalation flagged for human/medical monitor review."""
    escalation_id: str
    finding_id: str
    usubjid: str
    site_id: str
    finding_type: str
    urgency: str       # IMMEDIATE, HIGH, STANDARD
    reason: str
    evidence: List[RecordRef] = field(default_factory=list)
    status: str = "PENDING"  # PENDING, APPROVED, REJECTED, CLARIFIED, RESOLVED
    monitor_decision: Optional[str] = None  # APPROVED, REJECTED, CLARIFY
    monitor_reason: Optional[str] = None
    clarification_response: Optional[str] = None
    # Medical review decision fields
    decision: str = "ESCALATE"
    severity: str = "HIGH"
    rationale: str = ""
    alternatives_considered: List[str] = field(default_factory=list)
    is_serious: bool = True
    is_plausible: bool = True

    def __post_init__(self):
        if not self.rationale and self.reason:
            self.rationale = self.reason
        elif not self.reason and self.rationale:
            self.reason = self.rationale

    def to_dict(self) -> Dict[str, Any]:
        return {
            "escalation_id": self.escalation_id,
            "finding_id": self.finding_id,
            "usubjid": self.usubjid,
            "site_id": self.site_id,
            "finding_type": self.finding_type,
            "urgency": self.urgency,
            "reason": self.reason,
            "evidence": [str(r) for r in self.evidence],
            "status": self.status,
            "monitor_decision": self.monitor_decision,
            "monitor_reason": self.monitor_reason,
            "clarification_response": self.clarification_response,
            "decision": self.decision,
            "severity": self.severity,
            "rationale": self.rationale,
            "alternatives_considered": self.alternatives_considered,
            "is_serious": self.is_serious,
            "is_plausible": self.is_plausible,
        }


@dataclass
class Query:
    """A data management query issued to an investigational site."""
    query_id: str
    finding_id: str
    usubjid: str
    site_id: str
    domain: str
    description: str
    status: str = "OPEN"  # OPEN, PENDING, RESOLVED, CLOSED
    evidence: List[RecordRef] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_id": self.query_id,
            "finding_id": self.finding_id,
            "usubjid": self.usubjid,
            "site_id": self.site_id,
            "domain": self.domain,
            "description": self.description,
            "status": self.status,
            "evidence": [str(r) for r in self.evidence],
        }


@dataclass
class ComplianceDeviation:
    """A protocol compliance deviation (e.g. visit window or prohibited medication)."""
    deviation_id: str
    finding_id: str
    usubjid: str
    site_id: str
    deviation_type: str  # e.g., VISIT_WINDOW, PROHIBITED_MEDICATION, DOSING_SCHEDULE
    protocol_version: int
    description: str
    evidence: List[RecordRef] = field(default_factory=list)
    severity: str = "MINOR"  # MAJOR, MINOR

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deviation_id": self.deviation_id,
            "finding_id": self.finding_id,
            "usubjid": self.usubjid,
            "site_id": self.site_id,
            "deviation_type": self.deviation_type,
            "protocol_version": self.protocol_version,
            "description": self.description,
            "evidence": [str(r) for r in self.evidence],
            "severity": self.severity,
        }


@dataclass
class TraceEntry:
    """An audit trail trace entry emitted by an execution node."""
    node: str          # detect, medical_review, data_manager, compliance, human_gate, execute
    action: str
    status: str        # SUCCESS, WARNING, ERROR, SKIPPED
    usubjid: Optional[str] = None
    evidence: List[str] = field(default_factory=list)  # String representation of RecordRefs
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "action": self.action,
            "status": self.status,
            "usubjid": self.usubjid,
            "evidence": self.evidence,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class ReviewReport:
    """The final structured cycle report produced by ReviewCrew."""
    cut: int
    protocol_version: int
    findings: List[Finding] = field(default_factory=list)
    escalations: List[Escalation] = field(default_factory=list)
    queries: List[Query] = field(default_factory=list)
    compliance_deviations: List[ComplianceDeviation] = field(default_factory=list)
    trace: List[TraceEntry] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cut": self.cut,
            "protocol_version": self.protocol_version,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "escalations": [e.to_dict() for e in self.escalations],
            "queries": [q.to_dict() for q in self.queries],
            "compliance_deviations": [d.to_dict() for d in self.compliance_deviations],
            "trace": [t.to_dict() for t in self.trace],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)
