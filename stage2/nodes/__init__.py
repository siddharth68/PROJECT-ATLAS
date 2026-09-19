"""
Stage 2 MONITOR Execution Nodes.

The six-node sequential execution pipeline:
1. detect          - Discovers safety, protocol, and dosing findings via Stage 1 Atlas
2. medical_review  - Evaluates clinical severity and drafts escalations
3. data_manager    - Identifies data discrepancies and generates site queries
4. compliance      - Evaluates protocol deviations against active protocol version
5. human_gate      - Reviews escalations with medical monitor decisions (APPROVED, REJECTED, CLARIFY)
6. execute         - Commits actions, maintains cycle memory, and compiles ReviewReport
"""
from stage2.nodes.detect import run_detect_node
from stage2.nodes.medical_review import run_medical_review_node, MedicalReviewEngine
from stage2.nodes.data_manager import run_data_manager_node
from stage2.nodes.compliance import run_compliance_node
from stage2.nodes.human_gate import run_human_gate_node
from stage2.nodes.execute import run_execute_node

__all__ = [
    "run_detect_node",
    "run_medical_review_node",
    "MedicalReviewEngine",
    "run_data_manager_node",
    "run_compliance_node",
    "run_human_gate_node",
    "run_execute_node",
]
