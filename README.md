# ATLAS & STUDY SENTINEL — Unified Clinical Intelligence Platform

A high-performance, deterministic CDISC Knowledge Graph and autonomous 6-node clinical trial safety monitoring system built entirely with Python standard library (zero 3rd-party pip dependencies).

---

## 📁 Codebase Directory Structure

```
agentathon/
│
├── app.py                     # Unified Web Application & REST API (HTTP Server + JSON endpoints)
├── main.py                    # Main Entry Point Runner
├── server.py                  # Convenience Port Runner (default: port 8000)
├── requirements.txt           # Dependency declaration (Zero 3rd-party pip packages required)
├── README.md                  # Project overview, directory guide, and usage instructions
│
├── atlas/                     # Problem 1: CDISC Knowledge Graph & Q&A Engine
│   ├── atlas.py               # Core Atlas Agent, Query Routing & Answer Synthesis
│   ├── graph.py               # In-Memory StudyGraph (27,166 records, sub-ms traversal)
│   ├── reasoning.py           # Deterministic Clinical Logic (Hy's Law, Dosing, Meds, SAEs)
│   ├── evidence.py            # RecordRef Provenance Verification & Audit Layer
│   ├── data_loader.py         # CDISC Ingestion, Sanitization & S07 Unit Conversion
│   ├── schemas.py             # Strongly-Typed Domain Entities & Answers
│   └── cli.py                 # Command-Line Query Interface
│
├── stage1/                    # Stage 1 Official Interface Module
│   └── atlas.py               # Entry point for 'python -m stage1.atlas'
│
├── stage2/                    # Problem 2: Autonomous Clinical Surveillance
│   ├── crew.py                # ReviewCrew 6-Node Pipeline Orchestrator
│   ├── detect.py              # Node 1: Clinical & Data Anomaly Detection
│   ├── medical_review.py      # Node 2: Clinical Review (Hospitalization Rule & Severity)
│   ├── data_manager.py        # Node 3: Automated Site Queries Engine
│   ├── compliance.py          # Node 4: Protocol Deviation Analyzer (v1/v2/v3 Windows)
│   ├── human_gate.py          # Node 5: Interactive Medical Monitor Decision Gate
│   ├── execute.py             # Node 6: Cycle Memory, Deduplication & JSON Compiler
│   └── schemas.py             # ReviewReport, Escalation & Query Schemas
│
├── frontend/                  # Clinical Dashboard User Interface
│   └── index.html             # Corporate White Template Dashboard (Zero Build Step)
│
├── tests/                     # Automated Test Suite (All Passing)
│   ├── test_stage2.py         # 6-Node Pipeline Order & Cycle Idempotence Tests
│   ├── test_medical_review.py # SAE Escalations & AESHOSP Hospitalization Rule Tests
│   ├── test_data_compliance.py# Data Manager Queries & Protocol Compliance Tests
│   ├── test_graph.py          # StudyGraph Ingestion & Patient 360 Retrieval Tests
│   └── test_reasoning.py      # Clinical Q&A, Traps & Hy's Law Precision Tests
│
├── scripts/                   # Utility Scripts & In-Process Demos
│   ├── demo.py                # End-to-End In-Process Capability Demo
│   ├── test_cuts.py           # Multi-Cut Rebuild Test Script
│   ├── test_more.py           # Batch Question Integration Script
│   └── test_server.py         # Server Endpoint Smoke Test Script
│
├── benchmarks/                # Challenge Question Sets & Validation Benchmarks
│   ├── stage1_public.json     # Problem 1 Benchmark Questions & Ground Truth
│   ├── stage2_public.json     # Problem 2 Benchmark Monitoring Output
│   └── graph_stats.json       # Graph Node & Edge Telemetry Metrics
│
├── docs/                      # Challenge Specifications & Protocol Reference
│   ├── Study_Sentinel_Problem_1_Atlas.pdf  # Problem 1 Specification Document
│   ├── Study_Sentinel_Templates.docx       # Challenge Templates & Rules
│   └── specs/                 # Extracted Study Text & Criteria
│
├── starter/                   # Evaluation Harness
│   ├── run_local_harness.py   # Official Batch Evaluation Runner
│   └── schemas.py             # Evaluation Schemas
│
└── hackathon-data/            # Multi-Cut CDISC Clinical Trial Data (Cuts 1-12)
```

---

## 🚀 Quickstart & Execution

### 1. Launch the Unified Web Dashboard
Starts the pure Python clinical intelligence dashboard on port 8000:
```bash
python app.py
```
*(Or specify a custom port: `python app.py 8080`, or run `python main.py`)*  
Open your browser to: **`http://127.0.0.1:8000`**

### 2. Run Stage 1 ATLAS CLI Queries
```bash
# Query Hy's Law candidates at Cut 5
python -m stage1.atlas --data hackathon-data --question "how many potential hy's law cases are there in cut 5"

# Query Patient Profile
python -m stage1.atlas --data hackathon-data --question "Show patient profile for 042-S07-001"

# Query Study-Wide Metrics
python -m stage1.atlas --data hackathon-data --question "How many subjects in the study?"
```

### 3. Run Stage 2 Autonomous Monitoring Cycle
```bash
# Execute 6-node surveillance cycle at Cut 5, Protocol v2
python -m stage2.crew --data hackathon-data --cut 5 --protocol 2 --export stage2_public.json
```

### 4. Run Automated Test Suites
```bash
# Run all unit and integration tests
python -m unittest discover -s tests -p "test_*.py"

# Or run individual test suites
python tests/test_stage2.py
python tests/test_medical_review.py
python tests/test_data_compliance.py
python tests/test_graph.py
python tests/test_reasoning.py
```

### 5. Run Official Challenge Evaluation Harness
```bash
python starter/run_local_harness.py --module stage1.atlas --data hackathon-data
```

---

## 🧠 Core System Capabilities

### Problem 1: ATLAS Knowledge Graph & Q&A
- **CDISC Standardization Across 9 Domains**: Ingests `DM`, `AE`, `LB`, `VS`, `EX`, `CM`, `DS`, `MH`, and `EG`.
- **SI Unit Conversion**: Investigational Site S07 records ALT/AST in $\mu\text{kat/L}$; the system automatically standardizes to $\text{U/L}$ using $1\,\mu\text{kat/L} = 60\,\text{U/L}$.
- **Hy's Law Precision**: Identifies concurrent $\text{ALT or AST} > 3\times\text{ULN}$ and $\text{Total Bilirubin} > 2\times\text{ULN}$ within 14 days, properly evaluating Protocol baseline exclusions (e.g. S03 pre-existing hepatitis).
- **Zero Hallucination Guarantee**: Every answer is verified against the knowledge graph with cited `RecordRef` identifiers. Unmatched/trap queries return empty sets (`[]`) with `0` evidence fabricated.
- **Sub-Millisecond Patient 360**: Assembles complete multi-domain longitudinal patient profiles in $< 0.1\,\text{ms}$.

### Problem 2: MONITOR Autonomous Review Crew
Deterministic 6-node pipeline executing in strict order:
$$\text{detect} \longrightarrow \text{medical\_review} \longrightarrow \text{data\_manager} \longrightarrow \text{compliance} \longrightarrow \text{human\_gate} \longrightarrow \text{execute}$$

1. **`detect`**: Identifies Hy's Law signals, dosing schedule errors, prohibited concomitant medications, visit window deviations, and serious adverse events with graph evidence.
2. **`medical_review`**: Evaluates clinical seriousness and plausibility. **Mandatory Protocol Rule**: If `AESHOSP = 'Y'`, hospitalization makes the event serious and mandates escalation, even if recorded as `AESER = 'N'`.
3. **`data_manager`**: Converts data discrepancies into specific, actionable site queries associated with subject, site, and domain.
4. **`compliance`**: Audits visit windows against active protocol amendments (v1: $\pm 7\text{d}$, v2: $\pm 5\text{d}$, v3: $\pm 3\text{d}$) and flags prohibited medications.
5. **`human_gate`**: Evaluates monitor replies (`APPROVED`, `REJECTED`, `CLARIFY`), providing dynamic graph evidence for clarifications.
6. **`execute`**: Maintains state across monitoring cycles, guaranteeing **idempotence** (re-running the same cut produces zero duplicate queries or escalations).
