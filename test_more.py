import subprocess
import time
import requests

# Start server
proc = subprocess.Popen(['python', 'server.py'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(4)

tests = [
    ("How many subjects in the study?", "Count subjects"),
    ("How many subjects in drug arm?", "Drug arm"),
    ("How many sites?", "Sites"),
    ("List Hy's law candidates", "List Hy's law"),
    ("What are the monitor decisions for Hy's law candidates?", "Monitor decisions"),
    ("Which subjects have AST > 2xULN?", "AST 2xULN"),
    ("Find dosing errors", "Dosing errors"),
    ("Find serious adverse events", "SAEs"),
    ("Find discontinued subjects", "Discontinuations"),
    ("Find visit window deviations", "Visit deviations"),
    ("Show patient profile for 042-S05-003", "Patient 360"),
]

for q, label in tests:
    try:
        r = requests.post('http://localhost:8000/answer', json={'question': q}, timeout=30)
        data = r.json()
        ans = data['answer']
        if isinstance(ans, list):
            print(f"\n{label}: {len(ans)} results")
            for item in ans[:3]:
                print(f"  - {item}")
            if len(ans) > 3:
                print(f"  ... and {len(ans)-3} more")
        else:
            print(f"\n{label}: {ans}")
    except Exception as e:
        print(f"\n{label}: ERROR - {e}")

proc.terminate()