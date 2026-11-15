"""ServiceNow count tables feature, split by domain. Exposes a single combined `router`.

Domains: tables (grid config), links (saved links + condition composer/library/import/builder),
migrate (export / import of tables, links and conditions), rehost (old host -> ServiceCentral host),
accounts (login identities), worker (Edge-automation SSE runs).
"""
from fastapi import APIRouter

from . import accounts, links, migrate, rehost, tables, worker

router = APIRouter()
router.include_router(tables.router)
router.include_router(links.router)
router.include_router(migrate.router)
router.include_router(rehost.router)
router.include_router(accounts.router)
router.include_router(worker.router)
