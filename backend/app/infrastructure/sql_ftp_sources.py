from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from app.core.errors import DomainError
from app.domain.auth import Principal, has_global_data_access
from app.domain.cleaner_capabilities import FORMAL_CLEANER_CONTRACTS
from app.domain.ftp_sources import FtpSourceCreate, RemotePackage
from app.domain.jobs import CreateJobRequest, JobType, TriggerType
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_stage_data_service import SqlStageDataService


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _serialize(row):
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in dict(row).items()}


def _admin(principal):
    if not principal.can("SOURCE_ADMIN"):
        raise DomainError("PERMISSION_DENIED", "只有数据源管理员可以配置或调度 FTP 采集", 403)


class SqlFtpSourceService:
    def __init__(self, engine):
        self.engine = engine

    def options(self, principal):
        _admin(principal)
        with self.engine.connect() as connection:
            domains = [dict(row) for row in connection.execute(text(
                "SELECT data_domain_id,domain_code,domain_name,test_stage,factory_code FROM iam.data_domain "
                "WHERE active=1 AND domain_code<>N'MIGRATION_HOLD' ORDER BY data_domain_id"
            )).mappings()]
            releases = []
            rows = connection.execute(text(
                "SELECT cr.cleaner_release_id,cr.cleaner_version,cr.cleaner_code,cr.adapter_code,"
                "cr.input_contract_version,cr.output_contract_version,fp.test_stage,fp.factory_code,fp.format_code "
                "FROM ingestion.cleaner_release cr JOIN ingestion.format_profile fp ON fp.format_profile_id=cr.format_profile_id "
                "WHERE cr.status='RELEASED' AND fp.status='RELEASED' ORDER BY cr.cleaner_release_id DESC"
            )).mappings()
            for row in rows:
                contract = FORMAL_CLEANER_CONTRACTS.get((row["test_stage"], row["factory_code"]))
                if contract and all(row[key] == value for key, value in contract.items()):
                    releases.append(dict(row))
            return dict(domains=domains, releases=releases)

    def list(self, principal):
        if not principal.can("SOURCE_ADMIN") and not principal.can("DATASET_READ"):
            raise DomainError("PERMISSION_DENIED", "缺少数据源查看权限", 403)
        with self.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT s.source_definition_id,s.source_code,s.source_name,s.test_stage,s.factory_code,"
                "s.data_domain_id,d.domain_name,s.cleaner_release_id,s.active,c.config_json,c.last_status,"
                "c.last_started_at_utc,c.last_finished_at_utc,c.next_scan_at_utc,c.lease_expires_at_utc,c.worker_id,"
                "c.error_code,c.error_message,c.scan_requested FROM ingestion.source_definition s "
                "JOIN ingestion.ftp_collection_state c ON c.source_definition_id=s.source_definition_id "
                "JOIN iam.data_domain d ON d.data_domain_id=s.data_domain_id WHERE "
                "(:control=1 OR :global_read=1 OR EXISTS(SELECT 1 FROM iam.data_domain_grant g "
                "WHERE g.data_domain_id=s.data_domain_id AND g.user_id=:user_id AND g.status='ACTIVE' "
                "AND d.active=1 AND (g.expires_at_utc IS NULL OR g.expires_at_utc>SYSUTCDATETIME()))) "
                "ORDER BY s.source_definition_id DESC"
            ), dict(control=int(principal.can("SOURCE_ADMIN")), global_read=int(has_global_data_access(principal)), user_id=principal.user_id)).mappings()
            result = []
            for row in rows:
                item = _serialize(row)
                config = json.loads(item.pop("config_json"))
                item.update(protocol=config["protocol"], package_mode=config["package_mode"], interval_seconds=config["interval_seconds"])
                if principal.can("SOURCE_ADMIN"):
                    item["config"] = config
                result.append(item)
            return result

    def _binding(self, connection, config):
        binding = connection.execute(text(
            "SELECT d.data_domain_id,d.domain_code,u.user_id AS service_user_id "
            "FROM iam.data_domain d WITH (HOLDLOCK) CROSS JOIN iam.app_user u WITH (HOLDLOCK) "
            "WHERE d.data_domain_id=:domain AND d.active=1 AND d.domain_code<>N'MIGRATION_HOLD' "
            "AND d.test_stage=:stage AND (d.factory_code IS NULL OR d.factory_code=:factory) "
            "AND u.login_name=N'SYSTEM_INGESTION' AND u.identity_provider='OIDC' "
            "AND u.external_subject=N'internal:tms:system-ingestion' AND u.status='DISABLED' "
            "AND NOT EXISTS(SELECT 1 FROM iam.user_role ur WHERE ur.user_id=u.user_id)"
        ), dict(domain=config.data_domain_id, stage=config.test_stage, factory=config.factory_code)).mappings().one_or_none()
        if binding is None:
            raise DomainError("FTP_DOMAIN_BINDING_INVALID", "请选择阶段和厂家一致的有效数据域，并核对系统采集身份", 409)
        release = connection.execute(text(
            "SELECT cr.cleaner_code,cr.adapter_code,cr.input_contract_version,cr.output_contract_version,"
            "fp.test_stage,fp.factory_code,fp.format_code FROM ingestion.cleaner_release cr WITH (HOLDLOCK) "
            "JOIN ingestion.format_profile fp WITH (HOLDLOCK) ON fp.format_profile_id=cr.format_profile_id "
            "WHERE cr.cleaner_release_id=:release AND cr.status='RELEASED' AND fp.status='RELEASED'"
        ), dict(release=config.cleaner_release_id)).mappings().one_or_none()
        expected = FORMAL_CLEANER_CONTRACTS[(config.test_stage, config.factory_code)]
        if release is None or release["test_stage"] != config.test_stage or release["factory_code"] != config.factory_code or any(release[key] != value for key, value in expected.items()):
            raise DomainError("FTP_CLEANER_BINDING_INVALID", "请选择该阶段与厂家的已发布正式 Cleaner 合同", 409)
        return dict(binding)

    def _audit(self, connection, principal, source_id, operation):
        connection.execute(text(
            "INSERT governance.audit_log(actor,operation,entity_type,entity_id,reason,actor_user_id) "
            "VALUES(:actor,:operation,'ingestion.source_definition',:entity,:operation,:user)"
        ), dict(actor=principal.login_name, operation=operation, entity=str(source_id), user=principal.user_id))

    def create(self, principal, config: FtpSourceCreate):
        _admin(principal)
        # Verify the registered executable contract as well as the transaction-time scope.
        SqlCleanerRegistry(self.engine).get_released(config.cleaner_release_id)
        with self.engine.begin() as connection:
            if connection.execute(text("SELECT 1 FROM ingestion.source_definition WITH (UPDLOCK,HOLDLOCK) WHERE source_code=:code"), dict(code=config.source_code)).first():
                raise DomainError("FTP_SOURCE_EXISTS", "数据源编码已存在，请使用新的唯一编码", 409)
            binding = self._binding(connection, config)
            source_id = int(connection.execute(text(
                "INSERT ingestion.source_definition(source_code,source_name,source_kind,root_uri,credential_ref,"
                "data_domain_id,service_user_id,test_stage,factory_code,cleaner_release_id,active,created_by_user_id) "
                "OUTPUT INSERTED.source_definition_id VALUES(:code,:name,'FTP',:uri,:credential,:domain,:service,:stage,:factory,:release,0,:creator)"
            ), dict(code=config.source_code, name=config.source_name,
                    uri=f"{config.protocol.lower()}://{config.host}:{config.port}{config.remote_root}", credential=config.credential_ref,
                    domain=config.data_domain_id, service=binding["service_user_id"], stage=config.test_stage,
                    factory=config.factory_code, release=config.cleaner_release_id, creator=principal.user_id)).scalar_one())
            connection.execute(text("INSERT ingestion.ftp_collection_state(source_definition_id,config_json) VALUES(:id,:config)"), dict(id=source_id, config=config.model_dump_json()))
            self._audit(connection, principal, source_id, "FTP_SOURCE_CREATED")
        return dict(source_definition_id=source_id, active=False)

    def config(self, source_id):
        with self.engine.connect() as connection:
            raw = connection.execute(text("SELECT config_json FROM ingestion.ftp_collection_state WHERE source_definition_id=:id"), dict(id=source_id)).scalar_one_or_none()
        if raw is None:
            raise DomainError("FTP_SOURCE_NOT_FOUND", "FTP 数据源不存在", 404)
        return FtpSourceCreate.model_validate_json(raw)

    def control(self, principal, source_id, *, active=None, scan=False):
        _admin(principal)
        config = self.config(source_id)
        with self.engine.begin() as connection:
            connection.execute(text("SELECT source_definition_id FROM ingestion.ftp_collection_state WITH (UPDLOCK,HOLDLOCK) WHERE source_definition_id=:id"), dict(id=source_id)).one()
            row = connection.execute(text("SELECT active FROM ingestion.source_definition WITH (UPDLOCK,HOLDLOCK) WHERE source_definition_id=:id"), dict(id=source_id)).one()
            if active is True or scan:
                self._binding(connection, config)
            if scan and not row.active:
                raise DomainError("FTP_SOURCE_PAUSED", "请先启用数据源，再发起采集", 409)
            if active is not None:
                connection.execute(text("UPDATE ingestion.source_definition SET active=:active,updated_at_utc=SYSUTCDATETIME() WHERE source_definition_id=:id"), dict(active=int(active), id=source_id))
            if scan or active is True:
                connection.execute(text("UPDATE ingestion.ftp_collection_state SET scan_requested=1,next_scan_at_utc=SYSUTCDATETIME() WHERE source_definition_id=:id"), dict(id=source_id))
            self._audit(connection, principal, source_id, "FTP_SCAN_REQUESTED" if scan else ("FTP_SOURCE_ENABLED" if active else "FTP_SOURCE_PAUSED"))
        return dict(accepted=True)

    def _require_data_access(self, connection, principal, source_id):
        if not principal.can("DATASET_READ"):
            raise DomainError("FTP_SOURCE_NOT_FOUND", "FTP 数据源不存在或无权查看内容", 404)
        found = connection.execute(text(
            "SELECT 1 FROM ingestion.source_definition s JOIN ingestion.ftp_collection_state c "
            "ON c.source_definition_id=s.source_definition_id JOIN iam.data_domain d ON d.data_domain_id=s.data_domain_id "
            "WHERE s.source_definition_id=:id AND (:global_read=1 OR EXISTS(SELECT 1 FROM iam.data_domain_grant g "
            "WHERE g.data_domain_id=d.data_domain_id AND d.active=1 AND g.user_id=:user_id AND g.status='ACTIVE' "
            "AND (g.expires_at_utc IS NULL OR g.expires_at_utc>SYSUTCDATETIME())))"
        ), dict(id=source_id, global_read=int(has_global_data_access(principal)), user_id=principal.user_id)).first()
        if found is None:
            raise DomainError("FTP_SOURCE_NOT_FOUND", "FTP 数据源不存在或无权查看内容", 404)

    def packages(self, principal, source_id, *, page=1, page_size=30):
        with self.engine.connect() as connection:
            self._require_data_access(connection, principal, source_id)
            total = connection.execute(text("SELECT COUNT(*) FROM ingestion.ftp_package WHERE source_definition_id=:id"), dict(id=source_id)).scalar_one()
            rows = connection.execute(text(
                "SELECT p.ftp_package_id,p.relative_path,p.status,p.attempts,p.file_count,p.total_bytes,p.job_id,"
                "p.import_batch_id,p.last_observed_at_utc,p.error_code,p.error_message,j.status AS job_status "
                "FROM ingestion.ftp_package p LEFT JOIN ingestion.processing_job j ON j.job_id=p.job_id "
                "WHERE p.source_definition_id=:id ORDER BY p.ftp_package_id DESC "
                "OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"
            ), dict(id=source_id, offset=(page - 1) * page_size, limit=page_size)).mappings()
            return dict(total=total, items=[_serialize(row) for row in rows])

    def retry_package(self, principal, source_id, package_id):
        _admin(principal)
        with self.engine.begin() as connection:
            locked = connection.execute(text("SELECT source_definition_id FROM ingestion.ftp_collection_state WITH (UPDLOCK,HOLDLOCK) WHERE source_definition_id=:id"), dict(id=source_id)).first()
            if locked is None:
                raise DomainError("FTP_SOURCE_NOT_FOUND", "FTP 数据源不存在", 404)
            updated = connection.execute(text(
                "UPDATE ingestion.ftp_package SET attempts=0,status='WAITING',error_code=NULL,error_message=NULL "
                "WHERE source_definition_id=:id AND ftp_package_id=:package AND status IN('FAILED','RETRY') AND job_id IS NULL"
            ), dict(id=source_id, package=package_id))
            if updated.rowcount != 1:
                raise DomainError("FTP_PACKAGE_NOT_RETRYABLE", "该采集项不能重试；已提交任务请从原任务处理", 409)
            connection.execute(text("UPDATE ingestion.ftp_collection_state SET scan_requested=1 WHERE source_definition_id=:id"), dict(id=source_id))
            self._audit(connection, principal, source_id, "FTP_PACKAGE_RETRY")
        return dict(accepted=True)

    def claim(self, worker_id):
        with self.engine.begin() as connection:
            row = connection.execute(text(
                "SELECT TOP(1) c.source_definition_id,c.config_json,c.lease_token FROM ingestion.ftp_collection_state c "
                "WITH (UPDLOCK,READPAST,ROWLOCK) JOIN ingestion.source_definition s ON s.source_definition_id=c.source_definition_id "
                "WHERE s.active=1 AND (c.lease_expires_at_utc IS NULL OR c.lease_expires_at_utc<=SYSUTCDATETIME()) "
                "AND (c.scan_requested=1 OR c.next_scan_at_utc<=SYSUTCDATETIME()) ORDER BY c.next_scan_at_utc,c.source_definition_id"
            )).mappings().one_or_none()
            if row is None:
                return None
            config = FtpSourceCreate.model_validate_json(row["config_json"])
            token = str(uuid4())
            args = dict(id=row["source_definition_id"], token=token, worker=worker_id)
            connection.execute(text("UPDATE ingestion.ftp_collection_run SET status='INTERRUPTED',finished_at_utc=SYSUTCDATETIME() WHERE source_definition_id=:id AND status='RUNNING'"), args)
            connection.execute(text(
                "UPDATE ingestion.ftp_collection_state SET lease_token=:token,lease_expires_at_utc=DATEADD(second,120,SYSUTCDATETIME()),"
                "worker_id=:worker,scan_requested=0,last_status='RUNNING',last_started_at_utc=SYSUTCDATETIME(),error_code=NULL,error_message=NULL "
                "WHERE source_definition_id=:id"
            ), args)
            connection.execute(text("INSERT ingestion.ftp_collection_run(source_definition_id,lease_token,worker_id) VALUES(:id,:token,:worker)"), args)
            return dict(source_id=row["source_definition_id"], token=token, config=config)

    def heartbeat(self, source_id, token):
        with self.engine.begin() as connection:
            return connection.execute(text(
                "UPDATE c SET lease_expires_at_utc=DATEADD(second,120,SYSUTCDATETIME()) FROM ingestion.ftp_collection_state c "
                "JOIN ingestion.source_definition s ON s.source_definition_id=c.source_definition_id "
                "WHERE c.source_definition_id=:id AND c.lease_token=:token AND s.active=1 AND c.lease_expires_at_utc>SYSUTCDATETIME()"
            ), dict(id=source_id, token=token)).rowcount == 1

    def validate_claim(self, source_id, token, config):
        with self.engine.begin() as connection:
            self._lease(connection, source_id, token)
            return self._binding(connection, config)

    def _lease(self, connection, source_id, token):
        state = connection.execute(text(
            "SELECT config_json FROM ingestion.ftp_collection_state WITH (UPDLOCK,HOLDLOCK) "
            "WHERE source_definition_id=:id AND lease_token=:token AND lease_expires_at_utc>SYSUTCDATETIME()"
        ), dict(id=source_id, token=token)).mappings().one_or_none()
        source = connection.execute(text(
            "SELECT service_user_id,data_domain_id FROM ingestion.source_definition WITH (UPDLOCK,HOLDLOCK) "
            "WHERE source_definition_id=:id AND active=1"
        ), dict(id=source_id)).mappings().one_or_none() if state is not None else None
        if state is None or source is None:
            raise DomainError("FTP_LEASE_LOST", "采集已暂停或执行权失效，未提交新入库任务", 409)
        return dict(source) | dict(state)

    def observe(self, source_id, token, package: RemotePackage, config):
        with self.engine.begin() as connection:
            self._lease(connection, source_id, token)
            args = dict(id=source_id, key=package.key, path=package.path, fingerprint=package.fingerprint, files=len(package.files), size=package.total_bytes)
            old = connection.execute(text("SELECT * FROM ingestion.ftp_package WITH (UPDLOCK,HOLDLOCK) WHERE source_definition_id=:id AND package_key=:key"), args).mappings().one_or_none()
            if old is None:
                connection.execute(text(
                    "INSERT ingestion.ftp_package(source_definition_id,package_key,relative_path,observed_fingerprint,file_count,total_bytes) "
                    "VALUES(:id,:key,:path,:fingerprint,:files,:size)"
                ), args)
                return False
            if old["job_id"] is not None:
                connection.execute(text("UPDATE ingestion.ftp_package SET last_observed_at_utc=SYSUTCDATETIME() WHERE source_definition_id=:id AND package_key=:key"), args)
                if old["submitted_fingerprint"] != package.fingerprint:
                    connection.execute(text("UPDATE ingestion.ftp_package SET status='CHANGED',error_code='FTP_SUBMITTED_SOURCE_CHANGED',error_message=N'已提交的源路径发生变化，请核实新文件版本；历史数据未自动更新',last_observed_at_utc=SYSUTCDATETIME() WHERE source_definition_id=:id AND package_key=:key"), args)
                return False
            if old["observed_fingerprint"] != package.fingerprint:
                connection.execute(text(
                    "UPDATE ingestion.ftp_package SET observed_fingerprint=:fingerprint,first_observed_at_utc=SYSUTCDATETIME(),"
                    "last_observed_at_utc=SYSUTCDATETIME(),status='WAITING',attempts=0,file_count=:files,total_bytes=:size,error_code=NULL,error_message=NULL "
                    "WHERE source_definition_id=:id AND package_key=:key"
                ), args)
                return False
            now = connection.execute(text("SELECT SYSUTCDATETIME()")).scalar_one().replace(tzinfo=UTC)
            connection.execute(text("UPDATE ingestion.ftp_package SET last_observed_at_utc=SYSUTCDATETIME() WHERE source_definition_id=:id AND package_key=:key"), args)
            return old["status"] != "FAILED" and old["attempts"] < 3 and package.old_enough(now, config.stable_seconds) and (now - old["first_observed_at_utc"].replace(tzinfo=UTC)).total_seconds() >= config.stable_seconds

    def package_failed(self, source_id, token, package, error):
        with self.engine.begin() as connection:
            self._lease(connection, source_id, token)
            connection.execute(text(
                "UPDATE ingestion.ftp_package SET attempts=attempts+1,status=CASE WHEN attempts+1>=3 THEN 'FAILED' ELSE 'RETRY' END,"
                "error_code=:code,error_message=:message WHERE source_definition_id=:id AND package_key=:key AND job_id IS NULL"
            ), dict(id=source_id, key=package.key, code=error.code, message=error.message[:500]))

    def submit(self, source_id, token, package, files, content_sha256):
        with self.engine.begin() as connection:
            leased = self._lease(connection, source_id, token)
            config = FtpSourceCreate.model_validate_json(leased["config_json"])
            binding = self._binding(connection, config)
            row = connection.execute(text("SELECT ftp_package_id,job_id,observed_fingerprint FROM ingestion.ftp_package WITH (UPDLOCK,HOLDLOCK) WHERE source_definition_id=:id AND package_key=:key"), dict(id=source_id, key=package.key)).mappings().one()
            if row["job_id"] is not None:
                return int(row["job_id"])
            if row["observed_fingerprint"] != package.fingerprint:
                raise DomainError("FTP_SOURCE_CHANGED", "采集清单已变化，未创建正式入库任务", 409)
            principal = Principal(binding["service_user_id"], "SYSTEM_INGESTION", "系统采集", (), frozenset())
            metadata = dict(ftp_source_id=source_id, ftp_package_id=row["ftp_package_id"], source_catalog=files[0].source_metadata)
            batch_id = int(connection.execute(text(
                "INSERT ingestion.import_batch(source_channel,uploaded_by,status,metadata_json,owner_user_id,business_domain,"
                "test_stage,factory_code,batch_name,remark,access_scope,data_domain_id,source_definition_id) OUTPUT INSERTED.import_batch_id "
                "VALUES('SOURCE_CATALOG','SYSTEM_INGESTION','QUEUED',:metadata,:owner,'ENGINEERING',:stage,:factory,:name,N'FTP 自动采集','DOMAIN',:domain,:source)"
            ), dict(metadata=_json(metadata), owner=principal.user_id, stage=config.test_stage, factory=config.factory_code.lower(),
                    name=package.path[-200:], domain=binding["data_domain_id"], source=source_id)).scalar_one())
            SqlStageDataService.register_files_in_transaction(connection, principal=principal, business_domain="ENGINEERING",
                stage_label=config.test_stage, factory_code=config.factory_code.lower(), files=files, source_channel="SOURCE_CATALOG", batch_id=batch_id)
            job = SqlJobService(self.engine)._create_with_connection(connection, CreateJobRequest(
                import_batch_id=batch_id, cleaner_release_id=config.cleaner_release_id, job_type=JobType.INITIAL_IMPORT,
                trigger_type=TriggerType.AUTO, requested_by="SYSTEM_INGESTION", requested_by_user_id=principal.user_id,
                reason="受控 FTP 完整快照进入正式入库", idempotency_key=f"ftp-package:{source_id}:{package.key}",
            ))
            connection.execute(text(
                "UPDATE ingestion.ftp_package SET status='SUBMITTED',import_batch_id=:batch,job_id=:job,content_sha256=:sha,"
                "submitted_fingerprint=:fingerprint,error_code=NULL,error_message=NULL WHERE ftp_package_id=:package"
            ), dict(batch=batch_id, job=job.job_id, sha=content_sha256, fingerprint=package.fingerprint, package=row["ftp_package_id"]))
            return job.job_id

    def finish(self, source_id, token, config, *, discovered, submitted, error=None):
        with self.engine.begin() as connection:
            connection.execute(text("SELECT source_definition_id FROM ingestion.ftp_collection_state WITH (UPDLOCK,HOLDLOCK) WHERE source_definition_id=:id"), dict(id=source_id)).one()
            args = dict(id=source_id, token=token, status="FAILED" if error else "SUCCESS", code=error.code if error else None,
                        message=error.message[:500] if error else None, discovered=discovered, submitted=submitted, interval=config.interval_seconds)
            connection.execute(text(
                "UPDATE ingestion.ftp_collection_run SET status=:status,finished_at_utc=SYSUTCDATETIME(),discovered_count=:discovered,"
                "submitted_count=:submitted,error_code=:code,error_message=:message WHERE lease_token=:token AND status='RUNNING'"
            ), args)
            connection.execute(text(
                "UPDATE ingestion.ftp_collection_state SET last_status=:status,last_finished_at_utc=SYSUTCDATETIME(),"
                "lease_token=NULL,lease_expires_at_utc=NULL,error_code=:code,error_message=:message,"
                "next_scan_at_utc=DATEADD(second,CASE WHEN :status='FAILED' THEN "
                "CASE WHEN :interval*(consecutive_failures+2)>3600 THEN 3600 ELSE :interval*(consecutive_failures+2) END ELSE :interval END,SYSUTCDATETIME()),"
                "consecutive_failures=CASE WHEN :status='FAILED' THEN consecutive_failures+1 ELSE 0 END "
                "WHERE source_definition_id=:id AND lease_token=:token"
            ), args)
