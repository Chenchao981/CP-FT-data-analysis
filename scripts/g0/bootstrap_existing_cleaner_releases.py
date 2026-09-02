from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class ExistingRelease:
    stage: str
    factory: str
    format_code: str
    cleaner_code: str
    package: Path
    adapter_code: str
    entrypoint: str
    input_contract: str
    output_contract: str
    cleaner_version: str | None = None
    timeout_seconds: int = 3600
    max_output_bytes: int = 10 * 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_package(package: Path, checksum: str, snapshot_root: Path) -> Path:
    """Copy a Cleaner package once into a content-addressed immutable location."""
    if _sha256(package) != checksum:
        raise RuntimeError(f"Cleaner package changed before snapshot: {package}")

    target_directory = snapshot_root.resolve() / checksum
    target_directory.mkdir(parents=True, exist_ok=True)
    target = target_directory / package.name
    if target.exists():
        if not target.is_file() or _sha256(target) != checksum:
            raise RuntimeError(f"Cleaner release snapshot checksum mismatch: {target}")
        return target.resolve()

    temporary = target_directory / f".{package.name}.{uuid4().hex}.tmp"
    try:
        with package.open("rb") as source, temporary.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if _sha256(temporary) != checksum:
            raise RuntimeError(f"Cleaner package changed while snapshotting: {package}")
        try:
            os.link(temporary, target)
        except FileExistsError:
            if not target.is_file() or _sha256(target) != checksum:
                raise RuntimeError(
                    f"Cleaner release snapshot checksum mismatch: {target}"
                )
    finally:
        temporary.unlink(missing_ok=True)

    if _sha256(target) != checksum:
        raise RuntimeError(f"Cleaner release snapshot checksum mismatch: {target}")
    return target.resolve()


def _definitions() -> tuple[ExistingRelease, ...]:
    cp_package = Path(
        os.getenv(
            "TMS_CP_CLEANER_PACKAGE",
            r"F:\cp_data_ansys\packaging\release\app.pyz",
        )
    ).resolve()
    ft_package = Path(
        os.getenv(
            "TMS_FT_CLEANER_PACKAGE",
            r"F:\data_IGBT_multiple\packaging\release\ft_data_cleaner.pyz",
        )
    ).resolve()
    return (
        ExistingRelease(
            "CP",
            "HUAHONG",
            "HUAHONG_DCP_EXISTING",
            "HUAHONG_CP_EXISTING",
            cp_package,
            "HUAHONG_CP_PYZ",
            "prepare_dcp_input -> clean_dcp_data.process_directory",
            "CP_ARCHIVE_OR_TXT_V1",
            "CP_CSV_TRIPLET_V1",
        ),
        ExistingRelease(
            "CP",
            "JETECH",
            "JETECH_CP_EXISTING",
            "JETECH_CP_EXISTING",
            cp_package,
            "JETECH_CP_PYZ",
            "jt_data_processor.jt_main_processor.process_jt_files",
            "CP_EXCEL_OR_ZIP_V1",
            "CP_STANDARD_CSV_TRIPLET_V1",
        ),
        ExistingRelease(
            "CP",
            "LION",
            "LION_CP_EXISTING",
            "LION_CP_EXISTING",
            cp_package,
            "LION_CP_PYZ",
            "lion_batch_processor.generate_lion_run_csvs",
            "CP_EXCEL_OR_ZIP_V1",
            "CP_STANDARD_CSV_TRIPLET_V1",
        ),
        ExistingRelease(
            "CP",
            "GUOYU",
            "GUOYU_FRD_CP_EXISTING",
            "GUOYU_FRD_CP_EXISTING",
            cp_package,
            "GUOYU_CP_PYZ",
            "guoyu_batch_processor.process_guoyu_directory",
            "CP_EXCEL_OR_ZIP_V1",
            "CP_STANDARD_CSV_TRIPLET_V1",
        ),
        ExistingRelease(
            "FT",
            "RIYUEXIN",
            "RIYUEXIN_DC_EXISTING",
            "RIYUEXIN_FT_EXISTING",
            ft_package,
            "RIYUEXIN_FT_PYZ",
            "factories.tms_adapters.riyuexin_dc.RiyuexinTmsDCCleaner.process_all_dc_files",
            "FT_DIRECTORY_XLSX_V1",
            "FT_XLSX_SCATTER_V1",
        ),
        ExistingRelease(
            "FT",
            "RIYUEGUANG",
            "RIYUEGUANG_DC_EXISTING",
            "RIYUEGUANG_FT_EXISTING",
            ft_package,
            "RIYUEGUANG_FT_PYZ",
            "factories.tms_adapters.riyueguang_dc.RiyueguangTmsDCCleaner.process_all_dc_files",
            "FT_DIRECTORY_XLSX_V1",
            "FT_XLSX_SCATTER_V1",
        ),
        ExistingRelease(
            "FT",
            "DIANJI",
            "DIANJI_POWERTECH_DYNAMIC_EXISTING",
            "DIANJI_FT_POWERTECH_EXISTING",
            ft_package,
            "DIANJI_FT_PYZ",
            "factories.dianji.dc_cleaner.DianjiDCCleaner.process_all via TMS manifest adapter",
            "DIANJI_POWERTECH_DIRECTORY_V1",
            "DIANJI_FT_SCATTER_V1",
            "v2.19.0",
        ),
        ExistingRelease(
            "FT",
            "JIEQUN",
            "JIEQUN_FT_QUICK_PAT_EXISTING",
            "JIEQUN_FT_QUICK_PAT_EXISTING",
            ft_package,
            "JIEQUN_FT_QUICK_PAT_PYZ",
            "factories.jiequn.pat_cleaner.generate_raw_pat",
            "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
            "FT_PAT_RESULT_V1",
            timeout_seconds=7200,
            max_output_bytes=64 * 1024 * 1024,
        ),
        ExistingRelease(
            "FT",
            "RIYUEXIN",
            "RIYUEXIN_FT_QUICK_PAT_EXISTING",
            "RIYUEXIN_FT_QUICK_PAT_EXISTING",
            ft_package,
            "RIYUEXIN_FT_QUICK_PAT_PYZ",
            "factories.riyuexin.pat_cleaner.generate_raw_pat",
            "RIYUEXIN_RAW_XLSX_DIRECTORY_V1",
            "FT_PAT_RESULT_V1",
            "v2.19.0-pat",
            timeout_seconds=7200,
            max_output_bytes=64 * 1024 * 1024,
        ),
        ExistingRelease(
            "FT",
            "DIANJI",
            "DIANJI_FT_QUICK_PAT_EXISTING",
            "DIANJI_FT_QUICK_PAT_EXISTING",
            ft_package,
            "DIANJI_FT_QUICK_PAT_PYZ",
            "factories.dianji.pat_cleaner.generate_raw_pat",
            "DIANJI_REGISTERED_RAW_DIRECTORY_V1",
            "FT_PAT_RESULT_V1",
            "v2.19.0-pat",
            timeout_seconds=7200,
            max_output_bytes=64 * 1024 * 1024,
        ),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register immutable snapshots for explicitly selected Cleaner factories"
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--factory",
        action="append",
        choices=(
            "HUAHONG",
            "JETECH",
            "LION",
            "GUOYU",
            "RIYUEXIN",
            "RIYUEGUANG",
            "DIANJI",
            "JIEQUN",
        ),
        help="Register only this factory; repeat to select more than one",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="Explicitly register every configured Cleaner definition",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    database_url = os.environ.get("TMS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    runtime = Path(
        os.getenv("TMS_CLEANER_PYTHON", r"D:\ProgramData\anaconda3\python.exe")
    ).resolve()
    snapshot_root = Path(
        os.getenv(
            "TMS_CLEANER_RELEASE_SNAPSHOT_ROOT",
            str(ROOT / "artifacts" / "cleaner_releases"),
        )
    ).resolve()
    if not runtime.is_file():
        raise FileNotFoundError(f"Cleaner Python runtime is unavailable: {runtime}")

    releases = _definitions()
    if not args.all:
        selected = set(args.factory or ())
        releases = tuple(item for item in releases if item.factory in selected)
    if not releases:
        raise RuntimeError("no Cleaner definitions were selected")
    for release in releases:
        if not release.package.is_file():
            raise FileNotFoundError(
                f"Cleaner package is unavailable: {release.package}"
            )

    engine = create_engine(database_url)
    registered: list[dict[str, object]] = []
    with engine.begin() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        if revision != "sql2014_0025":
            raise RuntimeError(f"sql2014_0025 is required, database is {revision}")
        approved_by = connection.execute(
            text(
                "SELECT TOP (1) u.user_id FROM iam.app_user u "
                "JOIN iam.user_role ur ON ur.user_id=u.user_id "
                "JOIN iam.role r ON r.role_id=ur.role_id "
                "WHERE r.role_code='SYSTEM_ADMIN' AND u.status='ACTIVE' "
                "ORDER BY u.user_id"
            )
        ).scalar_one_or_none()

        for definition in releases:
            checksum = _sha256(definition.package)
            artifact = _snapshot_package(
                definition.package,
                checksum,
                snapshot_root,
            )
            version = definition.cleaner_version or f"sha256-{checksum[:12]}"
            profile_id = connection.execute(
                text(
                    "SELECT format_profile_id FROM ingestion.format_profile "
                    "WHERE format_code=:format_code AND profile_version='route-a-v1'"
                ),
                {"format_code": definition.format_code},
            ).scalar_one_or_none()
            if profile_id is None:
                profile_id = connection.execute(
                    text(
                        "INSERT ingestion.format_profile("
                        "supplier_id,test_stage,factory_code,format_code,profile_version,"
                        "signature_json,file_role_contract_json,status,approved_by,approved_at_utc) "
                        "OUTPUT INSERTED.format_profile_id VALUES("
                        "NULL,:stage,:factory,:format_code,'route-a-v1',:signature,:roles,"
                        "'RELEASED',:approved_by,SYSUTCDATETIME())"
                    ),
                    {
                        "stage": definition.stage,
                        "factory": definition.factory,
                        "format_code": definition.format_code,
                        "signature": json.dumps(
                            {"selection": "explicit factory selection"},
                            ensure_ascii=False,
                        ),
                        "roles": json.dumps(
                            {
                                "input_contract": definition.input_contract,
                                "output_contract": definition.output_contract,
                            },
                            ensure_ascii=False,
                        ),
                        "approved_by": approved_by,
                    },
                ).scalar_one()
            else:
                connection.execute(
                    text(
                        "UPDATE ingestion.format_profile SET factory_code=:factory,"
                        "test_stage=:stage,status='RELEASED' WHERE format_profile_id=:profile_id"
                    ),
                    {
                        "factory": definition.factory,
                        "stage": definition.stage,
                        "profile_id": profile_id,
                    },
                )

            release_row = (
                connection.execute(
                    text(
                        "SELECT cleaner_release_id,code_checksum,status,artifact_uri,"
                        "runtime_uri,entrypoint,adapter_code,input_contract_version,"
                        "output_contract_version,execution_config_json,timeout_seconds,"
                        "max_output_bytes FROM ingestion.cleaner_release "
                        "WHERE cleaner_code=:cleaner_code AND cleaner_version=:version "
                        "AND format_profile_id=:profile_id"
                    ),
                    {
                        "cleaner_code": definition.cleaner_code,
                        "version": version,
                        "profile_id": profile_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            release_id = (
                int(release_row["cleaner_release_id"])
                if release_row is not None
                else None
            )
            if (
                release_row is not None
                and str(release_row["code_checksum"] or "").lower() != checksum
            ):
                raise RuntimeError(
                    f"Cleaner version checksum collision: {definition.cleaner_code}/{version}"
                )
            values = {
                "profile_id": profile_id,
                "cleaner_code": definition.cleaner_code,
                "version": version,
                "checksum": checksum,
                "artifact_uri": str(artifact),
                "runtime_uri": str(runtime),
                "entrypoint": definition.entrypoint,
                "adapter_code": definition.adapter_code,
                "input_contract": definition.input_contract,
                "output_contract": definition.output_contract,
                "config": json.dumps(
                    {
                        "factory_code": definition.factory,
                        **(
                            {"outlier_method": "iqr", "convert_units": True}
                            if definition.stage == "CP"
                            else {}
                        ),
                    },
                    ensure_ascii=False,
                ),
                "timeout_seconds": definition.timeout_seconds,
                "max_output_bytes": definition.max_output_bytes,
                "approved_by": approved_by,
            }
            if release_id is None:
                release_id = connection.execute(
                    text(
                        "INSERT ingestion.cleaner_release("
                        "format_profile_id,cleaner_code,cleaner_version,code_checksum,"
                        "artifact_uri,status,approved_by,approved_at_utc,runtime_uri,entrypoint,"
                        "adapter_code,input_contract_version,output_contract_version,"
                        "execution_config_json,timeout_seconds,max_output_bytes) "
                        "OUTPUT INSERTED.cleaner_release_id VALUES("
                        ":profile_id,:cleaner_code,:version,:checksum,:artifact_uri,'RELEASED',"
                        ":approved_by,SYSUTCDATETIME(),:runtime_uri,:entrypoint,:adapter_code,"
                        ":input_contract,:output_contract,:config,:timeout_seconds,"
                        ":max_output_bytes)"
                    ),
                    values,
                ).scalar_one()
            else:
                expected_contract = {
                    "status": "RELEASED",
                    "artifact_uri": values["artifact_uri"],
                    "runtime_uri": values["runtime_uri"],
                    "entrypoint": values["entrypoint"],
                    "adapter_code": values["adapter_code"],
                    "input_contract_version": values["input_contract"],
                    "output_contract_version": values["output_contract"],
                    "timeout_seconds": values["timeout_seconds"],
                    "max_output_bytes": values["max_output_bytes"],
                }
                mismatches = [
                    field
                    for field, expected in expected_contract.items()
                    if (
                        int(release_row[field])
                        if field in {"timeout_seconds", "max_output_bytes"}
                        else str(release_row[field] or "")
                    )
                    != expected
                ]
                try:
                    stored_config = json.loads(
                        str(release_row["execution_config_json"] or "null")
                    )
                    expected_config = json.loads(str(values["config"]))
                except json.JSONDecodeError:
                    mismatches.append("execution_config_json")
                else:
                    if stored_config != expected_config:
                        mismatches.append("execution_config_json")
                if mismatches:
                    raise RuntimeError(
                        "Published Cleaner Release is immutable; register a new "
                        f"version instead (release_id={release_id}, fields="
                        + ",".join(sorted(set(mismatches)))
                        + ")"
                    )
            registered.append(
                {
                    "cleaner_release_id": int(release_id),
                    "stage": definition.stage,
                    "factory": definition.factory,
                    "version": version,
                    "artifact_uri": str(artifact),
                    "output_contract": definition.output_contract,
                    "timeout_seconds": definition.timeout_seconds,
                    "max_output_bytes": definition.max_output_bytes,
                }
            )
    print(json.dumps({"registered": registered}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
