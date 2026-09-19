import os
import sys
import subprocess
import time
import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Start server
proc = subprocess.Popen([sys.executable, 'app.py'], cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(3)

# Test API
try:
    r = requests.get('http://localhost:8000/stats', timeout=10)
    print('STATS:', r.json())
except Exception as e:
    print('Stats error:', e)

try:
    r = requests.post('http://localhost:8000/answer', json={'question': "How many Hy's law candidates?"}, timeout=30)
    print('ANSWER:', r.json())
except Exception as e:
    print('Answer error:', e)

try:
    r = requests.post('http://localhost:8000/answer', json={'question': 'Which subjects have ALT > 3xULN?'}, timeout=30)
    print('ALT 3xULN:', r.json())
except Exception as e:
    print('ALT error:', e)

try:
    r = requests.post('http://localhost:8000/answer', json={'question': 'Find prohibited medications for 042-S07-001'}, timeout=30)
    print('PROHIBITED:', r.json())
except Exception as e:
    print('Prohibited error:', e)

# Stop server
proc.terminate()