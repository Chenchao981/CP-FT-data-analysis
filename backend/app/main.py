from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.auth import router as auth_router
from app.api.cleaners import router as cleaners_router
from app.api.contracts import router as contracts_router
from app.api.datasets import router as datasets_router
from app.api.enrichments import router as enrichments_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.quick_analysis import router as quick_analysis_router
from app.api.stage_data import router as stage_data_router
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.exception_handlers import domain_error_handler, validation_error_handler
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.domain.jobs import InMemoryJobService
from app.domain.quick_analysis import InMemoryQuickAnalysisService
from app.domain.quick_capacity import QuickCapacityPolicy
from app.infrastructure.database import get_engine
from app.infrastructure.source_catalog import SourceCatalog
from app.infrastructure.sql_auth_service import SqlAuthService
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_dataset_service import SqlDatasetService
from app.infrastructure.sql_enrichment_service import SqlFieldEnrichmentService
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_quick_analysis_service import SqlQuickAnalysisService
from app.infrastructure.sql_stage_data_service import SqlStageDataService


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    quick_capacity = QuickCapacityPolicy.from_environment()
    database_configured = bool(os.getenv("TMS_DATABASE_URL"))
    application = FastAPI(
        title="TMS CP/FT Data Platform",
        version="0.4.1",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    application.add_middleware(RequestContextMiddleware)
    application.add_exception_handler(DomainError, domain_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.state.job_service = (
        SqlJobService(get_engine())
        if settings.job_repository == "sql"
        else InMemoryJobService()
    )
    application.state.dataset_service = (
        SqlDatasetService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.field_enrichment_service = (
        SqlFieldEnrichmentService(get_engine())
        if os.getenv("TMS_DATABASE_URL")
        else None
    )
    application.state.auth_service = (
        SqlAuthService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.stage_data_service = (
        SqlStageDataService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.cleaner_registry = (
        SqlCleanerRegistry(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.quick_analysis_service = (
        SqlQuickAnalysisService(get_engine(), capacity=quick_capacity)
        if database_configured and settings.job_repository == "sql"
        else InMemoryQuickAnalysisService(capacity=quick_capacity)
    )
    application.state.quick_capacity_policy = quick_capacity
    application.state.source_catalog = SourceCatalog.from_environment()
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
    application.include_router(stage_data_router, prefix="/api/v1", tags=["stage-data"])
    application.include_router(
        quick_analysis_router, prefix="/api/v1", tags=["quick-analysis"]
    )
    return application


app = create_app()
