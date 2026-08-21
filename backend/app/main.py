from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.contracts import router as contracts_router
from app.api.auth import router as auth_router
from app.api.cleaners import router as cleaners_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.datasets import router as datasets_router
from app.api.enrichments import router as enrichments_router
from app.core.errors import DomainError
from app.core.exception_handlers import domain_error_handler, validation_error_handler
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.domain.jobs import InMemoryJobService
from app.infrastructure.database import get_engine
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_dataset_service import SqlDatasetService
from app.infrastructure.sql_enrichment_service import SqlFieldEnrichmentService
from app.infrastructure.sql_auth_service import SqlAuthService


def create_app() -> FastAPI:
    configure_logging()
    application = FastAPI(
        title="TMS CP/FT Data Platform",
        version="0.3.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    application.add_middleware(RequestContextMiddleware)
    application.add_exception_handler(DomainError, domain_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.state.job_service = (
        SqlJobService(get_engine())
        if get_settings().job_repository == "sql"
        else InMemoryJobService()
    )
    application.state.dataset_service = (
        SqlDatasetService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.field_enrichment_service = (
        SqlFieldEnrichmentService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.auth_service = (
        SqlAuthService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.include_router(health_router, prefix="/api/v1/health", tags=["health"])
    application.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    application.include_router(
        contracts_router, prefix="/api/v1/contracts", tags=["contracts"]
    )
    application.include_router(
        cleaners_router, prefix="/api/v1/cleaners", tags=["cleaners"]
    )
    application.include_router(jobs_router, prefix="/api/v1/jobs", tags=["jobs"])
    application.include_router(
        datasets_router, prefix="/api/v1/datasets", tags=["datasets"]
    )
    application.include_router(
        enrichments_router, prefix="/api/v1/enrichments", tags=["enrichments"]
    )
    return application


app = create_app()
