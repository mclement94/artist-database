#!/usr/bin/env python
"""
Migration script to add the colorcode column to the artwork table.
Run this once to update your existing database.
"""

import os
import sqlite3

# Get database path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else BASE_DIR)
DB_PATH = os.path.join(DATA_DIR, "database.db")

print(f"Database path: {DB_PATH}")

try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if colorcode column already exists
    cursor.execute("PRAGMA table_info(artwork)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "colorcode" in columns:
        print("✓ colorcode column already exists. No migration needed.")
    else:
        print("Adding colorcode column to artwork table...")
        cursor.execute("ALTER TABLE artwork ADD COLUMN colorcode VARCHAR(50)")
        conn.commit()
        print("✓ colorcode column added successfully!")
    
    conn.close()
    print("Migration complete.")
    
except Exception as e:
    print(f"Error: {e}")
    exit(1)
