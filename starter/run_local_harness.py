#!/usr/bin/env python3
"""
ATLAS Local Evaluation Harness.

Usage:
    python starter/run_local_harness.py --module stage1.atlas --data hackathon-data
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time

# Ensure workspace root is on sys.path
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from starter.schemas import Domain, RecordRef, Answer, validate_answer


PUBLIC_QUESTIONS = [
    {
        "id": "Q1_COUNT_TOTAL",
        "question": "How many subjects in the study?",
        "kind": "COUNT",
        "expected_desc": "Exact integer (241) and supporting DM RecordRefs",
    },
    {
        "id": "Q2_COUNT_ARM",
        "question": "How many subjects in drug arm?",
        "kind": "COUNT",
        "expected_desc": "Exact integer (128) and supporting drug-arm DM RecordRefs",
    },
    {
        "id": "Q3_COUNT_DISC_AE",
        "question": "How many subjects at site S01 discontinued due to an adverse event?",
        "kind": "COUNT",
        "expected_desc": "Exact integer (1) and supporting DS RecordRef for 042-S01-010",
    },
    {
        "id": "Q4_COUNT_SITES",
        "question": "How many sites in the study?",
        "kind": "COUNT",
        "expected_desc": "Exact integer (12) and supporting site DM RecordRefs",
    },
    {
        "id": "Q5_LOOKUP_VISIT_WINDOW",
        "question": "List the laboratory and adverse-event records for 042-S01-001 within 14 days of the WEEK4 visit",
        "kind": "LOOKUP",
        "expected_desc": "Exact list of RecordRefs within 14 days of WEEK4 visit date",
    },
    {
        "id": "Q6_LOOKUP_LAB_VAL",
        "question": "What was subject 042-S01-001's ALT at Week 4?",
        "kind": "LOOKUP",
        "expected_desc": "ALT record for 042-S01-001 at WEEK4 and supporting LB RecordRef",
    },
    {
        "id": "Q7_FINDING_HYS_LAW",
        "question": "Which subjects meet potential Hy's law criteria?",
        "kind": "FINDING",
        "expected_desc": "Hy's law candidate (042-S05-003) and qualifying ALT/BILI RecordRefs",
    },
    {
        "id": "Q8_FINDING_DOSING",
        "question": "Which subjects had dosing errors?",
        "kind": "FINDING",
        "expected_desc": "Subjects with dose violations and supporting EX RecordRefs",
    },
    {
        "id": "Q9_FINDING_PROHIBITED",
        "question": "Identify subjects with prohibited medications",
        "kind": "FINDING",
        "expected_desc": "Subjects with prohibited meds and supporting CM RecordRefs",
    },
    {
        "id": "Q10_TRAP_SITE_DOSE",
        "question": "Which subjects at site S01 received a wrong dose?",
        "kind": "TRAP",
        "expected_desc": "Empty answer [] and empty evidence [] (honest trap answer)",
    },
]


def resolve_data_dir(raw_path: str) -> str:
    """Resolve data directory even if nested (hackathon-data/hackathon-data)."""
    if os.path.exists(os.path.join(raw_path, "data")):
        return raw_path
    nested = os.path.join(raw_path, "hackathon-data")
    if os.path.exists(os.path.join(nested, "data")):
        return nested
    return raw_path


def main():
    parser = argparse.ArgumentParser(description="ATLAS Problem 1 Local Test Harness")
    parser.add_argument("--module", default="stage1.atlas", help="Module to test, e.g. stage1.atlas")
    parser.add_argument("--data", default="hackathon-data", help="Path to hackathon dataset")
    parser.add_argument("--output", default="stage1_public.json", help="Path to save public run results")
    parser.add_argument("--stats", default="graph_stats.json", help="Path to save graph stats")
    args = parser.parse_args()

    data_dir = resolve_data_dir(args.data)

    print("=" * 70)
    print("           ATLAS — STUDY SENTINEL EVALUATION HARNESS")
    print("=" * 70)
    print(f"  Testing module: {args.module}")
    print(f"  Data directory: {data_dir}")
    print("=" * 70)

    # 1. Import module
    try:
        mod = importlib.import_module(args.module)
        StudyGraph = getattr(mod, "StudyGraph")
        Atlas = getattr(mod, "Atlas")
        EvidenceValidator = getattr(mod, "EvidenceValidator", None)
    except Exception as e:
        print(f"\n[FATAL] Failed to import {args.module}: {e}")
        return 1

    # 2. Build graph and verify build performance
    print("\n>>> Building Knowledge Graph...")
    t_start = time.time()
    graph = StudyGraph(data_dir)
    graph.build()
    t_build = time.time() - t_start

    stats = graph.get_stats()
    stats_dict = {
        "nodes": stats.nodes,
        "edges": stats.edges,
        "subjects": stats.subjects,
        "records_by_domain": stats.records_by_domain,
        "cuts": stats.cuts,
        "build_time_seconds": round(t_build, 3),
    }

    print(f"    Build completed in {t_build:.3f}s")
    print(f"    Nodes (records): {stats.nodes:,}")
    print(f"    Edges:           {stats.edges:,}")
    print(f"    Subjects:        {stats.subjects}")
    print(f"    Domains:         {stats.records_by_domain}")

    # Save graph_stats.json
    stats_path = os.path.join(WORKSPACE_ROOT, args.stats)
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats_dict, f, indent=2)
    print(f"    Saved: {args.stats}")

    # 3. Instantiate QA Engine
    atlas = Atlas(graph)
    if EvidenceValidator is not None:
        validator = EvidenceValidator(graph)
    else:
        from atlas.evidence import EvidenceValidator as EV
        validator = EV(graph)

    # 4. Evaluate Public Questions
    print("\n" + "=" * 70)
    print("           EVALUATING 10 PUBLIC QUESTIONS")
    print("=" * 70)

    results_for_export = []
    all_passed = True
    total_time = 0.0

    for idx, item in enumerate(PUBLIC_QUESTIONS, 1):
        q_id = item["id"]
        q_text = item["question"]
        q_kind = item["kind"]

        t0 = time.time()
        answer_obj = atlas.answer(q_text, question_id=q_id)
        dt = time.time() - t0
        total_time += dt

        # Schema Validation
        is_schema_valid = validate_answer(answer_obj)

        # Evidence Validation
        evidence_valid = True
        for ref in answer_obj.evidence:
            if not validator.record_exists(ref):
                evidence_valid = False
                break

        # Time Limit Check (< 120s)
        time_ok = dt < 120.0

        passed = is_schema_valid and evidence_valid and time_ok
        if not passed:
            all_passed = False

        status_str = "[PASS]" if passed else "[FAIL]"
        print(f"\n{idx:02d}. {status_str} [{q_kind}] {q_id}")
        print(f"    Question: \"{q_text}\"")

        # Format answer display cleanly
        if isinstance(answer_obj.answer, list):
            ans_display = f"List of {len(answer_obj.answer)} item(s)"
            if len(answer_obj.answer) <= 3:
                ans_display += f": {answer_obj.answer}"
        elif isinstance(answer_obj.answer, dict):
            ans_display = f"Dict with keys {list(answer_obj.answer.keys())}"
        else:
            ans_display = str(answer_obj.answer)

        print(f"    Answer:   {ans_display}")
        print(f"    Evidence: {len(answer_obj.evidence)} citation(s)")
        print(f"    Latency:  {dt*1000:.1f} ms  (Limit: 120s)")

        # Prepare export entry
        results_for_export.append({
            "question_id": answer_obj.question_id,
            "question": q_text,
            "kind": q_kind,
            "answer": answer_obj.answer,
            "evidence": [str(r) for r in answer_obj.evidence],
            "meta": answer_obj.meta or {},
            "latency_ms": round(dt * 1000, 2),
            "schema_valid": is_schema_valid,
            "evidence_valid": evidence_valid,
        })

    # Save stage1_public.json
    export_path = os.path.join(WORKSPACE_ROOT, args.output)
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(results_for_export, f, indent=2, default=str)

    print("\n" + "=" * 70)
    print("                    EVALUATION SUMMARY")
    print("=" * 70)
    print(f"  Questions Evaluated: {len(PUBLIC_QUESTIONS)}")
    print(f"  All Schema Valid:    {'YES' if all(r['schema_valid'] for r in results_for_export) else 'NO'}")
    print(f"  All Evidence Valid:  {'YES' if all(r['evidence_valid'] for r in results_for_export) else 'NO'}")
    print(f"  Max Question Time:   {max(r['latency_ms'] for r in results_for_export):.1f} ms")
    print(f"  Total Answering:     {total_time*1000:.1f} ms")
    print(f"  Public Run Exported: {args.output}")
    print(f"  Status:              {'SUCCEEDED (10/10 PASS)' if all_passed else 'FAILED'}")
    print("=" * 70)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
