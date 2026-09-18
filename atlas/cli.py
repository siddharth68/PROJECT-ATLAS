#!/usr/bin/env python3
"""
CLI entry point for ATLAS.
"""
import sys
import json
import os
from atlas import StudyGraph, Atlas, Domain


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m atlas <data_dir> [command] [args...]")
        print("Commands:")
        print("  build [cut]           - Build study graph (optionally at specific cut)")
        print("  stats                 - Print graph statistics")
        print("  patient360 <usubjid>  - Get Patient360 for subject")
        print("  answer <question>     - Answer a question")
        print("  test                  - Run basic tests")
        return 1
    
    data_dir = sys.argv[1]
    command = sys.argv[2] if len(sys.argv) > 2 else 'build'
    
    graph = StudyGraph(data_dir)
    
    if command == 'build':
        cut = int(sys.argv[3]) if len(sys.argv) > 3 else None
        graph.build(cut)
        print(f"Built study graph at cut {cut or 'latest'}")
        stats = graph.get_stats()
        print(f"  Subjects: {stats.subjects}")
        print(f"  Records: {stats.nodes}")
        print(f"  By domain: {stats.records_by_domain}")
        return 0
    
    elif command == 'stats':
        if not graph._built:
            graph.build()
        stats = graph.get_stats()
        print(json.dumps({
            'nodes': stats.nodes,
            'edges': stats.edges,
            'subjects': stats.subjects,
            'records_by_domain': stats.records_by_domain,
            'cuts': stats.cuts
        }, indent=2))
        return 0
    
    elif command == 'patient360':
        if len(sys.argv) < 4:
            print("Usage: python -m atlas <data_dir> patient360 <usubjid>")
            return 1
        usubjid = sys.argv[3]
        if not graph._built:
            graph.build()
        p360 = graph.patient360(usubjid)
        print(json.dumps({
            'usubjid': p360.usubjid,
            'dm': p360.dm,
            'ae_count': len(p360.ae),
            'lb_count': len(p360.lb),
            'vs_count': len(p360.vs),
            'ex_count': len(p360.ex),
            'cm_count': len(p360.cm),
            'ds': p360.ds,
            'mh': p360.mh,
            'eg_count': len(p360.eg)
        }, indent=2, default=str))
        return 0
    
    elif command == 'answer':
        if len(sys.argv) < 4:
            print("Usage: python -m atlas <data_dir> answer <question>")
            return 1
        question = ' '.join(sys.argv[3:])
        if not graph._built:
            graph.build()
        atlas = Atlas(graph)
        answer = atlas.answer(question)
        print(json.dumps(answer.to_dict(), indent=2, default=str))
        return 0
    
    elif command == 'test':
        return run_tests(graph, data_dir)
    
    else:
        print(f"Unknown command: {command}")
        return 1


def run_tests(graph: StudyGraph, data_dir: str) -> int:
    """Run basic tests."""
    print("Running tests...")
    graph.build()
    
    # Test 1: Graph stats
    stats = graph.get_stats()
    assert stats.subjects > 0, "Should have subjects"
    assert stats.nodes > 0, "Should have records"
    print(f"OK Graph stats: {stats.subjects} subjects, {stats.nodes} records")
    
    # Test 2: Patient360
    first_subj = list(graph.records_by_subject.keys())[0]
    p360 = graph.patient360(first_subj)
    assert p360.usubjid == first_subj
    assert 'arm' in p360.dm
    print(f"OK Patient360 for {first_subj}")
    
    # Test 3: Hy's law candidates
    from atlas.reasoning import find_hys_law_candidates
    candidates = find_hys_law_candidates(graph)
    print(f"OK Hy's law candidates: {len(candidates)} found")
    
    # Test 4: Atlas answers
    atlas = Atlas(graph)
    
    # Count questions
    ans = atlas.answer("How many subjects in the study?")
    assert isinstance(ans.answer, int) and ans.answer > 0
    print(f"OK Count subjects: {ans.answer}")
    
    ans = atlas.answer("How many subjects in drug arm?")
    assert isinstance(ans.answer, int) and ans.answer > 0
    print(f"OK Count drug arm: {ans.answer}")
    
    ans = atlas.answer("How many sites?")
    assert isinstance(ans.answer, int) and ans.answer > 0
    print(f"OK Count sites: {ans.answer}")
    
    # Hy's law questions
    ans = atlas.answer("How many Hy's law candidates?")
    assert isinstance(ans.answer, int)
    print(f"OK Hy's law count: {ans.answer}")
    
    ans = atlas.answer("List Hy's law candidates")
    assert isinstance(ans.answer, list)
    print(f"OK Hy's law list: {len(ans.answer)} candidates")
    
    # Prohibited meds
    ans = atlas.answer("Find prohibited medications")
    assert isinstance(ans.answer, list)
    print(f"OK Prohibited meds: {len(ans.answer)} found")
    
    # Dosing errors
    ans = atlas.answer("Find dosing errors")
    assert isinstance(ans.answer, list)
    print(f"OK Dosing errors: {len(ans.answer)} found")
    
    # Visit deviations
    ans = atlas.answer("Find visit window deviations")
    assert isinstance(ans.answer, list)
    print(f"OK Visit deviations: {len(ans.answer)} found")
    
    # SAEs
    ans = atlas.answer("Find serious adverse events")
    assert isinstance(ans.answer, list)
    print(f"OK SAEs: {len(ans.answer)} found")
    
    # Discontinuations
    ans = atlas.answer("Find discontinued subjects")
    assert isinstance(ans.answer, list)
    print(f"OK Discontinuations: {len(ans.answer)} found")
    
    # Patient360
    ans = atlas.answer(f"Show patient profile for {first_subj}")
    assert ans.answer.get('found') is True
    print(f"OK Patient360 for {first_subj}")
    
    # Test 5: Evidence validation
    for ans in [
        atlas.answer("How many subjects?"),
        atlas.answer("List Hy's law candidates"),
        atlas.answer(f"Show patient profile for {first_subj}")
    ]:
        from atlas.schemas import validate_answer
        assert validate_answer(ans), "Answer should be valid"
        for ref in ans.evidence:
            # Verify ref exists in graph
            domain_records = graph.records_by_domain.get(ref.domain, [])
            found = any(r.ref == ref for r in domain_records)
            assert found, f"Evidence ref {ref} not found in graph"
    print("OK Evidence validation passed")
    
    # Test 6: Rebuild at different cut
    graph.build(cut=1)
    stats1 = graph.get_stats()
    graph.build(cut=12)
    stats12 = graph.get_stats()
    assert stats12.nodes >= stats1.nodes, "Later cut should have more records"
    print(f"OK Rebuild at different cuts: cut=1 ({stats1.nodes} records), cut=12 ({stats12.nodes} records)")
    
    # Test 7: Unit conversion (S07 µkat/L to U/L)
    s07_subjects = [s for s in graph.records_by_subject if s.startswith('042-S07-')]
    if s07_subjects:
        lb_records = graph.records_by_subject[s07_subjects[0]].get(Domain.LB, [])
        alt_recs = [r for r in lb_records if r.parsed.get('lbtestcd') == 'ALT' and r.parsed.get('lborres_std') is not None]
        if alt_recs:
            # Should be converted from µkat/L to U/L (x60)
            assert alt_recs[0].parsed['lborres_std'] == alt_recs[0].parsed['lborres_numeric'] * 60
            print(f"OK Unit conversion for S07: {alt_recs[0].parsed['lborres_numeric']} µkat/L -> {alt_recs[0].parsed['lborres_std']} U/L")
    
    # Test 8: Special value handling (<5, ND, empty)
    lb_records = graph.records_by_domain.get(Domain.LB, [])
    special_vals = [r for r in lb_records if r.data.get('LBORRES') in ('<5', 'ND', '')]
    for r in special_vals:
        assert r.parsed.get('lborres_numeric') is None, f"Special value {r.data.get('LBORRES')} should parse as None"
    print(f"OK Special value handling: {len(special_vals)} records with <5/ND/empty")
    
    # Test 9: Corrections applied
    if graph.corrections:
        # Check that corrected records have new values
        corrected_count = 0
        for domain, records in graph.records_by_domain.items():
            for r in records:
                for (c_cut, c_domain, c_usubjid, c_seq, c_field), new_val in graph.corrections.items():
                    if c_cut <= (graph._build_cut or 999) and c_domain == domain.value and c_usubjid == r.ref.usubjid and c_seq == r.ref.seq:
                        if r.data.get(c_field) == new_val:
                            corrected_count += 1
        print(f"OK Corrections applied: {corrected_count} records corrected")
    
    print("\nAll tests passed!")
    return 0


if __name__ == '__main__':
    sys.exit(main())