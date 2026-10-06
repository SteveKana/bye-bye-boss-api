"""The daily brief used to be a fixed 18:30 job, right after the (then) 18:00
matching run. Since the 2026-10-04 redesign matching results come back
from OpenAI's Batch API at no fixed time, so the brief is now triggered by
matching's `MatchesScored` event instead -- see listeners.py. The service
method `DailyBriefService.send_daily_briefs` (every complete profile) is kept
for the CLI/tests but is no longer scheduled.
"""
