#!/usr/bin/env python3
"""Initialise database — creates missing tables (idempotent).

Use --reset flag to drop and recreate all tables (DESTRUCTIVE).
"""
import os
import sys

# Add app root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.db import init_db, create_all_tables, drop_all_tables
# Import all models so they register with Base.metadata
import src.models  # noqa: F401


def main():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL not set. Check your .env file.")
        sys.exit(1)

    reset = '--reset' in sys.argv

    print(f"Connecting to: {database_url.split('@')[1] if '@' in database_url else database_url}")

    init_db(database_url)

    if reset:
        print("WARNING: --reset flag set. Dropping all tables...")
        drop_all_tables()
        print("Creating all tables...")
        create_all_tables()
        print("Database RESET complete.")
    else:
        print("Creating missing tables (existing data preserved)...")
        create_all_tables()
        print("Database initialised successfully.")


if __name__ == '__main__':
    main()
