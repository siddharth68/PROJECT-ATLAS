"""
ATLAS - Study Sentinel Problem 1 Solution
"""
from atlas.schemas import Domain, RecordRef, Answer, Patient360, GraphStats, validate_answer
from atlas.graph import StudyGraph
from atlas.atlas import Atlas
from atlas.evidence import EvidenceValidator, validate_evidence

__all__ = [
    'Domain',
    'RecordRef', 
    'Answer',
    'Patient360',
    'GraphStats',
    'validate_answer',
    'StudyGraph',
    'Atlas',
    'EvidenceValidator',
    'validate_evidence',
]

__version__ = '1.0.0'