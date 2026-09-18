#!/usr/bin/env python3
"""
Focused tests for StudyGraph and Patient360.

Tests cover:
 1. Single-subject graph construction
 2. Multiple subjects
 3. Patient 360 completeness
 4. Cross-table relationships
 5. Duplicate records
 6. Duplicate subjects
 7. Missing / malformed fields
 8. build(cut=...)
 9. Rebuild after source data changes
10. Fast Patient 360 retrieval
"""

import csv
import json
import os
import shutil
import sys
import tempfile
import time

# Ensure parent dir on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atlas.schemas import Domain, RecordRef, Patient360, GraphStats, validate_answer
from atlas.graph import StudyGraph, NormalizedRecord


# ── helpers ──────────────────────────────────────────────────────────

PASS = 0
FAIL = 0


def ok(label: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {label}"
        if detail:
            msg += f" -- {detail}"
        print(msg)


DATA_DIR = None  # set in main()


def _build_graph(cut=None) -> StudyGraph:
    g = StudyGraph(DATA_DIR)
    g.build(cut)
    return g


# ── Test 1: single-subject graph construction ────────────────────────

def test_single_subject():
    print("\n--- Test 1: Single-subject graph construction ---")
    g = _build_graph()
    subj = g.get_subjects()[0]
    domains = g.records_by_subject.get(subj, {})
    ok("subject exists in graph", subj != "")
    ok("has DM record", Domain.DM in domains and len(domains[Domain.DM]) > 0)
    ok("DM has arm", domains[Domain.DM][0].parsed.get("arm") in ("DRUG", "PLACEBO"))
    ok("DM has siteid", bool(domains[Domain.DM][0].parsed.get("siteid")))
    ok("ref string format", str(domains[Domain.DM][0].ref).startswith("DM|"))


# ── Test 2: multiple subjects ───────────────────────────────────────

def test_multiple_subjects():
    print("\n--- Test 2: Multiple subjects ---")
    g = _build_graph()
    subjects = g.get_subjects()
    ok("more than 1 subject", len(subjects) > 1, f"got {len(subjects)}")
    ok("subjects are sorted", subjects == sorted(subjects))
    # every subject has DM
    all_have_dm = all(
        Domain.DM in g.records_by_subject[s] for s in subjects
    )
    ok("every subject has DM", all_have_dm)


# ── Test 3: Patient 360 completeness ────────────────────────────────

def test_patient360_completeness():
    print("\n--- Test 3: Patient 360 completeness ---")
    g = _build_graph()
    subj = g.get_subjects()[0]
    p = g.patient360(subj)
    ok("P360 type", isinstance(p, Patient360))
    ok("P360 usubjid", p.usubjid == subj)
    ok("P360 dm dict", isinstance(p.dm, dict) and "arm" in p.dm)
    ok("P360 ae list", isinstance(p.ae, list))
    ok("P360 lb list", isinstance(p.lb, list))
    ok("P360 vs list", isinstance(p.vs, list))
    ok("P360 ex list", isinstance(p.ex, list))
    ok("P360 cm list", isinstance(p.cm, list))
    ok("P360 ds list", isinstance(p.ds, list))
    ok("P360 mh list", isinstance(p.mh, list))
    ok("P360 eg list", isinstance(p.eg, list))
    # each domain record carries a ref
    if p.lb:
        ok("lb record has ref", hasattr(p.lb[0].get("ref", None), "domain"))
        ok("lb record has lbtestcd", "lbtestcd" in p.lb[0])
    # non-existent subject returns empty P360
    p2 = g.patient360("DOES-NOT-EXIST")
    ok("unknown subject -> empty P360", p2.dm == {} and p2.lb == [])


# ── Test 4: cross-table relationships ───────────────────────────────

def test_cross_table():
    print("\n--- Test 4: Cross-table relationships ---")
    g = _build_graph()
    # every AE subject should exist in DM
    ae_subjs = set(r.ref.usubjid for r in g.records_by_domain.get(Domain.AE, []))
    dm_subjs = set(g.get_subjects())
    orphans = ae_subjs - dm_subjs
    ok("no orphan AE subjects", len(orphans) == 0, f"orphans: {orphans}")

    # every LB subject in DM
    lb_subjs = set(r.ref.usubjid for r in g.records_by_domain.get(Domain.LB, []))
    orphans_lb = lb_subjs - dm_subjs
    ok("no orphan LB subjects", len(orphans_lb) == 0)

    # site index populated
    ok("site index non-empty", len(g._by_site) > 0)
    for site, subs in g._by_site.items():
        ok(f"site {site} subjects exist in DM", subs.issubset(dm_subjs))
        break  # just check one


# ── Test 5: duplicate records ───────────────────────────────────────

def test_duplicate_records():
    print("\n--- Test 5: Duplicate records ---")
    g = _build_graph()
    # check no two records in same domain share (usubjid, seq)
    for domain, recs in g.records_by_domain.items():
        seen = set()
        dupes = 0
        for r in recs:
            key = (r.ref.usubjid, r.ref.seq)
            if key in seen:
                dupes += 1
            seen.add(key)
        ok(f"{domain.value} no dup (subj,seq)", dupes == 0, f"{dupes} dupes")


# ── Test 6: duplicate subjects ──────────────────────────────────────

def test_duplicate_subjects():
    print("\n--- Test 6: Duplicate subjects ---")
    g = _build_graph()
    # every subject should have exactly 1 DM record
    for subj in g.get_subjects():
        dm = g.records_by_subject[subj].get(Domain.DM, [])
        ok(f"{subj} has 1 DM", len(dm) == 1, f"has {len(dm)}")
        break  # spot-check first subject
    # overall: total DM records == number of subjects
    dm_count = len(g.records_by_domain.get(Domain.DM, []))
    subj_count = len(g.get_subjects())
    ok("DM records == subject count", dm_count == subj_count,
       f"DM={dm_count}, subj={subj_count}")


# ── Test 7: missing / malformed fields ──────────────────────────────

def test_malformed():
    print("\n--- Test 7: Missing / malformed fields ---")
    g = _build_graph()
    # special values should NOT become 0
    lb_recs = g.records_by_domain.get(Domain.LB, [])
    for r in lb_recs:
        raw = r.data.get("LBORRES", "")
        if raw in ("<5", "ND", ""):
            ok(f"special '{raw}' not parsed as 0",
               r.parsed.get("lborres_numeric") is None,
               f"got {r.parsed.get('lborres_numeric')}")
            break
    # parse_date on bad string returns None
    from atlas.data_loader import parse_date
    ok("bad date -> None", parse_date("NOTADATE") is None)
    ok("empty date -> None", parse_date("") is None)
    ok("None date -> None", parse_date(None) is None)


# ── Test 8: build(cut=...) ──────────────────────────────────────────

def test_build_cut():
    print("\n--- Test 8: build(cut=...) ---")
    g1 = _build_graph(cut=1)
    g12 = _build_graph(cut=12)
    gfull = _build_graph()

    n1 = sum(len(r) for r in g1.records_by_domain.values())
    n12 = sum(len(r) for r in g12.records_by_domain.values())
    nfull = sum(len(r) for r in gfull.records_by_domain.values())

    ok("cut=1 < cut=12", n1 < n12, f"cut1={n1}, cut12={n12}")
    ok("cut=12 <= full", n12 <= nfull, f"cut12={n12}, full={nfull}")

    # protocol version at cut 1 should be 1
    pv1 = g1.get_protocol_version()
    ok("protocol v1 at cut 1", pv1 == 1, f"got {pv1}")

    pv9 = _build_graph(cut=9).get_protocol_version()
    ok("protocol v3 at cut 9", pv9 == 3, f"got {pv9}")


# ── Test 9: rebuild after data changes ──────────────────────────────

def test_rebuild():
    print("\n--- Test 9: Rebuild after source data changes ---")
    g = _build_graph()
    n_before = sum(len(r) for r in g.records_by_domain.values())

    # Rebuild at a different cut
    g.build(cut=3)
    n_after = sum(len(r) for r in g.records_by_domain.values())

    ok("rebuild changes record count", n_before != n_after,
       f"before={n_before}, after={n_after}")
    ok("rebuild sets _build_cut", g._build_cut == 3)
    ok("rebuild clears old subjects_list",
       len(g._subjects_list) <= len(_build_graph().get_subjects()))


# ── Test 10: fast Patient 360 retrieval ─────────────────────────────

def test_fast_patient360():
    print("\n--- Test 10: Fast Patient 360 retrieval ---")
    g = _build_graph()
    subjects = g.get_subjects()
    # warm up
    g.patient360(subjects[0])

    t0 = time.time()
    for s in subjects:
        g.patient360(s)
    elapsed = time.time() - t0

    avg_ms = (elapsed / len(subjects)) * 1000
    ok(f"P360 avg < 5 ms (got {avg_ms:.2f} ms)", avg_ms < 5)
    ok(f"all {len(subjects)} P360s in < 2 s (got {elapsed:.2f} s)", elapsed < 2)


# ── Test 11 (bonus): corrections applied ────────────────────────────

def test_corrections():
    print("\n--- Test 11: Corrections applied ---")
    g = _build_graph()  # full, includes cut=5 corrections
    if not g.corrections:
        ok("no corrections file", True)
        return

    applied = 0
    for (c_cut, c_domain, c_usubjid, c_seq, c_field), new_val in g.corrections.items():
        if c_cut <= (g._build_cut or 999):
            rec = g.get_record(f"{c_domain}|{c_usubjid}|{c_seq}")
            if rec and rec.data.get(c_field) == new_val:
                applied += 1

    total = sum(1 for (c, *_) in g.corrections if c <= (g._build_cut or 999))
    ok(f"corrections applied ({applied}/{total})", applied == total,
       f"applied={applied}, expected={total}")


# ── Test 12 (bonus): unit conversion S07 ────────────────────────────

def test_s07_conversion():
    print("\n--- Test 12: S07 unit conversion ---")
    g = _build_graph()
    s07_subjs = g.get_subjects_at_site("S07")
    if not s07_subjs:
        ok("no S07 subjects", True)
        return

    subj = sorted(s07_subjs)[0]
    lb = g.records_by_subject[subj].get(Domain.LB, [])
    alts = [r for r in lb if r.parsed.get("lbtestcd") == "ALT"
            and r.parsed.get("lborres_numeric") is not None
            and r.parsed.get("lborres_std") is not None]
    if alts:
        r = alts[0]
        expected = r.parsed["lborres_numeric"] * 60
        ok(f"S07 ALT std = raw*60",
           abs(r.parsed["lborres_std"] - expected) < 0.01,
           f"std={r.parsed['lborres_std']}, expected={expected}")
    else:
        ok("no S07 ALT records", True)


# ── Test 13 (bonus): GraphStats ─────────────────────────────────────

def test_stats():
    print("\n--- Test 13: GraphStats ---")
    g = _build_graph()
    s = g.get_stats()
    ok("stats type", isinstance(s, GraphStats))
    ok("nodes > 0", s.nodes > 0, f"nodes={s.nodes}")
    ok("edges > 0", s.edges > 0, f"edges={s.edges}")
    ok("subjects > 0", s.subjects > 0, f"subjects={s.subjects}")
    ok("records_by_domain dict", isinstance(s.records_by_domain, dict))
    ok("LB in records_by_domain", "LB" in s.records_by_domain)
    ok("cuts > 0", s.cuts > 0, f"cuts={s.cuts}")


# ── Test 14 (bonus): query API ──────────────────────────────────────

def test_query():
    print("\n--- Test 14: query() API ---")
    g = _build_graph()
    subj = g.get_subjects()[0]

    # query by subject
    dm = g.query(Domain.DM, usubjid=subj)
    ok("query DM by subject", len(dm) == 1)

    # query with filter
    alt_recs = g.query(Domain.LB, usubjid=subj, filters={"lbtestcd": "ALT"})
    for r in alt_recs:
        ok("filter lbtestcd=ALT", r.parsed["lbtestcd"] == "ALT")
        break

    # query whole domain
    all_ae = g.query(Domain.AE)
    ok("query all AE", len(all_ae) == len(g.records_by_domain.get(Domain.AE, [])))


# ── main ─────────────────────────────────────────────────────────────

def main():
    global DATA_DIR
    if len(sys.argv) < 2:
        # default path
        DATA_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "hackathon-data", "hackathon-data"
        )
    else:
        DATA_DIR = sys.argv[1]

    if not os.path.isdir(DATA_DIR):
        print(f"ERROR: data directory not found: {DATA_DIR}")
        return 1

    print(f"Data dir: {DATA_DIR}")
    print("=" * 60)

    test_single_subject()
    test_multiple_subjects()
    test_patient360_completeness()
    test_cross_table()
    test_duplicate_records()
    test_duplicate_subjects()
    test_malformed()
    test_build_cut()
    test_rebuild()
    test_fast_patient360()
    test_corrections()
    test_s07_conversion()
    test_stats()
    test_query()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
