#!/usr/bin/env python3
"""
Comprehensive test suite for Member 3:
Reasoning + Question Answering + Evidence Validation.

Covers all 14 required areas:
 1. COUNT question
 2. LOOKUP question
 3. FINDING question
 4. TRAP question
 5. Hy's law calculation
 6. Unit conversion during reasoning
 7. Date-window filtering
 8. Unknown laboratory values (<5, ND, blank)
 9. Invalid/missing evidence
10. Empty-result handling
11. Duplicate records
12. Malformed/incomplete records
13. Evidence actually supports the claim
14. Multiple subjects satisfying the same finding
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, date, timedelta

# Ensure parent directory on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atlas.schemas import Domain, RecordRef, Answer, validate_answer
from atlas.graph import StudyGraph, NormalizedRecord
from atlas.evidence import EvidenceValidator, validate_evidence
from atlas.reasoning import (
    DateUtils,
    find_hys_law_candidates,
    check_hys_law_exclusions,
    check_hys_law_exclusions_detail,
    get_monitor_decision,
    find_prohibited_meds,
    find_dosing_errors,
    find_visit_window_deviations,
    find_saes,
    find_discontinued_subjects,
    find_lab_threshold,
    count_subjects_by_arm,
    count_sites,
)
from atlas.atlas import Atlas


# ---------------------------------------------------------------------------
# Test Runner Helpers
# ---------------------------------------------------------------------------

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


def run_all_tests(data_dir: str):
    print("=" * 60)
    print(f"Running Member 3 Reasoning & Evidence Tests on {data_dir}")
    print("=" * 60)

    graph = StudyGraph(data_dir)
    graph.build()
    atlas = Atlas(graph)
    validator = EvidenceValidator(graph)

    # -----------------------------------------------------------------------
    # 1. COUNT question
    # -----------------------------------------------------------------------
    print("\n--- 1. COUNT Questions ---")
    ans_total = atlas.answer("How many subjects in the study?")
    ok("count total subjects is integer", isinstance(ans_total.answer, int) and ans_total.answer == 241)
    ok("count total has valid schema", validate_answer(ans_total))
    ok("count total evidence length matches count", len(ans_total.evidence) == 241)
    ok("count total evidence are all DM", all(r.domain == Domain.DM for r in ans_total.evidence))

    ans_drug = atlas.answer("How many subjects in drug arm?")
    ok("count drug arm is integer", isinstance(ans_drug.answer, int) and ans_drug.answer == 128)
    ok("count drug arm evidence length matches count", len(ans_drug.evidence) == 128)
    # Verify all cited drug arm evidence records actually have ARM == 'DRUG'
    drug_evidence_correct = all(
        graph.get_record(str(r)).parsed.get("arm") == "DRUG" for r in ans_drug.evidence
    )
    ok("count drug arm evidence actually belong to DRUG arm", drug_evidence_correct)

    ans_placebo = atlas.answer("How many subjects in placebo arm?")
    ok("count placebo arm is integer", isinstance(ans_placebo.answer, int) and ans_placebo.answer == 113)
    ok("sum of drug + placebo equals total", ans_drug.answer + ans_placebo.answer == ans_total.answer)

    ans_zero = atlas.answer("How many subjects had cardiac arrest?")
    ok("zero count question returns 0", ans_zero.answer == 0)
    ok("zero count question has empty evidence", len(ans_zero.evidence) == 0)

    # -----------------------------------------------------------------------
    # 2. LOOKUP question
    # -----------------------------------------------------------------------
    print("\n--- 2. LOOKUP Questions ---")
    first_subj = graph.get_subjects()[0]
    ans_lookup = atlas.answer(f"What was subject {first_subj} ALT at Week 4?")
    ok("lookup answer returned", isinstance(ans_lookup.answer, list) and len(ans_lookup.answer) > 0)
    ok("lookup schema valid", validate_answer(ans_lookup))
    ok("lookup evidence cited", len(ans_lookup.evidence) > 0)
    ok("lookup evidence is LB domain", ans_lookup.evidence[0].domain == Domain.LB)
    ok("lookup evidence matches subject", ans_lookup.evidence[0].usubjid == first_subj)

    rec = graph.get_record(str(ans_lookup.evidence[0]))
    ok("lookup record exists in graph", rec is not None)
    ok("lookup record test is ALT", rec.parsed.get("lbtestcd") == "ALT")
    ok("lookup record visit is WEEK4", (rec.parsed.get("visit") or "").upper() == "WEEK4")

    # Lookup for non-existent subject
    ans_lookup_fake = atlas.answer("What was subject 999-S99-999 ALT at Week 4?")
    ok("lookup non-existent subject produces empty answer", ans_lookup_fake.answer == [])
    ok("lookup non-existent subject produces empty evidence", len(ans_lookup_fake.evidence) == 0)

    # -----------------------------------------------------------------------
    # 3. FINDING question
    # -----------------------------------------------------------------------
    print("\n--- 3. FINDING Questions ---")
    ans_dosing = atlas.answer("Which subjects had dosing errors?")
    ok("dosing error finding is list", isinstance(ans_dosing.answer, list))
    ok("dosing error schema valid", validate_answer(ans_dosing))
    ok("dosing errors evidence length matches findings", len(ans_dosing.evidence) == len(ans_dosing.answer))
    ok("dosing error evidence all EX", all(r.domain == Domain.EX for r in ans_dosing.evidence))
    # Verify each dosing error EX record actually has a dose violation
    all_dosing_violations = True
    for ref in ans_dosing.evidence:
        ex_rec = graph.get_record(str(ref))
        dm_rec = graph.records_by_subject[ref.usubjid][Domain.DM][0]
        arm = dm_rec.parsed.get("arm")
        dose = ex_rec.parsed.get("exdose")
        if arm == "DRUG" and dose == 10:
            all_dosing_violations = False
        if arm == "PLACEBO" and dose == 0:
            all_dosing_violations = False
    ok("every cited dosing error evidence record has an actual dose violation", all_dosing_violations)

    ans_sae = atlas.answer("Find serious adverse events")
    ok("SAE finding is list", isinstance(ans_sae.answer, list))
    ok("SAE evidence all AE domain", all(r.domain == Domain.AE for r in ans_sae.evidence))
    # Verify each SAE record is actually serious (AESER=Y or AESHOSP=Y)
    all_saes_serious = all(
        graph.get_record(str(r)).parsed.get("is_serious") for r in ans_sae.evidence
    )
    ok("every cited SAE evidence record is verified serious", all_saes_serious)

    # -----------------------------------------------------------------------
    # 4. TRAP question
    # -----------------------------------------------------------------------
    print("\n--- 4. TRAP Questions ---")
    trap_queries = [
        "Find subjects who developed end-stage renal disease",
        "Which patients received DRUG-042 50mg?",
        "Find Hy's law cases with cholestasis",
        "List subjects enrolled at Site 99",
        "Which patients died from acute pancreatitis?",
    ]
    for tq in trap_queries:
        ans_trap = atlas.answer(tq)
        ok(f"trap '{tq[:35]}...' returns empty answer []", ans_trap.answer == [])
        ok(f"trap '{tq[:35]}...' returns empty evidence []", len(ans_trap.evidence) == 0)
        ok(f"trap '{tq[:35]}...' meta marks is_trap or has explanation", (
            ans_trap.meta.get("is_trap") is True or "explanation" in ans_trap.meta
        ))
        ok(f"trap '{tq[:35]}...' schema valid", validate_answer(ans_trap))

    # -----------------------------------------------------------------------
    # 5. Hy's Law calculation
    # -----------------------------------------------------------------------
    print("\n--- 5. Hy's Law Calculation ---")
    # Test candidates without blind site exclusion (S03/S07 prompt injection trap)
    all_candidates = find_hys_law_candidates(graph, apply_exclusions=False)
    candidate_subjs = {c["usubjid"] for c in all_candidates}
    ok("finds candidates including S07 (avoiding prompt injection trap)", any("S07" in s for s in candidate_subjs))
    ok("candidate count before exclusions is 3", len(all_candidates) == 3)

    # Inspect the 3 candidates:
    # 1. 042-S07-001: ALT/AST elevation, but concomitant SULFONYLUREA (Glibenclamide)
    # 2. 042-S08-014: ALT/AST elevation, but concomitant SULFONYLUREA (Glibenclamide)
    # 3. 042-S05-003: ALT/AST elevation, NO baseline elevation, NO hepatotoxic med
    c_s05 = [c for c in all_candidates if c["usubjid"] == "042-S05-003"][0]
    ok("042-S05-003 is not excluded", c_s05["is_excluded"] is False)
    ok("042-S05-003 trans ratio > 3xULN", c_s05["pairs"][0]["trans_ratio"] > 3.0)
    ok("042-S05-003 bili ratio > 2xULN", c_s05["pairs"][0]["bili_ratio"] > 2.0)
    ok("042-S05-003 within 14 days", c_s05["pairs"][0]["diff_days"] <= 14)

    # Valid candidates after exclusions
    filtered_candidates = find_hys_law_candidates(graph, apply_exclusions=True)
    ok("Hy's law candidate count after exclusions is exactly 1", len(filtered_candidates) == 1)
    ok("Filtered Hy's law candidate is 042-S05-003", filtered_candidates[0]["usubjid"] == "042-S05-003")

    # Check medical monitor decision resolution
    status_s05, reason_s05 = get_monitor_decision(graph, "042-S05-003")
    ok("042-S05-003 monitor decision is APPROVED", status_s05 == "APPROVED")

    # Hy's law QA answering
    ans_hys = atlas.answer("Find Hy's law candidates")
    ok("Hy's law answer has 1 candidate after exclusions", len(ans_hys.answer) == 1)
    ok("Hy's law answer evidence contains exact qualifying ALT/AST and BILI records", len(ans_hys.evidence) == 2)
    ok("Hy's law evidence domain is LB", all(r.domain == Domain.LB for r in ans_hys.evidence))

    # -----------------------------------------------------------------------
    # 6. Unit conversion during reasoning
    # -----------------------------------------------------------------------
    print("\n--- 6. Unit Conversion During Reasoning ---")
    s07_subjs = [s for s in graph.get_subjects() if "S07" in s]
    ok("S07 subjects exist in study", len(s07_subjs) > 0)
    s07_cand = [c for c in all_candidates if "S07" in c["usubjid"]][0]
    # S07 reports ALT in ukat/L, converted by x60
    t_rec = s07_cand["trans_rec"]
    raw_val = float(t_rec.data["LBORRES"])
    std_val = t_rec.parsed["lborres_std"]
    ok("S07 raw unit is ukat/L", t_rec.data.get("LBORRESU") == "ukat/L")
    ok("S07 std value is raw * 60", abs(std_val - raw_val * 60) < 1e-5)
    # Check threshold evaluation against converted ULN
    std_uln = t_rec.parsed["ref_high_std"]
    ok("S07 std ULN is converted (0.93 * 60 = 55.8)", abs(std_uln - 55.8) < 1e-4)
    ok("S07 standardized comparison evaluates correctly", std_val > 3.0 * std_uln)

    # -----------------------------------------------------------------------
    # 7. Date-window filtering
    # -----------------------------------------------------------------------
    print("\n--- 7. Date-Window Filtering ---")
    # Test same-day records
    d_same1 = datetime(2026, 4, 13)
    d_same2 = datetime(2026, 4, 13)
    ok("same day is within 14 days", DateUtils.is_within_window(d_same1, d_same2, 14))
    ok("same day diff is 0 days", DateUtils.days_diff(d_same1, d_same2) == 0)

    # Boundary at day 14
    d_day14 = d_same1 + timedelta(days=14)
    ok("day 14 boundary is within 14-day window", DateUtils.is_within_window(d_same1, d_day14, 14))

    # Boundary at day 15 (outside window)
    d_day15 = d_same1 + timedelta(days=15)
    ok("day 15 is outside 14-day window", not DateUtils.is_within_window(d_same1, d_day15, 14))

    # Different date formats handling
    ok("DateUtils handles date object", DateUtils.to_datetime(date(2026, 1, 1)) == datetime(2026, 1, 1))
    ok("DateUtils handles ISO string", DateUtils.to_datetime("2026-04-13") == datetime(2026, 4, 13))
    ok("DateUtils handles None gracefully", DateUtils.to_datetime(None) is None)

    # Visit window deviations (v1=±7d, v2/v3=±3d)
    devs_v1 = find_visit_window_deviations(graph, protocol_version=1)
    devs_v3 = find_visit_window_deviations(graph, protocol_version=3)
    ok("stricter window (v3: ±3d) yields more or equal deviations than v1 (±7d)", len(devs_v3) >= len(devs_v1))

    # -----------------------------------------------------------------------
    # 8. Unknown laboratory values (<5, ND, blank, comma decimal)
    # -----------------------------------------------------------------------
    print("\n--- 8. Unknown Laboratory Values ---")
    lb_records = graph.records_by_domain[Domain.LB]
    special_below5 = [r for r in lb_records if r.data.get("LBORRES") == "<5"]
    special_nd = [r for r in lb_records if r.data.get("LBORRES") == "ND"]
    special_empty = [r for r in lb_records if not r.data.get("LBORRES")]

    ok("'<5' records exist in dataset", len(special_below5) > 0)
    ok("'<5' is NOT parsed as 0 or numeric", all(r.parsed.get("lborres_numeric") is None for r in special_below5))
    ok("'<5' std value is None", all(r.parsed.get("lborres_std") is None for r in special_below5))

    ok("'ND' records exist in dataset", len(special_nd) > 0)
    ok("'ND' is NOT parsed as numeric", all(r.parsed.get("lborres_numeric") is None for r in special_nd))

    # Comma decimal handling
    comma_recs = [r for r in lb_records if "," in (r.data.get("LBORRES") or "")]
    ok("comma decimal records exist (e.g. '12,4')", len(comma_recs) > 0)
    ok("comma decimals correctly converted to float", all(
        isinstance(r.parsed.get("lborres_numeric"), float) for r in comma_recs
    ))

    # Ensure find_lab_threshold never matches on <5 or None
    thresh_results = find_lab_threshold(graph, "BILI", threshold=0.0, comparison="<=")
    # No numeric value should be <= 0 in this dataset, and <5 should not be treated as 0
    ok("find_lab_threshold ignores non-numeric results", len(thresh_results) == 0)

    # -----------------------------------------------------------------------
    # 9. Invalid/missing evidence
    # -----------------------------------------------------------------------
    print("\n--- 9. Invalid / Missing Evidence Rejection ---")
    # Fabricated ref (does not exist in graph)
    fake_ref = RecordRef(Domain.LB, "042-S01-001", 99999)
    ok("validator detects fake ref does not exist", not validator.record_exists(fake_ref))
    ok("validator rejects fake ref", not validator.validate_ref(fake_ref))

    # Fabricated subject
    fake_subj_ref = RecordRef(Domain.DM, "999-S99-999", 1)
    ok("validator detects fake subject ref does not exist", not validator.record_exists(fake_subj_ref))

    # Filtering mixed list with fake refs
    real_ref = graph.records_by_domain[Domain.DM][0].ref
    sanitized = validator.filter_valid_evidence([real_ref, fake_ref, fake_subj_ref])
    ok("validator filters out non-existent refs", len(sanitized) == 1 and sanitized[0] == real_ref)

    # -----------------------------------------------------------------------
    # 10. Empty-result handling
    # -----------------------------------------------------------------------
    print("\n--- 10. Empty-Result Handling ---")
    ans_empty_count = atlas.answer("How many subjects enrolled at Site S99?")
    ok("empty count returns 0", ans_empty_count.answer == 0)
    ok("empty count evidence is empty list", ans_empty_count.evidence == [])
    ok("empty count validates against schema", validate_answer(ans_empty_count))

    ans_empty_find = atlas.answer("Find subjects with heart transplant")
    ok("empty finding returns []", ans_empty_find.answer == [])
    ok("empty finding evidence is empty list", ans_empty_find.evidence == [])
    ok("empty finding validates against schema", validate_answer(ans_empty_find))

    # -----------------------------------------------------------------------
    # 11. Duplicate records handling
    # -----------------------------------------------------------------------
    print("\n--- 11. Duplicate Records Handling ---")
    dup_list = [real_ref, real_ref, real_ref]
    deduped = validator.filter_valid_evidence(dup_list)
    ok("validator deduplicates evidence references preserving single copy", len(deduped) == 1)

    # -----------------------------------------------------------------------
    # 12. Malformed / incomplete records handling
    # -----------------------------------------------------------------------
    print("\n--- 12. Malformed / Incomplete Records Handling ---")
    # Verify records with missing dates or non-numeric values do not crash reasoning
    try:
        find_hys_law_candidates(graph, usubjid="042-S01-001")
        find_visit_window_deviations(graph, usubjid="042-S01-001")
        find_lab_threshold(graph, "ALT", threshold=100.0)
        ok("reasoning runs without error on records with missing fields", True)
    except Exception as e:
        ok("reasoning runs without error on records with missing fields", False, str(e))

    # -----------------------------------------------------------------------
    # 13. Evidence actually supports the claim
    # -----------------------------------------------------------------------
    print("\n--- 13. Evidence Actually Supports the Claim ---")
    # Attempt to cite a DM record as evidence for Hy's Law
    dm_ref = graph.records_by_subject["042-S05-003"][Domain.DM][0].ref
    ok("validator rejects DM record for HYS_LAW claim", not validator.validate_claim_evidence("HYS_LAW", dm_ref))

    # Attempt to cite an unrelated LB record (e.g. GLUC) for HYS_LAW claim
    gluc_recs = [
        r for r in graph.records_by_subject["042-S05-003"][Domain.LB]
        if r.parsed.get("lbtestcd") == "GLUC"
    ]
    if gluc_recs:
        ok("validator rejects GLUC record for HYS_LAW claim", not validator.validate_claim_evidence("HYS_LAW", gluc_recs[0].ref))

    # True qualifying ALT record for HYS_LAW claim
    s05_alt_qual = [
        r for r in graph.records_by_subject["042-S05-003"][Domain.LB]
        if r.parsed.get("lbtestcd") == "ALT" and r.parsed.get("lborres_std", 0) > 3.0 * r.parsed.get("ref_high_std", 1)
    ][0]
    ok("validator accepts qualifying ALT record for HYS_LAW claim", validator.validate_claim_evidence("HYS_LAW", s05_alt_qual.ref))

    # Attempt to cite non-serious AE for SAE claim
    non_serious_aes = [
        r for r in graph.records_by_domain[Domain.AE]
        if not r.parsed.get("is_serious")
    ]
    if non_serious_aes:
        ok("validator rejects non-serious AE for SAE claim", not validator.validate_claim_evidence("SAE", non_serious_aes[0].ref))

    # -----------------------------------------------------------------------
    # 14. Multiple subjects satisfying the same finding
    # -----------------------------------------------------------------------
    print("\n--- 14. Multiple Subjects Satisfying the Same Finding ---")
    # Prohibited meds has 14 occurrences across multiple subjects
    meds = find_prohibited_meds(graph, protocol_version=3)
    unique_med_subjs = {m.ref.usubjid for m in meds}
    ok("multiple subjects satisfy prohibited medication rule", len(unique_med_subjs) > 1)
    ans_meds = atlas.answer("Identify subjects with prohibited medications")
    ok("QA engine returns all findings for multiple subjects", len(ans_meds.answer) == len(meds))
    ok("QA engine cites evidence for all findings", len(ans_meds.evidence) == len(meds))

    # Dosing errors across multiple subjects
    errors = find_dosing_errors(graph)
    unique_err_subjs = {e.ref.usubjid for e in errors}
    ok("multiple subjects have dosing errors", len(unique_err_subjs) > 1)
    ans_errs = atlas.answer("Find dosing errors")
    ok("QA engine returns all dosing error occurrences", len(ans_errs.answer) == len(errors))

    # -----------------------------------------------------------------------
    # Final Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print(f"Member 3 Test Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    data_directory = sys.argv[1] if len(sys.argv) > 1 else "hackathon-data/hackathon-data"
    sys.exit(run_all_tests(data_directory))
