#!/usr/bin/env python3
"""
ATLAS // STUDY SENTINEL — MAIN PLATFORM RUNNER
Problem 1 (ATLAS) + Problem 2 (MONITOR) + Python Frontend

Usage:
    python main.py
    # or specify custom port
    python main.py 8080
"""

import os
import sys

# Ensure current directory is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import run

if __name__ == "__main__":
    port = 8000
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run(port=port)
