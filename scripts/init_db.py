#!/usr/bin/env python3
"""Initialize the SQLite database."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.state import init_db

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully")
