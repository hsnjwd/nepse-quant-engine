"""NEPSE Quant Engine — reproducible performance benchmark suite (Sprint 11.1).

Run with::

    python -m benchmarks.runner --symbols 50 --rows 500

Results (JSON + console table) are written under ``benchmarks/results/``.
Every benchmark records execution time, symbol/row counts, cache state
and peak memory where practical.  Nothing here modifies production data:
all inputs are synthetic DataFrames written to a temp directory.
"""
