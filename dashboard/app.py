"""Repository entry point for the Streamlit dashboard.

Run with::

    streamlit run dashboard/app.py -- --database outputs/fleetmind.duckdb
"""

from fleetmind.dashboard.app import main

main()
