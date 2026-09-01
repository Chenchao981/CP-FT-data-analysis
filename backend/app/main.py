from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.analysis_rules import router as analysis_rules_router
from app.api.analytics import router as analytics_router
from app.api.analytics_exports import router as analytics_exports_router
from app.api.auth import router as auth_router
from app.api.catalog import router as catalog_router
from app.api.cleaners import router as cleaners_router
from app.api.contracts import router as contracts_router
from app.api.data_domains import router as data_domains_router
from app.api.datasets import router as datasets_router
from app.api.enrichments import router as enrichments_router
from app.api.health import router as health_router
from app.api.input_requests import router as input_requests_router
from app.api.jobs import router as jobs_router
from app.api.lifecycle import router as lifecycle_router
from app.api.management import router as management_router
from app.api.master_data import router as master_data_router
from app.api.operations import router as operations_router
from app.api.parameter_relationship import router as parameter_relationship_router
from app.api.quality_evaluation import router as quality_evaluation_router
from app.api.quick_analysis import router as quick_analysis_router
from app.api.saved_analyses import router as saved_analyses_router
from app.api.spatial_analysis import router as spatial_analysis_router
from app.api.stage_data import _upload_root
from app.api.stage_data import router as stage_data_router
from app.api.wafer_summary import router as wafer_summary_router
from app.api.worker_operations import router as worker_operations_router
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.exception_handlers import domain_error_handler, validation_error_handler
from app.core.logging import configure_logging
from app.core.middleware import (
    AnalyticsFeatureFlagMiddleware,
    LocalResultBodyLimitMiddleware,
    RequestContextMiddleware,
)
from app.domain.jobs import InMemoryJobService
from app.domain.quick_analysis import InMemoryQuickAnalysisService
from app.domain.quick_capacity import QuickCapacityPolicy
from app.infrastructure.analytics_instant_risk_service import (
    AnalyticsInstantRiskService,
)
from app.infrastructure.database import get_engine
from app.infrastructure.formal_artifact_files import ManagedJobPathPolicy
from app.infrastructure.source_catalog import SourceCatalog
from app.infrastructure.sql_analysis_rule_service import SqlAnalysisRuleService
from app.infrastructure.sql_analytics_export_service import SqlAnalyticsExportService
from app.infrastructure.sql_analytics_service import SqlAnalyticsService
from app.infrastructure.sql_auth_service import SqlAuthService
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_data_domain_service import SqlDataDomainService
from app.infrastructure.sql_dataset_service import SqlDatasetService
from app.infrastructure.sql_enrichment_service import SqlFieldEnrichmentService
from app.infrastructure.sql_input_request_service import (
    SqlProcessingInputRequestService,
)
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_lifecycle_service import SqlLifecycleService
from app.infrastructure.sql_m2_query_service import SqlM2QueryService
from app.infrastructure.sql_management_service import SqlManagementService
from app.infrastructure.sql_master_data_service import SqlMasterDataService
from app.infrastructure.sql_operations_service import SqlOperationsService
from app.infrastructure.sql_parameter_relationship_service import (
    SqlParameterRelationshipService,
)
from app.infrastructure.sql_quality_evaluation_service import (
    SqlQualityEvaluationService,
)
from app.infrastructure.sql_quick_analysis_service import SqlQuickAnalysisService
from app.infrastructure.sql_saved_analysis_service import SqlSavedAnalysisService
from app.infrastructure.sql_spatial_analysis_service import SqlSpatialAnalysisService
from app.infrastructure.sql_stage_data_service import SqlStageDataService
from app.infrastructure.sql_wafer_summary_service import SqlWaferSummaryService
from app.infrastructure.sql_worker_operations_service import (
    SqlWorkerOperationsService,
)


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    quick_capacity = QuickCapacityPolicy.from_environment()
    database_configured = bool(os.getenv("TMS_DATABASE_URL"))
    application = FastAPI(
        title="TMS CP/FT Data Platform",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    application.add_middleware(AnalyticsFeatureFlagMiddleware)
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(LocalResultBodyLimitMiddleware)
    application.add_exception_handler(DomainError, domain_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.state.job_service = (
        SqlJobService(get_engine())
        if settings.job_repository == "sql"
        else InMemoryJobService()
    )
    application.state.analytics_feature_flags = settings.analytics_features.as_dict()
    application.state.analysis_rule_service = (
        SqlAnalysisRuleService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.dataset_service = (
        SqlDatasetService(
            get_engine(), rule_service=application.state.analysis_rule_service
        )
        if os.getenv("TMS_DATABASE_URL")
        else None
    )
    application.state.data_domain_service = (
        SqlDataDomainService(get_engine()) if database_configured else None
    )
    application.state.analytics_service = (
        SqlAnalyticsService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.spatial_analysis_service = (
        SqlSpatialAnalysisService(
            get_engine(), rule_service=application.state.analysis_rule_service
        )
        if os.getenv("TMS_DATABASE_URL")
        else None
    )
    application.state.parameter_relationship_service = (
        SqlParameterRelationshipService(
            get_engine(), rule_service=application.state.analysis_rule_service
        )
        if os.getenv("TMS_DATABASE_URL")
        else None
    )
    application.state.quality_evaluation_service = (
        SqlQualityEvaluationService(
            get_engine(), rule_service=application.state.analysis_rule_service
        )
        if os.getenv("TMS_DATABASE_URL")
        else None
    )
    application.state.analytics_instant_risk_service = (
        AnalyticsInstantRiskService(
            application.state.dataset_service,
            application.state.quality_evaluation_service,
        )
        if os.getenv("TMS_DATABASE_URL")
        else None
    )
    application.state.wafer_summary_service = (
        SqlWaferSummaryService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.saved_analysis_service = (
        SqlSavedAnalysisService(get_engine()) if os.getenv("TMS_DATABASE_URL") else None
    )
    application.state.analytics_export_service = (
        SqlAnalyticsExportService(get_engine())
        if os.getenv("TMS_DATABASE_URL")
        else None
    )
    application.state.field_enrichment_service = (
        SqlFieldEnrichmentService(get_engine())
        if os.getenv("TMS_DATABASE_URL")
        else None
    )
    application.state.processing_input_request_service = (
        SqlProcessingInputRequestService(get_engine())
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
    application.state.operations_service = (
        SqlOperationsService(get_engine(), environment=settings.environment)
        if database_configured
        else None
    )
    application.state.m2_query_service = (
        SqlM2QueryService(get_engine()) if database_configured else None
    )
    application.state.management_service = (
        SqlManagementService(get_engine()) if database_configured else None
    )
    application.state.master_data_service = (
        SqlMasterDataService(get_engine()) if database_configured else None
    )
    application.state.worker_operations_service = (
        SqlWorkerOperationsService(get_engine()) if database_configured else None
    )
    application.state.lifecycle_service = (
        SqlLifecycleService(
            get_engine(),
            ManagedJobPathPolicy(
                Path(
                    os.getenv(
                        "TMS_WORK_ROOT",
                        r"F:\CP-FT数据分析\data\work",
                    )
                )
            ),
        )
        if database_configured
        else None
    )
    application.state.quick_capacity_policy = quick_capacity
    source_catalog = SourceCatalog.from_environment()
    source_catalog.assert_storage_separate(_upload_root())
    application.state.source_catalog = source_catalog
    application.include_router(health_router, prefix="/api/v1/health", tags=["health"])
    application.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    application.include_router(
        data_domains_router, prefix="/api/v1", tags=["data-domains"]
    )
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
        analytics_router, prefix="/api/v1/analytics", tags=["analytics"]
    )
    application.include_router(
        analysis_rules_router,
        prefix="/api/v1/analysis-rules",
        tags=["analysis-rules"],
    )
    application.include_router(
        spatial_analysis_router,
        prefix="/api/v1/analytics/spatial",
        tags=["analytics"],
    )
    application.include_router(
        parameter_relationship_router,
        prefix="/api/v1/analytics",
        tags=["analytics"],
    )
    application.include_router(
        quality_evaluation_router,
        prefix="/api/v1/analytics/quality-evaluation",
        tags=["analytics"],
    )
    application.include_router(
        wafer_summary_router,
        prefix="/api/v1/analytics/wafer-summary",
        tags=["analytics"],
    )
    application.include_router(
        saved_analyses_router,
        prefix="/api/v1/analytics",
        tags=["analytics"],
    )
    application.include_router(
        analytics_exports_router,
        prefix="/api/v1/analytics",
        tags=["analytics"],
    )
    application.include_router(
        catalog_router, prefix="/api/v1/catalog", tags=["catalog"]
    )
    application.include_router(
        enrichments_router, prefix="/api/v1/enrichments", tags=["enrichments"]
    )
    application.include_router(stage_data_router, prefix="/api/v1", tags=["stage-data"])
    application.include_router(
        input_requests_router, prefix="/api/v1", tags=["input-requests"]
    )
    application.include_router(
        quick_analysis_router, prefix="/api/v1", tags=["quick-analysis"]
    )
    application.include_router(operations_router, prefix="/api/v1", tags=["operations"])
    application.include_router(
        worker_operations_router, prefix="/api/v1", tags=["worker-operations"]
    )
    application.include_router(management_router, prefix="/api/v1", tags=["management"])
    application.include_router(
        master_data_router, prefix="/api/v1", tags=["master-data"]
    )
    application.include_router(lifecycle_router, prefix="/api/v1", tags=["lifecycle"])
    return application


app = create_app()
