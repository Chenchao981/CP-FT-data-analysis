from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.cleaner_capabilities import validate_capability_contract

EXPECTED_SCHEMA_REVISION = "sql2014_0025"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a DRAFT Cleaner Release and explicitly promote it"
    )
    parser.add_argument("--release-id", required=True, type=int)
    parser.add_argument(
        "--expected-sha256",
        required=True,
        help="Approved 64-character package SHA-256",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Change DRAFT to RELEASED; omission performs a read-only validation",
    )
    args = parser.parse_args(argv)
    expected = str(args.expected_sha256).strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        parser.error("--expected-sha256 must contain exactly 64 hexadecimal characters")
    if args.release_id <= 0:
        parser.error("--release-id must be positive")
    args.expected_sha256 = expected
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    database_url = os.getenv("TMS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != EXPECTED_SCHEMA_REVISION:
            raise RuntimeError(
                f"{EXPECTED_SCHEMA_REVISION} is required, database is {revision}"
            )
        row = (
            connection.execute(
                text(
                    "SELECT cr.cleaner_release_id,cr.cleaner_code,cr.cleaner_version,"
                    "cr.code_checksum,cr.artifact_uri,cr.status,cr.adapter_code,"
                    "cr.input_contract_version,cr.output_contract_version,"
                    "cr.execution_config_json,"
                    "fp.test_stage,fp.factory_code,fp.format_code,fp.status AS profile_status "
                    "FROM ingestion.cleaner_release cr WITH (UPDLOCK,HOLDLOCK) "
                    "JOIN ingestion.format_profile fp WITH (HOLDLOCK) "
                    "ON fp.format_profile_id=cr.format_profile_id "
                    "WHERE cr.cleaner_release_id=:release_id"
                ),
                {"release_id": args.release_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise RuntimeError(f"Cleaner Release {args.release_id} does not exist")
        if str(row["status"]) != "DRAFT":
            raise RuntimeError(
                f"Cleaner Release {args.release_id} is not DRAFT: {row['status']}"
            )
        if str(row["profile_status"]) != "RELEASED":
            raise RuntimeError("Cleaner Format Profile is not RELEASED")
        registered_sha = str(row["code_checksum"] or "").lower()
        if registered_sha != args.expected_sha256:
            raise RuntimeError("Cleaner Release checksum differs from approved SHA-256")
        artifact = Path(str(row["artifact_uri"])).resolve()
        if not artifact.is_file():
            raise FileNotFoundError(f"Cleaner Release artifact is unavailable: {artifact}")
        actual_sha = _sha256(artifact)
        if actual_sha != registered_sha:
            raise RuntimeError("Cleaner Release artifact checksum verification failed")
        validate_capability_contract(
            adapter_code=str(row["adapter_code"]),
            test_stage=str(row["test_stage"]),
            factory_code=str(row["factory_code"]),
            cleaner_code=str(row["cleaner_code"]),
            input_contract_version=str(row["input_contract_version"]),
            output_contract_version=str(row["output_contract_version"]),
            execution_config_json=row["execution_config_json"],
        )

        approved_by = None
        if args.promote:
            approved_by = connection.execute(
                text(
                    "SELECT TOP (1) u.user_id FROM iam.app_user u "
                    "JOIN iam.user_role ur ON ur.user_id=u.user_id "
                    "JOIN iam.role r ON r.role_id=ur.role_id "
                    "WHERE r.role_code='SYSTEM_ADMIN' AND u.status='ACTIVE' "
                    "ORDER BY u.user_id"
                )
            ).scalar_one_or_none()
            if approved_by is None:
                raise RuntimeError("No active SYSTEM_ADMIN can approve the Cleaner Release")
            updated = connection.execute(
                text(
                    "UPDATE ingestion.cleaner_release SET status='RELEASED',"
                    "approved_by=:approved_by,approved_at_utc=SYSUTCDATETIME() "
                    "WHERE cleaner_release_id=:release_id AND status='DRAFT'"
                ),
                {"release_id": args.release_id, "approved_by": approved_by},
            ).rowcount
            if updated != 1:
                raise RuntimeError("Cleaner Release promotion lost its DRAFT lock")

    print(
        json.dumps(
            {
                "cleaner_release_id": args.release_id,
                "cleaner_code": str(row["cleaner_code"]),
                "cleaner_version": str(row["cleaner_version"]),
                "test_stage": str(row["test_stage"]),
                "factory_code": str(row["factory_code"]),
                "format_code": str(row["format_code"]),
                "adapter_code": str(row["adapter_code"]),
                "input_contract_version": str(row["input_contract_version"]),
                "output_contract_version": str(row["output_contract_version"]),
                "sha256": actual_sha,
                "result": "PROMOTED" if args.promote else "VALIDATED_DRAFT",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
