"""Driver vacations & leave tracking (PRD §schedule).

Tracks annual leave (urlop wypoczynkowy — capped, counted in working days),
sick leave (L4 — uncapped, separate stat) and other absence kinds per driver,
with optional one-way push/pull sync to a Google Calendar (the app is the
source of truth). See ``models.py`` for the schema and ``services.py`` for the
working-day / balance calculations.
"""
