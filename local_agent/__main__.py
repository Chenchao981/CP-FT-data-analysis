from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

import uvicorn

from .app import create_app
from .config import AgentConfig
from .runner import cp_capability_gate, ft_jiequn_capability


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the user-scoped TMS Local Agent on 127.0.0.1 only."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="JSON configuration path (or set TMS_LOCAL_AGENT_CONFIG).",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate configuration and tool package, then exit.",
    )
    args = parser.parse_args()
    config_path = args.config
    if config_path is None:
        configured = os.getenv("TMS_LOCAL_AGENT_CONFIG", "").strip()
        config_path = Path(configured) if configured else None
    config = AgentConfig.from_json(config_path) if config_path else AgentConfig.defaults()
    if args.validate_only:
        print(
            json.dumps(
                {
                    "valid": True,
                    "bind_host": config.bind_host,
                    "port": config.port,
                    "tools": [
                        ft_jiequn_capability(config).public_dict(),
                        cp_capability_gate().public_dict(),
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    pairing_token = secrets.token_urlsafe(32)
    app = create_app(config, pairing_token=pairing_token)
    print(
        json.dumps(
            {
                "event": "TMS_LOCAL_AGENT_READY",
                "base_url": f"http://127.0.0.1:{config.port}",
                "pairing_token": pairing_token,
                "pairing_token_ttl_seconds": config.pairing_token_ttl_seconds,
                "note": "请仅将本次启动令牌填入可信的 TMS 快速分析页面",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    uvicorn.run(
        app,
        host=config.bind_host,
        port=config.port,
        access_log=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
