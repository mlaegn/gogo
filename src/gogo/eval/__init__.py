"""Evaluation. Reads history, never writes to it.

Kept in its own package so the boundary is visible: nothing in here may be imported by
the worker or the serving path, and nothing in here may write to `observations`,
`forecast_snapshots` or `window_impressions`. A harness that can modify the record it
measures is not a harness.
"""
