"""
Stage 1 ATLAS interface and CLI runner.
Exposes StudyGraph and Atlas classes.
"""
from __future__ import annotations

import sys
import argparse
from atlas.schemas import Domain, RecordRef, Answer, Patient360, GraphStats, validate_answer
from atlas.graph import StudyGraph, NormalizedRecord
from atlas.atlas import Atlas
from atlas.evidence import EvidenceValidator, validate_evidence
from atlas.cli import main as cli_main

__all__ = [
    'StudyGraph',
    'Atlas',
    'NormalizedRecord',
    'EvidenceValidator',
    'validate_evidence',
]


def main():
    parser = argparse.ArgumentParser(description="Stage 1 ATLAS")
    parser.add_argument("--data", default="hackathon-data", help="Path to study data directory")
    parser.add_argument("--cut", type=int, default=None, help="Data cut to build graph at")
    parser.add_argument("--question", default=None, help="Optional question to answer")
    args, unknown = parser.parse_known_args()

    data_dir = args.data
    # If nested directory exists (e.g. hackathon-data/hackathon-data), resolve it
    import os
    if os.path.exists(os.path.join(data_dir, "hackathon-data")):
        data_dir = os.path.join(data_dir, "hackathon-data")

    cut = args.cut
    if cut is None and args.question:
        import re
        m = re.search(r'\bcut\s*(?:=|is|at)?\s*(\d+)\b', args.question.lower())
        if m:
            cut = int(m.group(1))

    graph = StudyGraph(data_dir)
    graph.build(cut=cut)
    atlas = Atlas(graph)

    if args.question:
        ans = atlas.answer(args.question)
        import json
        print(json.dumps(ans.to_dict(), indent=2, default=str))
        return 0
    else:
        # Run standard test / stats
        stats = graph.get_stats()
        print(f"ATLAS Stage 1 Graph Built successfully:")
        print(f"  Subjects: {stats.subjects}")
        print(f"  Records:  {stats.nodes}")
        print(f"  Edges:    {stats.edges}")
        print(f"  Domains:  {stats.records_by_domain}")
        return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("build", "stats", "patient360", "answer", "test"):
        sys.exit(cli_main())
    else:
        sys.exit(main())
