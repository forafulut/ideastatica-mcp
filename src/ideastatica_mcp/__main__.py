"""Command line entry point: ``python -m ideastatica_mcp`` or ``ideastatica-mcp``."""

from __future__ import annotations

import argparse
import os
import sys

from ideastatica_mcp.config import ENV_PREFIX


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ideastatica-mcp", description="MCP server for IDEA StatiCa Connection")
    parser.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="host for HTTP transports")
    parser.add_argument("--port", type=int, default=8765, help="port for HTTP transports")
    parser.add_argument("--setup-dir", help="IDEA StatiCa installation folder (IDEASTATICA_SETUP_DIR)")
    parser.add_argument("--api-url", help="attach to a running service instead of starting one (IDEASTATICA_API_URL)")
    parser.add_argument("--service-port", type=int, help="port to start the service on (IDEASTATICA_PORT)")
    parser.add_argument("--no-autostart", action="store_true", help="never start the service automatically")
    parser.add_argument("--output-dir", help="folder for generated files (IDEASTATICA_OUTPUT_DIR)")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR (IDEASTATICA_LOG_LEVEL)")
    return parser


def apply_cli_overrides(args: argparse.Namespace) -> None:
    mapping = {
        "SETUP_DIR": args.setup_dir,
        "API_URL": args.api_url,
        "PORT": str(args.service_port) if args.service_port else None,
        "OUTPUT_DIR": args.output_dir,
        "LOG_LEVEL": args.log_level,
    }
    if args.no_autostart:
        mapping["AUTOSTART"] = "false"
    for key, value in mapping.items():
        if value:
            os.environ[ENV_PREFIX + key] = value


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    apply_cli_overrides(args)

    from ideastatica_mcp.server import build_server

    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run(args.transport, host=args.host, port=args.port)  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
