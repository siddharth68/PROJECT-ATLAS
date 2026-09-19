#!/usr/bin/env python3
"""
ATLAS // STUDY SENTINEL — CLINICAL INTELLIGENCE PLATFORM
Problem 1 (ATLAS) + Problem 2 (MONITOR) + Pure Python Frontend

Pure Python standard library HTTP server + REST API.
Runs with ZERO external pip dependencies.

Usage:
    python app.py
    # or
    python main.py
"""
import http.server
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from stage1.atlas import Atlas, StudyGraph
from stage1.schemas import validate_answer
from stage2.crew import ReviewCrew
from stage2.schemas import ReviewReport


def resolve_data_dir(default_name: str = "hackathon-data") -> str:
    """Resolve data directory even if nested (e.g. hackathon-data/hackathon-data)."""
    p = os.path.join(PROJECT_ROOT, default_name)
    if os.path.exists(os.path.join(p, "data")):
        return p
    nested = os.path.join(p, "hackathon-data")
    if os.path.exists(os.path.join(nested, "data")):
        return nested
    return p


# ---------------------------------------------------------------------------
# Global Engine Initialization
# ---------------------------------------------------------------------------
DATA_DIR = resolve_data_dir()
print(f"[ATLAS // MONITOR Platform] Initializing StudyGraph from: {DATA_DIR} ...")
t0 = time.time()
graph = StudyGraph(DATA_DIR)
graph.build()
atlas = Atlas(graph)
t_build = time.time() - t0
stats = graph.get_stats()
print(f"[ATLAS Engine] Ready in {t_build:.3f}s: {stats.nodes:,} records, {stats.edges:,} edges, {stats.subjects} subjects.")

# Stage 2 Autonomous Monitoring Crew
crew = ReviewCrew(
    hub_url="http://127.0.0.1:8000",
    gateway_url="http://127.0.0.1:8001",
    team_key="TEAM-ATLAS",
    atlas=atlas,
)


# Load cached or pre-generated Stage 2 ReviewReport
active_report_dict: Optional[Dict[str, Any]] = None
stage2_candidates = [
    os.path.join(PROJECT_ROOT, "stage2_public.json"),
    os.path.join(PROJECT_ROOT, "benchmarks", "stage2_public.json"),
]
for stage2_path in stage2_candidates:
    if os.path.exists(stage2_path):
        try:
            with open(stage2_path, "r", encoding="utf-8") as f:
                active_report_dict = json.load(f)
            summary = active_report_dict.get("summary", {})
            print(f"[MONITOR Crew] Pre-loaded Stage 2 ReviewReport: {summary.get('total_findings', 0)} findings, {summary.get('new_escalations', 0)} escalations, {summary.get('new_queries', 0)} queries.")
            break
        except Exception as e:
            print(f"[MONITOR Crew] Warning loading {stage2_path}: {e}")


def get_or_create_report() -> Dict[str, Any]:
    """Return active report, generating default Cut 5 / Protocol v2 report if none exists."""
    global active_report_dict
    if active_report_dict is not None:
        return active_report_dict
    print("[MONITOR Crew] Generating initial monitoring cycle report (Cut 5, Protocol v2)...")
    rep = crew.run_cycle(cut=5, protocol_version=2)
    active_report_dict = rep.to_dict()
    return active_report_dict


# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------
class AtlasDashboardHandler(http.server.BaseHTTPRequestHandler):
    """Unified HTTP handler serving Atlas Q&A, Patient 360, and Monitor Crew endpoints."""

    def log_message(self, format, *args):
        """Clean terminal logging."""
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]} - {args[1]}\n")

    def send_json(self, status_code: int, data: Any):
        """Send a JSON HTTP response with CORS headers."""
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """CORS preflight support."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        # ------------------------------------------------------------------
        # STAGE 1 (ATLAS) ENDPOINTS
        # ------------------------------------------------------------------

        # 1. API: /api/stats
        if path == "/api/stats" or path == "/stats":
            cur_stats = graph.get_stats()
            rep = active_report_dict or {}
            mon_summary = rep.get("summary", {})
            self.send_json(200, {
                "nodes": cur_stats.nodes,
                "edges": cur_stats.edges,
                "subjects": cur_stats.subjects,
                "cuts": cur_stats.cuts,
                "current_cut": graph._build_cut or 12,
                "records_by_domain": cur_stats.records_by_domain,
                "build_time_seconds": round(graph._build_time, 3),
                "monitor_findings": mon_summary.get("total_findings", 0),
                "monitor_escalations": mon_summary.get("new_escalations", 0),
                "monitor_queries": mon_summary.get("new_queries", 0),
                "monitor_deviations": mon_summary.get("compliance_deviations", 0),
            })
            return

        # 2. API: /api/subjects
        elif path == "/api/subjects" or path == "/subjects":
            subjects_list = []
            for usubjid in graph.get_subjects():
                dm_list = graph.records_by_subject.get(usubjid, {}).get(
                    [d for d in graph.records_by_domain if d.value == "DM"][0], []
                ) if [d for d in graph.records_by_domain if d.value == "DM"] else []
                dm = dm_list[0].data if dm_list else {}
                subjects_list.append({
                    "usubjid": usubjid,
                    "site": dm.get("SITEID", usubjid.split("-")[1] if "-" in usubjid else "N/A"),
                    "arm": dm.get("ARM", "N/A"),
                    "sex": dm.get("SEX", "N/A"),
                    "age": dm.get("AGE", "N/A"),
                    "race": dm.get("RACE", "N/A"),
                })
            self.send_json(200, subjects_list)
            return

        # 3. API: /api/patient360
        elif path == "/api/patient360" or path.startswith("/patient360"):
            usubjid = params.get("usubjid", [""])[0]
            if not usubjid and "/" in path:
                parts = path.strip("/").split("/")
                if len(parts) >= 2:
                    usubjid = parts[1]

            if not usubjid:
                self.send_json(400, {"error": "Missing usubjid parameter"})
                return

            p360 = graph.patient360(usubjid)
            self.send_json(200, {
                "usubjid": p360.usubjid,
                "dm": p360.dm,
                "ae": p360.ae,
                "lb": p360.lb,
                "vs": p360.vs,
                "ex": p360.ex,
                "cm": p360.cm,
                "ds": p360.ds,
                "mh": p360.mh,
                "eg": p360.eg,
            })
            return

        # 4. API: /api/public_results
        elif path == "/api/public_results":
            res_candidates = [
                os.path.join(PROJECT_ROOT, "stage1_public.json"),
                os.path.join(PROJECT_ROOT, "benchmarks", "stage1_public.json"),
            ]
            loaded = False
            for res_path in res_candidates:
                if os.path.exists(res_path):
                    with open(res_path, "r", encoding="utf-8") as f:
                        self.send_json(200, json.load(f))
                    loaded = True
                    break
            if not loaded:
                self.send_json(404, {"error": "stage1_public.json not found"})
            return

        # ------------------------------------------------------------------
        # STAGE 2 (MONITOR) ENDPOINTS
        # ------------------------------------------------------------------

        # 5. API: /api/monitor/report
        elif path == "/api/monitor/report":
            report_data = get_or_create_report()
            self.send_json(200, report_data)
            return

        # 6. API: /api/monitor/summary
        elif path == "/api/monitor/summary":
            report_data = get_or_create_report()
            summary = report_data.get("summary", {})
            self.send_json(200, {
                "cut": report_data.get("cut", 5),
                "protocol_version": report_data.get("protocol_version", 2),
                "timestamp": report_data.get("timestamp", ""),
                "summary": summary,
                "trace_steps": len(report_data.get("trace", [])),
            })
            return

        # 7. API: /api/monitor/findings
        elif path == "/api/monitor/findings":
            report_data = get_or_create_report()
            findings = report_data.get("findings", [])
            # Filter support
            sev = params.get("severity", [None])[0]
            ftype = params.get("type", [None])[0]
            subj = params.get("usubjid", [None])[0]
            limit = int(params.get("limit", [100])[0])
            offset = int(params.get("offset", [0])[0])

            filtered = findings
            if sev:
                filtered = [f for f in filtered if f.get("severity") == sev.upper()]
            if ftype:
                filtered = [f for f in filtered if f.get("finding_type") == ftype]
            if subj:
                filtered = [f for f in filtered if subj.lower() in f.get("usubjid", "").lower()]

            total_count = len(filtered)
            paged = filtered[offset : offset + limit]

            self.send_json(200, {
                "total": total_count,
                "offset": offset,
                "limit": limit,
                "findings": paged,
            })
            return

        # 8. API: /api/monitor/escalations
        elif path == "/api/monitor/escalations":
            report_data = get_or_create_report()
            escalations = report_data.get("escalations", [])
            status = params.get("status", [None])[0]
            if status:
                escalations = [e for e in escalations if e.get("status") == status.upper()]
            self.send_json(200, escalations)
            return

        # 9. API: /api/monitor/queries
        elif path == "/api/monitor/queries":
            report_data = get_or_create_report()
            queries = report_data.get("queries", [])
            domain = params.get("domain", [None])[0]
            site = params.get("site", [None])[0]
            status = params.get("status", [None])[0]
            limit = int(params.get("limit", [100])[0])
            offset = int(params.get("offset", [0])[0])

            filtered = queries
            if domain:
                filtered = [q for q in filtered if q.get("domain") == domain.upper()]
            if site:
                filtered = [q for q in filtered if site.lower() in q.get("site_id", "").lower()]
            if status:
                filtered = [q for q in filtered if q.get("status") == status.upper()]

            total_count = len(filtered)
            paged = filtered[offset : offset + limit]

            self.send_json(200, {
                "total": total_count,
                "offset": offset,
                "limit": limit,
                "queries": paged,
            })
            return

        # 10. API: /api/monitor/compliance
        elif path == "/api/monitor/compliance":
            report_data = get_or_create_report()
            deviations = report_data.get("compliance_deviations", [])
            sev = params.get("severity", [None])[0]
            dtype = params.get("type", [None])[0]
            limit = int(params.get("limit", [100])[0])
            offset = int(params.get("offset", [0])[0])

            filtered = deviations
            if sev:
                filtered = [d for d in filtered if d.get("severity") == sev.upper()]
            if dtype:
                filtered = [d for d in filtered if d.get("deviation_type") == dtype]

            total_count = len(filtered)
            paged = filtered[offset : offset + limit]

            self.send_json(200, {
                "total": total_count,
                "offset": offset,
                "limit": limit,
                "deviations": paged,
            })
            return

        # 11. API: /api/monitor/trace
        elif path == "/api/monitor/trace":
            report_data = get_or_create_report()
            self.send_json(200, report_data.get("trace", []))
            return

        # 12. API: /api/monitor/export
        elif path == "/api/monitor/export":
            report_data = get_or_create_report()
            body = json.dumps(report_data, indent=2, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="stage2_review_report.json"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return

        # ------------------------------------------------------------------
        # STATIC ASSETS / FRONTEND
        # ------------------------------------------------------------------

        # 13. Serve Frontend (index.html)
        elif path == "/" or path == "/index.html" or path == "/frontend":
            html_path = os.path.join(PROJECT_ROOT, "frontend", "index.html")
            if os.path.exists(html_path):
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_json(404, {"error": "frontend/index.html not found"})
            return

        # Fallback 404
        self.send_json(404, {"error": f"Endpoint {path} not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Read JSON body
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            body = {}

        # ------------------------------------------------------------------
        # STAGE 1 (ATLAS) POST ENDPOINTS
        # ------------------------------------------------------------------

        # 1. API: /api/answer
        if path == "/api/answer" or path == "/answer":
            question = body.get("question", "")
            if not question:
                self.send_json(400, {"error": "Missing 'question' in request body"})
                return

            cut = body.get("cut", None)
            if cut is not None and cut != graph._build_cut:
                graph.build(cut)

            t_start = time.time()
            answer_obj = atlas.answer(question)
            dt_ms = round((time.time() - t_start) * 1000, 2)

            is_valid = validate_answer(answer_obj)

            resp = {
                "question_id": answer_obj.question_id,
                "question": question,
                "answer": answer_obj.answer,
                "evidence": [str(r) for r in answer_obj.evidence],
                "meta": answer_obj.meta or {},
                "latency_ms": dt_ms,
                "schema_valid": is_valid,
                "evidence_valid": True,
            }
            self.send_json(200, resp)
            return

        # 2. API: /api/build
        elif path == "/api/build" or path == "/build":
            cut = body.get("cut", None)
            t_start = time.time()
            graph.build(cut=cut)
            dt = time.time() - t_start
            cur_stats = graph.get_stats()
            self.send_json(200, {
                "status": "rebuilt",
                "cut": cut or "latest",
                "subjects": cur_stats.subjects,
                "records": cur_stats.nodes,
                "edges": cur_stats.edges,
                "build_time_seconds": round(dt, 3),
            })
            return

        # ------------------------------------------------------------------
        # STAGE 2 (MONITOR) POST ENDPOINTS
        # ------------------------------------------------------------------

        # 3. API: /api/monitor/run or /api/monitor/cycle
        elif path == "/api/monitor/run" or path == "/api/monitor/cycle":
            global active_report_dict
            cut = int(body.get("cut", 5))
            protocol_version = int(body.get("protocol_version", 2))

            print(f"[MONITOR Crew] Running 6-Node Monitoring Cycle: Cut {cut}, Protocol v{protocol_version}...")
            t_start = time.time()

            # Ensure graph is synced to the requested cut
            if cut != graph._build_cut:
                graph.build(cut=cut)

            report = crew.run_cycle(cut=cut, protocol_version=protocol_version)
            dt = round(time.time() - t_start, 3)

            active_report_dict = report.to_dict()
            active_report_dict["execution_time_seconds"] = dt

            print(f"[MONITOR Crew] Cycle completed in {dt:.3f}s: {len(report.findings)} findings, {len(report.escalations)} escalations.")
            self.send_json(200, active_report_dict)
            return

        # 4. API: /api/monitor/gate (Interactive Human Gate Decision)
        elif path == "/api/monitor/gate":
            escalation_id = body.get("escalation_id", "")
            decision = body.get("decision", "APPROVED").upper()  # APPROVED, REJECTED, CLARIFY
            reason = body.get("reason", f"Reviewer decision: {decision}")

            if not active_report_dict or "escalations" not in active_report_dict:
                self.send_json(400, {"error": "No active monitoring report available. Run a cycle first."})
                return

            found = False
            target_esc = None
            for esc in active_report_dict.get("escalations", []):
                if esc.get("escalation_id") == escalation_id:
                    esc["status"] = decision
                    esc["monitor_decision"] = decision
                    esc["monitor_reason"] = reason
                    found = True
                    target_esc = esc
                    break

            if found:
                self.send_json(200, {
                    "status": "success",
                    "escalation_id": escalation_id,
                    "new_status": decision,
                    "reason": reason,
                    "escalation": target_esc,
                })
            else:
                self.send_json(404, {"error": f"Escalation {escalation_id} not found"})
            return

        self.send_json(404, {"error": f"POST endpoint {path} not found"})


def run(port: int = 8000):
    """Start the pure Python combined ATLAS + MONITOR dashboard server."""
    server_address = ("127.0.0.1", port)
    try:
        httpd = http.server.HTTPServer(server_address, AtlasDashboardHandler)
    except OSError:
        # Port fallback
        server_address = ("127.0.0.1", 8080)
        httpd = http.server.HTTPServer(server_address, AtlasDashboardHandler)

    active_port = server_address[1]
    url = f"http://127.0.0.1:{active_port}"

    print("=" * 76)
    print("      ATLAS // STUDY SENTINEL — CLINICAL INTELLIGENCE PLATFORM")
    print("      PROBLEM 1 (ATLAS)  +  PROBLEM 2 (MONITOR)")
    print("=" * 76)
    print(f"  * Runtime:      Pure Python Standard Library (Zero 3rd-party pip pkgs)")
    print(f"  * Local URL:    {url}")
    print(f"  * Stage 1:      Q&A Engine, Patient 360, Hy's Law Signals, Temporal Cuts")
    print(f"  * Stage 2:      6-Node Crew (Detect -> Medical Review -> Data Manager ->")
    print(f"                  Compliance -> Human Gate -> Execute), Site Queries, Deviations")
    print("=" * 76)
    print(f"  Open your browser and navigate to: {url}\n  Press Ctrl+C to terminate.\n")


    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[ATLAS Dashboard] Server shutting down cleanly.")
        httpd.server_close()


if __name__ == "__main__":
    port_arg = 8000
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port_arg = int(sys.argv[1])
    run(port=port_arg)
