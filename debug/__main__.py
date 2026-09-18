from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from debug.collector import CollectorState, create_app
from debug.i18n import gettext_debug as _
from debug.supervisor import DebugConfigError, DebugLaunchError, run_supervisor


def loopback_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError(_("port must be between 1 and 65535"))
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=_("Run the local Sophie development debugger"))
    parser.add_argument("--config", default="data/debug.env", help=_("Dedicated development dotenv file"))
    parser.add_argument("--port", type=loopback_port, default=8079, help=_("Collector API loopback port"))
    parser.add_argument("--ui-port", type=loopback_port, default=5174, help=_("Vite loopback port"))
    parser.add_argument("--no-open", action="store_true", help=_("Do not open a browser"))
    parser.add_argument("--export-openapi", type=Path, help=_("Write the debugger OpenAPI schema and exit"))
    return parser


def export_openapi(path: Path) -> None:
    state = CollectorState(
        session_id="schema",
        bearer_token="schema",
        browser_credential="schema",
        csrf_token="schema",
        api_origin="http://127.0.0.1:8079",
        ui_origin="http://127.0.0.1:5174",
        sanitized_targets={},
    )
    app = create_app(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = build_parser().parse_args()
    if args.export_openapi is not None:
        export_openapi(args.export_openapi)
        return
    if args.port == args.ui_port:
        raise SystemExit(_("Collector and UI ports must differ"))
    try:
        asyncio.run(
            run_supervisor(
                config_path=args.config,
                port=args.port,
                ui_port=args.ui_port,
                open_browser=not args.no_open,
            )
        )
    except (DebugConfigError, DebugLaunchError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
