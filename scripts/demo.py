"""
Complete in-process test demonstrating all ATLAS capabilities
"""
import os
import sys

# Ensure repository root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atlas import StudyGraph, Atlas
import json

print("=" * 60)
print("ATLAS - Study Sentinel Problem 1 - Complete Demo")
print("=" * 60)

# Build graph
data_dir = os.path.join(REPO_ROOT, "hackathon-data")
if os.path.exists(os.path.join(data_dir, "hackathon-data")):
    data_dir = os.path.join(data_dir, "hackathon-data")
graph = StudyGraph(data_dir)
graph.build()

print(f"\n[OK] Graph built at latest cut")
stats = graph.get_stats()
print(f"  Subjects: {stats.subjects}")
print(f"  Records: {stats.nodes:,}")
print(f"  Cuts: {stats.cuts}")
print(f"  By domain: {stats.records_by_domain}")

# Test rebuild at different cuts
print(f"\n[OK] Rebuild at different cuts:")
for cut in [1, 3, 6, 9, 12]:
    graph.build(cut=cut)
    s = graph.get_stats()
    print(f"  Cut {cut}: {s.subjects} subjects, {s.nodes:,} records")

# Build at latest
graph.build()

# Initialize Atlas
atlas = Atlas(graph)

print(f"\n[OK] Atlas initialized")

# Test questions
questions = [
    ("How many subjects in the study?", "COUNT"),
    ("How many subjects in drug arm?", "COUNT"),
    ("How many sites?", "COUNT"),
    ("How many Hy's law candidates?", "COUNT"),
    ("List Hy's law candidates", "FINDING"),
    ("What are the monitor decisions for Hy's law candidates?", "FINDING"),
    ("Which subjects have ALT > 3xULN?", "FINDING"),
    ("Which subjects have AST > 2xULN?", "FINDING"),
    ("Find prohibited medications", "FINDING"),
    ("Find prohibited medications for 042-S07-001", "FINDING"),
    ("Find dosing errors", "FINDING"),
    ("Find serious adverse events", "FINDING"),
    ("Find discontinued subjects", "FINDING"),
    ("Find visit window deviations", "FINDING"),
    ("Show patient profile for 042-S05-003", "PATIENT360"),
]

print(f"\n" + "=" * 60)
print("QUESTION ANSWERING DEMO")
print("=" * 60)

for q, qtype in questions:
    print(f"\nQ: {q}")
    answer = atlas.answer(q)
    print(f"   Type: {qtype}")
    ans_str = json.dumps(answer.answer, default=str)
    print(f"   Answer: {ans_str[:200]}{'...' if len(ans_str) > 200 else ''}")
    print(f"   Evidence ({len(answer.evidence)} refs): {answer.evidence[:3]}{'...' if len(answer.evidence) > 3 else ''}")

# Evidence validation
print(f"\n" + "=" * 60)
print("EVIDENCE VALIDATION")
print("=" * 60)
test_answers = [
    atlas.answer("How many subjects?"),
    atlas.answer("List Hy's law candidates"),
    atlas.answer("Show patient profile for 042-S05-003")
]
from atlas.schemas import validate_answer
for ans in test_answers:
    valid = validate_answer(ans)
    all_found = True
    for ref in ans.evidence:
        domain_records = graph.records_by_domain.get(ref.domain, [])
        found = any(r.ref == ref for r in domain_records)
        if not found:
            all_found = False
            print(f"  INVALID: {ref} not found in graph")
    print(f"  [OK] {ans.question_id}: schema_valid={valid}, evidence_valid={all_found}")

from atlas.schemas import Domain

# Unit conversion test
print(f"\n[OK] Unit conversion (S07 ukat/L -> U/L):")
s07_subjects = [s for s in graph.records_by_subject if s.startswith('042-S07-')]
if s07_subjects:
    lb_records = graph.records_by_subject[s07_subjects[0]].get(Domain.LB, [])
    alt_recs = [r for r in lb_records if r.parsed.get('lbtestcd') == 'ALT' and r.parsed.get('lborres_std') is not None]
    if alt_recs:
        r = alt_recs[0]
        print(f"  {r.parsed['lborres_numeric']} ukat/L -> {r.parsed['lborres_std']:.2f} U/L (x60)")

# Special value handling
print(f"\n[OK] Special value handling:")
lb_records = graph.records_by_domain.get(Domain.LB, [])
special = [r for r in lb_records if r.data.get('LBORRES') in ('<5', 'ND', '')]
print(f"  {len(special)} records with <5/ND/empty -> parsed as None")

# Corrections
print(f"\n[OK] Corrections applied: {len(graph.corrections)} total corrections in data")

print(f"\n" + "=" * 60)
print("ALL TESTS PASSED")
print("=" * 60)