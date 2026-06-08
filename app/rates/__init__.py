"""Country rates module.

Loads sectoral wage rates from YAML files in data/country_rates/.
Provides:
- Schema validation (Marshmallow)
- Date-range lookup
- Snapshot persistence for reproducible payroll calculations
- Read-only browsing UI for accountants
- `flask rates` CLI commands

YAML files are the source of truth. Updates happen via git PRs.
"""
