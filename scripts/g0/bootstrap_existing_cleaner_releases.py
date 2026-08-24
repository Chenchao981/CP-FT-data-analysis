from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, text


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
            "factories.riyuexin.dc_cleaner.DCDataCleaner.process_all_dc_files",
            "FT_DIRECTORY_XLSX_V1",
            "FT_XLSX_SCATTER_V1",
        ),
    )


def main() -> None:
    database_url = os.environ.get("TMS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    runtime = Path(
        os.getenv("TMS_CLEANER_PYTHON", r"D:\ProgramData\anaconda3\python.exe")
    ).resolve()
    if not runtime.is_file():
        raise FileNotFoundError(f"Cleaner Python runtime is unavailable: {runtime}")

    releases = _definitions()
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
        if revision != "sql2014_0011":
            raise RuntimeError(f"sql2014_0011 is required, database is {revision}")
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
            version = f"sha256-{checksum[:12]}"
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

            release_id = connection.execute(
                text(
                    "SELECT cleaner_release_id FROM ingestion.cleaner_release "
                    "WHERE cleaner_code=:cleaner_code AND cleaner_version=:version "
                    "AND format_profile_id=:profile_id"
                ),
                {
                    "cleaner_code": definition.cleaner_code,
                    "version": version,
                    "profile_id": profile_id,
                },
            ).scalar_one_or_none()
            values = {
                "profile_id": profile_id,
                "cleaner_code": definition.cleaner_code,
                "version": version,
                "checksum": checksum,
                "artifact_uri": str(definition.package),
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
                "approved_by": approved_by,
            }
            if release_id is None:
                release_id = connection.execute(
                    text(
                        "INSERT ingestion.cleaner_release("
                        "format_profile_id,cleaner_code,cleaner_version,code_checksum,"
                        "artifact_uri,status,approved_by,approved_at_utc,runtime_uri,entrypoint,"
                        "adapter_code,input_contract_version,output_contract_version,"
                        "execution_config_json) OUTPUT INSERTED.cleaner_release_id VALUES("
                        ":profile_id,:cleaner_code,:version,:checksum,:artifact_uri,'RELEASED',"
                        ":approved_by,SYSUTCDATETIME(),:runtime_uri,:entrypoint,:adapter_code,"
                        ":input_contract,:output_contract,:config)"
                    ),
                    values,
                ).scalar_one()
            registered.append(
                {
                    "cleaner_release_id": int(release_id),
                    "stage": definition.stage,
                    "factory": definition.factory,
                    "version": version,
                    "output_contract": definition.output_contract,
                }
            )
    print(json.dumps({"registered": registered}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
