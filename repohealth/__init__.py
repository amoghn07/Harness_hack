"""Open-source repo health monitor & auto-fixer.

Phase 1: ingest GitHub data (issues, PRs, dependency files, CI runs) and store
it in a queryable backend that becomes the agent's memory. Every later phase
reads from this store via SQL aggregation rather than hitting GitHub live.
"""

__version__ = "0.1.0"
