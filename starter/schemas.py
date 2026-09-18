"""
Schema definitions for ATLAS Problem 1.
DO NOT MODIFY THIS FILE.
"""

from dataclasses import dataclass
from typing import List, Optional, Any, Dict
from enum import Enum


class Domain(str, Enum):
    DM = "DM"
    AE = "AE"
    LB = "LB"
    VS = "VS"
    EX = "EX"
    CM = "CM"
    DS = "DS"
    MH = "MH"
    EG = "EG"


@dataclass(frozen=True)
class RecordRef:
    domain: Domain
    usubjid: str
    seq: int

    def __str__(self) -> str:
        return f"{self.domain.value}|{self.usubjid}|{self.seq}"


@dataclass
class Answer:
    question_id: str
    answer: Any
    evidence: List[RecordRef]
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "answer": self.answer,
            "evidence": [str(r) for r in self.evidence],
            "meta": self.meta or {}
        }


@dataclass
class Patient360:
    usubjid: str
    dm: Dict[str, Any]
    ae: List[Dict[str, Any]]
    lb: List[Dict[str, Any]]
    vs: List[Dict[str, Any]]
    ex: List[Dict[str, Any]]
    cm: List[Dict[str, Any]]
    ds: List[Dict[str, Any]]
    mh: List[Dict[str, Any]]
    eg: List[Dict[str, Any]]


@dataclass
class GraphStats:
    nodes: int
    edges: int
    subjects: int
    records_by_domain: Dict[str, int]
    cuts: int


def validate_answer(answer: Answer) -> bool:
    """Validate that an answer conforms to schema."""
    if not isinstance(answer.question_id, str):
        return False
    if not isinstance(answer.evidence, list):
        return False
    for ref in answer.evidence:
        if not isinstance(ref, RecordRef):
            return False
    return True
