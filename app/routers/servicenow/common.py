"""Shared primitives for the ServiceNow routers: constants, settings helpers, URL check, run lock."""
import json
import os
import re
import threading
from urllib.parse import urlparse

from app.core.settings import _get_user_setting

# Repo root (four levels up from app/routers/servicenow/common.py), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

_SETTING_KEY = "servicenow_tables"
_ACCOUNTS_KEY = "servicenow_accounts"
_LINKS_KEY = "servicenow_links"
_CONDITION_NAMES_KEY = "servicenow_condition_names"
_MAX_CONDITION_NAMES = 500
_CONDITION_LIBRARY_KEY = "servicenow_condition_library"
_CONDITION_TABLES_KEY = "servicenow_condition_tables"
_VALUE_NAMES_KEY = "servicenow_value_names"
_MAX_NAME_ENTRIES = 5000
_MAX_NAME_TABLES = 30
_MAX_NAME_FIELDS = 60
_MAX_NAME_VALUES = 200
_MAX_LIBRARY_CONDITIONS = 1000
_MAX_IMPORT_URLS = 20
_TABLE_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")
_MAX_BUILD_CONDITIONS = 60
_MAX_LINKS = 200
_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,19}$")
_MAX_URLS = 200
_DEFAULT_ACCOUNT_ID = "default"
_ACCOUNT_ID_RE = re.compile(r"^[a-z0-9]{1,16}$")
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")

# One worker at a time: login/refresh/logout all touch the same Edge profiles.
_run_lock = threading.Lock()

_DEFAULT_URL = (
    "https://dsv.service-now.com/x/dsva2/enterprise-integrations/simplelist/x_dsva2_enterpri_0_edi_case"
    "/params/list-title/Cases%20Received%20in%20past%207%20days%20Blue/query/"
    "origin_of_request!%3Dschenker_origin%5Estate!%3D1000%5Estate!%3D1300"
    "%5Edivision%3D420b49d01b3ae5103620b6c5464bcb89"
    "%5Esys_created_onBETWEENjavascript%3Ags.beginningOfLast7Days()%40javascript%3Ags.endOfToday()"
    "%5Eassignment_group!%3D2dafd3ec2b6d7690db00f454c891bf54"
)


def _load_json_setting(user_id: str, key: str, default):
    """Parse a per-user JSON setting; return `default` (called when callable) on missing or bad data."""
    raw = _get_user_setting(user_id, key)
    if raw:
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            pass
    return default() if callable(default) else default


def _load_model_setting(user_id: str, key: str, model, default):
    """Load a per-user JSON setting into a pydantic model; return `default()` on missing or bad data."""
    data = _load_json_setting(user_id, key, None)
    if isinstance(data, dict):
        try:
            return model(**data)
        except (TypeError, ValueError):
            pass
    return default()


def _is_servicenow_url(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and (host == "service-now.com" or host.endswith(".service-now.com"))
