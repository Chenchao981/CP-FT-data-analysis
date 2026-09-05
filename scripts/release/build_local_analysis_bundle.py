"""Build a relocatable personal-computer runner bundle without credentials or source data."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def build(package: Path, output: Path, origin: str) -> None:
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            "origin must be an exact TMS HTTP(S) origin without credentials"
        )
    if not package.is_file() or package.suffix.lower() != ".pyz":
        raise ValueError("an existing official FT PYZ package is required")
    if output.exists():
        raise FileExistsError("choose a new output path; bundles are not overwritten")
    with package.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "allowed_origins": [origin.rstrip("/")],
        "ft_package": "ft_data_cleaner.pyz",
        "ft_package_sha256": digest,
        "python_runtime": "D:\\ProgramData\\anaconda3\\python.exe",
    }
    launcher = """@echo off
cd /d "%~dp0"
if not defined TMS_LOCAL_AGENT_PYTHON set "TMS_LOCAL_AGENT_PYTHON=D:\\ProgramData\\anaconda3\\python.exe"
"%TMS_LOCAL_AGENT_PYTHON%" -m local_agent --config config.json --validate-only --check-runtime
if errorlevel 1 goto failed
"%TMS_LOCAL_AGENT_PYTHON%" -m local_agent --config config.json
:failed
pause
"""
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / "local_agent").glob("*.py")):
            bundle.write(path, f"local_agent/{path.name}")
        bundle.write(package, "ft_data_cleaner.pyz")
        bundle.writestr("config.json", json.dumps(config, indent=2))
        bundle.writestr("start.cmd", launcher.replace("\n", "\r\n"))
        bundle.writestr(
            "README.txt",
            "解压到员工电脑；需要已安装的 Anaconda 及 numpy、pandas、fastapi、uvicorn、openpyxl。\n管理员核对 config.json 的网页地址与 Python 路径，再双击 start.cmd。\n打开 TMS 个人分析工具中的个人电脑页，输入启动窗口连接码，选择杰群统一 CSV 目录。\n当前只交付已接入的杰群原始目录 PAT；其他格式不会自动启用。\n此包不包含账号、源数据、运行日志或配对令牌。\n",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    build(args.package.resolve(), args.output.resolve(), args.origin)
    print(json.dumps({"output": str(args.output), "status": "BUILT"}))
