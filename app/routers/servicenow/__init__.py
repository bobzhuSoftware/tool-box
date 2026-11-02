"""ServiceNow count tables feature, split by domain. Exposes a single combined `router`.

Domains: tables (grid config), links (saved links + condition composer/library/import/builder),
accounts (login identities), worker (Edge-automation SSE runs).
"""
from fastapi import APIRouter

from . import accounts, links, tables, worker

router = APIRouter()
router.include_router(tables.router)
router.include_router(links.router)
router.include_router(accounts.router)
router.include_router(worker.router)
