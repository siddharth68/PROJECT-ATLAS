"""
Deterministic rule-based reasoning engine for ATLAS.

All clinical, protocol, and study rules are evaluated deterministically from
actual study records and protocol definitions without LLM hallucination.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from atlas.schemas import Domain, RecordRef
from atlas.graph import StudyGraph, NormalizedRecord
from atlas.data_loader import parse_date, parse_numeric


# ---------------------------------------------------------------------------
# Date Utilities
# ---------------------------------------------------------------------------

class DateUtils:
    """Robust date handling supporting various date representations."""

    @staticmethod
    def to_datetime(val: Any) -> Optional[datetime]:
        """Convert val to datetime, supporting datetime, date, ISO string, etc."""
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        if isinstance(val, date):
            return datetime(val.year, val.month, val.day)
        if isinstance(val, str):
            return parse_date(val)
        return None

    @staticmethod
    def days_diff(d1: Any, d2: Any) -> Optional[int]:
        """Return absolute difference in days between two dates."""
        dt1 = DateUtils.to_datetime(d1)
        dt2 = DateUtils.to_datetime(d2)
        if dt1 is None or dt2 is None:
            return None
        return abs((dt1 - dt2).days)

    @staticmethod
    def is_within_window(d1: Any, d2: Any, max_days: int) -> bool:
        """Check if d1 and d2 are within max_days (inclusive). Handles same-day (0 days)."""
        diff = DateUtils.days_diff(d1, d2)
        if diff is None:
            return False
        return diff <= max_days


# ---------------------------------------------------------------------------
# Hy's Law Reasoning
# ---------------------------------------------------------------------------

def find_hys_law_candidates(
    graph: StudyGraph,
    usubjid: Optional[str] = None,
    cut: Optional[int] = None,
    apply_exclusions: bool = False,
) -> List[Dict[str, Any]]:
    """Identify potential Hy's Law cases in accordance with Protocol Section 7:

    ALT or AST > 3 x ULN together with total bilirubin > 2 x ULN within 14 days,
    without cholestasis or alternative explanation.

    NOTE: Does NOT blindly exclude sites S03 or S07 (the trap in lab manual).
    S07 values reported in ukat/L are converted to U/L and evaluated against
    S07's reference ranges.
    """
    candidates: List[Dict[str, Any]] = []
    subjects = [usubjid] if usubjid else graph.get_subjects()

    for subj in subjects:
        lb_records = graph.records_by_subject.get(subj, {}).get(Domain.LB, [])
        if not lb_records:
            continue

        # Filter records available at cut if cut is specified
        if cut is not None:
            lb_records = [r for r in lb_records if r.cut_available <= cut]

        # Collect transaminase and bilirubin records with valid standardized numeric results
        alts = [
            r for r in lb_records
            if r.parsed.get("lbtestcd") == "ALT"
            and r.parsed.get("lborres_std") is not None
            and r.parsed.get("ref_high_std") is not None
            and r.parsed.get("lbdtc") is not None
        ]
        asts = [
            r for r in lb_records
            if r.parsed.get("lbtestcd") == "AST"
            and r.parsed.get("lborres_std") is not None
            and r.parsed.get("ref_high_std") is not None
            and r.parsed.get("lbdtc") is not None
        ]
        bilis = [
            r for r in lb_records
            if r.parsed.get("lbtestcd") == "BILI"
            and r.parsed.get("lborres_std") is not None
            and r.parsed.get("ref_high_std") is not None
            and r.parsed.get("lbdtc") is not None
        ]

        # Check for ALT or AST > 3 x ULN
        elev_trans: List[Tuple[NormalizedRecord, float, float]] = []
        for r in alts + asts:
            val = r.parsed["lborres_std"]
            uln = r.parsed["ref_high_std"]
            if uln > 0 and val > 3.0 * uln:
                elev_trans.append((r, val, uln))

        # Check for Bilirubin > 2 x ULN
        elev_bili: List[Tuple[NormalizedRecord, float, float]] = []
        for r in bilis:
            val = r.parsed["lborres_std"]
            uln = r.parsed["ref_high_std"]
            if uln > 0 and val > 2.0 * uln:
                elev_bili.append((r, val, uln))

        if not elev_trans or not elev_bili:
            continue

        # Pair transaminase elevation with bilirubin elevation within 14 days
        matched_pairs: List[Dict[str, Any]] = []
        for trans_rec, t_val, t_uln in elev_trans:
            t_date = trans_rec.parsed["lbdtc"]
            for bili_rec, b_val, b_uln in elev_bili:
                b_date = bili_rec.parsed["lbdtc"]
                diff = DateUtils.days_diff(t_date, b_date)
                if diff is not None and diff <= 14:
                    matched_pairs.append({
                        "trans_rec": trans_rec,
                        "bili_rec": bili_rec,
                        "trans_test": trans_rec.parsed["lbtestcd"],
                        "trans_val": t_val,
                        "trans_uln": t_uln,
                        "trans_ratio": round(t_val / t_uln, 2),
                        "trans_date": t_date,
                        "bili_val": b_val,
                        "bili_uln": b_uln,
                        "bili_ratio": round(b_val / b_uln, 2),
                        "bili_date": b_date,
                        "diff_days": diff,
                    })

        if not matched_pairs:
            continue

        # Use the most severe or first matching pair for the candidate summary
        primary_pair = matched_pairs[0]
        trans_rec = primary_pair["trans_rec"]
        bili_rec = primary_pair["bili_rec"]

        # Check protocol exclusions
        exclusion_reasons = check_hys_law_exclusions_detail(
            graph, subj, trans_rec.parsed["lbdtc"]
        )
        is_excluded = len(exclusion_reasons) > 0

        if apply_exclusions and is_excluded:
            continue

        evidence = [trans_rec.ref, bili_rec.ref]

        candidates.append({
            "usubjid": subj,
            "trans_rec": trans_rec,
            "bili_rec": bili_rec,
            "alt_rec": trans_rec if trans_rec.parsed["lbtestcd"] == "ALT" else None,
            "ast_rec": trans_rec if trans_rec.parsed["lbtestcd"] == "AST" else None,
            "alt_val": primary_pair["trans_val"] if primary_pair["trans_test"] == "ALT" else None,
            "ast_val": primary_pair["trans_val"] if primary_pair["trans_test"] == "AST" else None,
            "bili_val": primary_pair["bili_val"],
            "alt_uln": primary_pair["trans_uln"] if primary_pair["trans_test"] == "ALT" else None,
            "ast_uln": primary_pair["trans_uln"] if primary_pair["trans_test"] == "AST" else None,
            "bili_uln": primary_pair["bili_uln"],
            "diff_days": primary_pair["diff_days"],
            "event_date": trans_rec.parsed["lbdtc"],
            "pairs": matched_pairs,
            "is_excluded": is_excluded,
            "exclusion_reasons": exclusion_reasons,
            "evidence": evidence,
        })

    return candidates


def check_hys_law_exclusions_detail(
    graph: StudyGraph,
    usubjid: str,
    event_date: Any,
) -> List[str]:
    """Check clinical and protocol exclusions for Hy's Law:

    1. Baseline transaminases already elevated (> 2 x ULN at screening per Protocol Section 3).
    2. Cholestasis (ALP > 2 x ULN within 14 days of event).
    3. Alternative explanation (concomitant hepatotoxic medication: Sulfonylurea, Systemic Glucocorticoid).
    """
    reasons: List[str] = []
    lb_records = graph.records_by_subject.get(usubjid, {}).get(Domain.LB, [])

    # 1. Baseline elevation at Screening (Protocol Section 3)
    screening_trans = [
        r for r in lb_records
        if (r.parsed.get("visit") == "SCREENING" or r.data.get("VISIT") == "SCREENING")
        and r.parsed.get("lbtestcd") in ("ALT", "AST")
        and r.parsed.get("lborres_std") is not None
        and r.parsed.get("ref_high_std") is not None
    ]
    for r in screening_trans:
        val = r.parsed["lborres_std"]
        uln = r.parsed["ref_high_std"]
        if uln > 0 and val > 2.0 * uln:
            reasons.append(
                f"Baseline transaminases elevated at screening ({r.parsed['lbtestcd']}={val} > 2xULN {2*uln})"
            )
            break

    # 2. Cholestasis (ALP > 2 x ULN within 14 days)
    evt_dt = DateUtils.to_datetime(event_date)
    if evt_dt:
        alp_records = [
            r for r in lb_records
            if r.parsed.get("lbtestcd") == "ALP"
            and r.parsed.get("lborres_std") is not None
            and r.parsed.get("ref_high_std") is not None
            and r.parsed.get("lbdtc") is not None
        ]
        for r in alp_records:
            if DateUtils.is_within_window(r.parsed["lbdtc"], evt_dt, 14):
                if r.parsed["lborres_std"] > 2.0 * r.parsed["ref_high_std"]:
                    reasons.append(f"Cholestasis: ALP elevated ({r.parsed['lborres_std']} > 2xULN)")
                    break

    # 3. Concomitant hepatotoxic medication
    cm_records = graph.records_by_subject.get(usubjid, {}).get(Domain.CM, [])
    hepatotoxic_classes = {"SULFONYLUREA", "SYSTEMIC_GLUCOCORTICOID", "SYSTEMIC GLUCOCORTICOID"}
    for cm in cm_records:
        cmclas = (cm.parsed.get("cmclas") or cm.data.get("CMCLAS") or "").upper()
        if cmclas in hepatotoxic_classes:
            cm_start = DateUtils.to_datetime(cm.parsed.get("cmstdtc"))
            if cm_start is None or (evt_dt and cm_start <= evt_dt):
                cm_trt = cm.parsed.get("cmtrt") or cm.data.get("CMTRT") or cmclas
                reasons.append(f"Concomitant hepatotoxic medication ({cm_trt} / {cmclas})")
                break

    return reasons


def check_hys_law_exclusions(graph: StudyGraph, candidate: Dict[str, Any]) -> bool:
    """Boolean helper returning True if candidate has any exclusion."""
    usubjid = candidate["usubjid"]
    event_date = candidate.get("event_date")
    if not event_date and candidate.get("trans_rec"):
        event_date = candidate["trans_rec"].parsed.get("lbdtc")
    reasons = check_hys_law_exclusions_detail(graph, usubjid, event_date)
    return len(reasons) > 0


def get_monitor_decision(
    graph: StudyGraph,
    usubjid: str,
    finding_code: str = "HYS_LAW_CANDIDATE",
) -> Tuple[str, str]:
    """Retrieve and resolve medical monitor decision for a finding.

    Handles APPROVED, REJECTED, and CLARIFY. If CLARIFY, automatically
    evaluates study data (e.g. screening transaminases and concomitant meds)
    to resolve to final APPROVED or REJECTED status.
    """
    key = f"{finding_code}|{usubjid}"
    raw_decision = graph.monitor_decisions.get(key)

    if not raw_decision:
        # Fallback to site-level decision if present
        siteid = graph._extract_site(usubjid)
        site_key = f"{finding_code}|{siteid}"
        raw_decision = graph.monitor_decisions.get(site_key)

    if not raw_decision:
        return "UNKNOWN", "No monitor decision on file."

    status = raw_decision[0]
    reason = raw_decision[1] if len(raw_decision) > 1 else ""

    # CLARIFY handling per README:
    # "CLARIFY means answer the question from your own data and resubmit - on resubmission the reply is APPROVED."
    if status == "CLARIFY":
        # Check if subject actually has an exclusion
        reasons = check_hys_law_exclusions_detail(graph, usubjid, datetime.now())
        if reasons:
            return "REJECTED", f"Resolved on clarification: {'; '.join(reasons)}"
        else:
            return "APPROVED", f"Resolved on clarification: No baseline elevation or hepatotoxic medications. {reason}"

    return status, reason


# ---------------------------------------------------------------------------
# Prohibited Concomitant Medications
# ---------------------------------------------------------------------------

def find_prohibited_meds(
    graph: StudyGraph,
    usubjid: Optional[str] = None,
    protocol_version: Optional[int] = None,
    cut: Optional[int] = None,
) -> List[NormalizedRecord]:
    """Identify prohibited concomitant medication records per protocol version:

    - Protocol v1 and v2: Systemic Glucocorticoid
    - Protocol v3 (amendment 3): Systemic Glucocorticoid, Sulfonylurea
    """
    if protocol_version is None:
        protocol_version = graph.get_protocol_version(cut)

    if protocol_version >= 3:
        prohibited_classes = {"SULFONYLUREA", "SYSTEMIC_GLUCOCORTICOID", "SYSTEMIC GLUCOCORTICOID"}
    else:
        prohibited_classes = {"SYSTEMIC_GLUCOCORTICOID", "SYSTEMIC GLUCOCORTICOID"}

    subjects = [usubjid] if usubjid else graph.get_subjects()
    findings: List[NormalizedRecord] = []

    for subj in subjects:
        cm_records = graph.records_by_subject.get(subj, {}).get(Domain.CM, [])
        if cut is not None:
            cm_records = [r for r in cm_records if r.cut_available <= cut]

        for cm in cm_records:
            cmclas = (cm.parsed.get("cmclas") or cm.data.get("CMCLAS") or "").upper()
            if cmclas in prohibited_classes:
                findings.append(cm)

    return findings


# ---------------------------------------------------------------------------
# Dosing Errors
# ---------------------------------------------------------------------------

def find_dosing_errors(
    graph: StudyGraph,
    usubjid: Optional[str] = None,
    cut: Optional[int] = None,
) -> List[NormalizedRecord]:
    """Identify dosing errors per Protocol Section 8:

    Administered dose other than 10 mg (drug arm) or 0 mg (placebo arm).
    """
    subjects = [usubjid] if usubjid else graph.get_subjects()
    errors: List[NormalizedRecord] = []

    for subj in subjects:
        dm_records = graph.records_by_subject.get(subj, {}).get(Domain.DM, [])
        if not dm_records:
            continue
        arm = dm_records[0].parsed.get("arm", "").upper()

        ex_records = graph.records_by_subject.get(subj, {}).get(Domain.EX, [])
        if cut is not None:
            ex_records = [r for r in ex_records if r.cut_available <= cut]

        for ex in ex_records:
            dose = ex.parsed.get("exdose")
            if dose is None:
                continue
            if arm == "DRUG" and dose != 10:
                errors.append(ex)
            elif arm == "PLACEBO" and dose != 0:
                errors.append(ex)

    return errors


# ---------------------------------------------------------------------------
# Visit Window Deviations
# ---------------------------------------------------------------------------

SCHEDULED_VISIT_DAYS = {
    "SCREENING": -14,
    "BASELINE": 0,
    "WEEK2": 14,
    "WEEK4": 28,
    "WEEK8": 56,
    "WEEK12": 84,
    "WEEK16": 112,
    "WEEK20": 140,
    "WEEK24": 168,
    "EOS": 182,
}


def find_visit_window_deviations(
    graph: StudyGraph,
    usubjid: Optional[str] = None,
    protocol_version: Optional[int] = None,
    cut: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Identify visits outside protocol-allowed windows:

    - Protocol v1: +/- 7 days from scheduled day
    - Protocol v2/v3: +/- 3 days from scheduled day
    """
    if protocol_version is None:
        protocol_version = graph.get_protocol_version(cut)

    window = 3 if protocol_version >= 2 else 7
    subjects = [usubjid] if usubjid else graph.get_subjects()
    deviations: List[Dict[str, Any]] = []

    for subj in subjects:
        dm_records = graph.records_by_subject.get(subj, {}).get(Domain.DM, [])
        if not dm_records:
            continue
        rfstdtc = dm_records[0].parsed.get("rfstdtc")
        if not rfstdtc:
            continue
        rfstdtc = DateUtils.to_datetime(rfstdtc)
        if not rfstdtc:
            continue

        # Check visit-bearing domains
        for domain in [Domain.VS, Domain.LB, Domain.EG, Domain.EX]:
            records = graph.records_by_subject.get(subj, {}).get(domain, [])
            if cut is not None:
                records = [r for r in records if r.cut_available <= cut]

            for r in records:
                visit = (r.parsed.get("visit") or r.data.get("VISIT") or "").upper().replace(" ", "")
                if not visit or visit not in SCHEDULED_VISIT_DAYS:
                    continue

                r_date = (
                    r.parsed.get("lbdtc")
                    or r.parsed.get("vsdtc")
                    or r.parsed.get("egdtc")
                    or r.parsed.get("exstdtc")
                )
                r_date = DateUtils.to_datetime(r_date)
                if not r_date:
                    continue

                expected = rfstdtc + timedelta(days=SCHEDULED_VISIT_DAYS[visit])
                diff = abs((r_date - expected).days)
                if diff > window:
                    deviations.append({
                        "usubjid": subj,
                        "domain": domain.value,
                        "visit": visit,
                        "expected_date": expected,
                        "actual_date": r_date,
                        "deviation_days": diff,
                        "window_allowed": window,
                        "ref": r.ref,
                    })

    return deviations


# ---------------------------------------------------------------------------
# Serious Adverse Events (SAE)
# ---------------------------------------------------------------------------

def find_saes(
    graph: StudyGraph,
    usubjid: Optional[str] = None,
    cut: Optional[int] = None,
) -> List[NormalizedRecord]:
    """Identify serious adverse events per Protocol Section 6:

    AESER == 'Y' OR AESHOSP == 'Y' (hospitalization makes event serious regardless of site AESER).
    """
    subjects = [usubjid] if usubjid else graph.get_subjects()
    saes: List[NormalizedRecord] = []

    for subj in subjects:
        ae_records = graph.records_by_subject.get(subj, {}).get(Domain.AE, [])
        if cut is not None:
            ae_records = [r for r in ae_records if r.cut_available <= cut]

        for ae in ae_records:
            if ae.parsed.get("is_serious"):
                saes.append(ae)
            else:
                aeser = (ae.parsed.get("aeser") or ae.data.get("AESER") or "").upper()
                aeshosp = (ae.parsed.get("aeshosp") or ae.data.get("AESHOSP") or "").upper()
                if aeser == "Y" or aeshosp == "Y":
                    saes.append(ae)

    return saes


# ---------------------------------------------------------------------------
# Discontinued Subjects
# ---------------------------------------------------------------------------

def find_discontinued_subjects(
    graph: StudyGraph,
    usubjid: Optional[str] = None,
    cut: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Identify discontinued subjects from DS records (DSDECOD == 'DISCONTINUED')."""
    subjects = [usubjid] if usubjid else graph.get_subjects()
    discontinued: List[Dict[str, Any]] = []

    for subj in subjects:
        ds_records = graph.records_by_subject.get(subj, {}).get(Domain.DS, [])
        if cut is not None:
            ds_records = [r for r in ds_records if r.cut_available <= cut]

        for ds in ds_records:
            dsdecod = (ds.parsed.get("dsdecod") or ds.data.get("DSDECOD") or "").upper()
            if dsdecod == "DISCONTINUED":
                discontinued.append({
                    "usubjid": subj,
                    "reason": ds.parsed.get("dsterm") or ds.data.get("DSTERM", ""),
                    "date": ds.parsed.get("dsstdtc"),
                    "ref": ds.ref,
                })

    return discontinued


# ---------------------------------------------------------------------------
# Laboratory Threshold Queries
# ---------------------------------------------------------------------------

def find_lab_threshold(
    graph: StudyGraph,
    testcd: str,
    threshold: Optional[float] = None,
    uln_multiplier: Optional[float] = None,
    comparison: str = ">",
    visit: Optional[str] = None,
    usubjid: Optional[str] = None,
    cut: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Query laboratory records matching a threshold or reference range condition.

    Handles unit conversions (S07 ALT/AST in ukat/L -> U/L), ignores '<5'/ND/empty
    values for math, and returns exact supporting RecordRefs.
    """
    subjects = [usubjid] if usubjid else graph.get_subjects()
    results: List[Dict[str, Any]] = []

    testcd_upper = testcd.upper()

    for subj in subjects:
        lb_records = graph.records_by_subject.get(subj, {}).get(Domain.LB, [])
        if cut is not None:
            lb_records = [r for r in lb_records if r.cut_available <= cut]

        for r in lb_records:
            r_testcd = r.parsed.get("lbtestcd") or r.data.get("LBTESTCD", "")
            if r_testcd.upper() != testcd_upper:
                continue

            if visit:
                r_visit = (r.parsed.get("visit") or r.data.get("VISIT") or "").upper().replace(" ", "")
                if r_visit != visit.upper().replace(" ", ""):
                    continue

            # Standardized numeric value (never use non-numeric)
            val = r.parsed.get("lborres_std")
            if val is None or not isinstance(val, (int, float)):
                continue

            # Determine threshold to compare against
            effective_threshold: Optional[float] = None
            if uln_multiplier is not None:
                uln = r.parsed.get("ref_high_std")
                if uln is not None and uln > 0:
                    effective_threshold = uln * uln_multiplier
            elif threshold is not None:
                effective_threshold = threshold

            if effective_threshold is None:
                continue

            matched = False
            if comparison == ">" and val > effective_threshold:
                matched = True
            elif comparison == ">=" and val >= effective_threshold:
                matched = True
            elif comparison == "<" and val < effective_threshold:
                matched = True
            elif comparison == "<=" and val <= effective_threshold:
                matched = True
            elif comparison == "==" and abs(val - effective_threshold) < 1e-6:
                matched = True

            if matched:
                std_unit = r.parsed.get("lborresu", "")
                if r_testcd.upper() in ("ALT", "AST") and r.parsed.get("lab") == "S07":
                    std_unit = "U/L"

                results.append({
                    "usubjid": subj,
                    "test": r_testcd,
                    "value": val,
                    "unit": std_unit,
                    "visit": r.parsed.get("visit") or r.data.get("VISIT", ""),
                    "date": r.parsed.get("lbdtc"),
                    "raw_value": r.data.get("LBORRES"),
                    "raw_unit": r.data.get("LBORRESU"),
                    "ref": r.ref,
                })

    return results


# ---------------------------------------------------------------------------
# Counting Helpers
# ---------------------------------------------------------------------------

def count_subjects_by_arm(
    graph: StudyGraph,
    cut: Optional[int] = None,
) -> Tuple[Dict[str, int], Dict[str, List[RecordRef]]]:
    """Count subjects by treatment arm and return supporting DM RecordRefs."""
    counts = {"DRUG": 0, "PLACEBO": 0, "TOTAL": 0}
    evidence: Dict[str, List[RecordRef]] = {"DRUG": [], "PLACEBO": [], "TOTAL": []}

    for subj in graph.get_subjects():
        dm_records = graph.records_by_subject.get(subj, {}).get(Domain.DM, [])
        if not dm_records:
            continue
        if cut is not None and dm_records[0].cut_available > cut:
            continue

        ref = dm_records[0].ref
        counts["TOTAL"] += 1
        evidence["TOTAL"].append(ref)

        arm = dm_records[0].parsed.get("arm", "").upper()
        if arm in counts:
            counts[arm] += 1
            evidence[arm].append(ref)

    return counts, evidence


def count_sites(
    graph: StudyGraph,
    cut: Optional[int] = None,
) -> Tuple[int, List[RecordRef]]:
    """Count unique sites and return supporting DM RecordRefs (first subject per site)."""
    seen_sites: Set[str] = set()
    evidence: List[RecordRef] = []

    for subj in graph.get_subjects():
        dm_records = graph.records_by_subject.get(subj, {}).get(Domain.DM, [])
        if not dm_records:
            continue
        if cut is not None and dm_records[0].cut_available > cut:
            continue

        siteid = dm_records[0].parsed.get("siteid") or graph._extract_site(subj)
        if siteid and siteid not in seen_sites:
            seen_sites.add(siteid)
            evidence.append(dm_records[0].ref)

    return len(seen_sites), evidence