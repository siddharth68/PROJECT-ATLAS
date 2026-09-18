from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
import json
import os
import sys

# Add atlas to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from atlas import StudyGraph, Atlas

app = FastAPI(title="ATLAS - Study Sentinel Problem 1")

# Global graph and atlas instances
graph = None
atlas = None

class QuestionRequest(BaseModel):
    question: str
    cut: Optional[int] = None

class BuildRequest(BaseModel):
    cut: Optional[int] = None

class AnswerResponse(BaseModel):
    question_id: str
    answer: Any
    evidence: List[str]
    meta: Dict[str, Any]

@app.on_event("startup")
async def startup():
    global graph, atlas
    data_dir = os.path.join(os.path.dirname(__file__), "hackathon-data", "hackathon-data")
    graph = StudyGraph(data_dir)
    graph.build()
    atlas = Atlas(graph)

@app.post("/build")
async def build_graph(request: BuildRequest):
    global graph, atlas
    graph.build(request.cut)
    stats = graph.get_stats()
    return {
        "status": "built",
        "cut": request.cut,
        "subjects": stats.subjects,
        "records": stats.nodes,
        "records_by_domain": stats.records_by_domain
    }

@app.get("/stats")
async def get_stats():
    if not graph._built:
        graph.build()
    stats = graph.get_stats()
    return {
        "nodes": stats.nodes,
        "edges": stats.edges,
        "subjects": stats.subjects,
        "records_by_domain": stats.records_by_domain,
        "cuts": stats.cuts
    }

@app.post("/answer", response_model=AnswerResponse)
async def answer_question(request: QuestionRequest):
    if not graph._built:
        graph.build()
    if request.cut is not None and request.cut != graph._build_cut:
        graph.build(request.cut)
    answer = atlas.answer(request.question)
    return answer.to_dict()

@app.get("/patient360/{usubjid}")
async def get_patient360(usubjid: str):
    if not graph._built:
        graph.build()
    p360 = graph.patient360(usubjid)
    return {
        "usubjid": p360.usubjid,
        "dm": p360.dm,
        "ae_count": len(p360.ae),
        "lb_count": len(p360.lb),
        "vs_count": len(p360.vs),
        "ex_count": len(p360.ex),
        "cm_count": len(p360.cm),
        "ds": p360.ds,
        "mh": p360.mh,
        "eg_count": len(p360.eg)
    }

@app.get("/subjects")
async def list_subjects():
    if not graph._built:
        graph.build()
    return {"subjects": list(graph.records_by_subject.keys())}

# Serve static frontend
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

@app.get("/", response_class=HTMLResponse)
async def root():
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("""
    <html><body>
    <h1>ATLAS API Running</h1>
    <p>Frontend not found. API endpoints:</p>
    <ul>
        <li>POST /build - Build graph at cut</li>
        <li>GET /stats - Graph statistics</li>
        <li>POST /answer - Answer question</li>
        <li>GET /patient360/{usubjid} - Patient profile</li>
        <li>GET /subjects - List all subjects</li>
    </ul>
    </body></html>
    """)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)