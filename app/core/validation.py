"""Shared request-validation helpers."""
import re

# Matches http:// and https:// URLs. Shared by pdf, dsv and teams_transcript.
_URL_PATTERN = re.compile(r'^https?://', re.IGNORECASE)
