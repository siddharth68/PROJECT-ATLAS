#!/usr/bin/env python3
"""
ATLAS Problem 1 — Web Server Entry Point.
Delegates to the pure Python standard library dashboard in app.py.
Runs with ZERO external pip dependencies.
"""
import os
import sys
from app import run

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run(port=port, host="0.0.0.0")