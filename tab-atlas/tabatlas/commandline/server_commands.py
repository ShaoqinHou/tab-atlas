from __future__ import annotations

import sys
import webbrowser
from collections.abc import Callable

from ..database import workspace_access_token
from ..presentation import generate_report
from ..report_server import create_report_server
from ..workspace import create_workspace_server
from .context import CommandContext
from .formatting import output
from .paths import REPORT_ASSETS, ROOT


def view(context: CommandContext) -> int:
    output_dir = context.args.output.resolve()
    report_path = generate_report(
        context.connection,
        output_dir,
        REPORT_ASSETS,
        context.state_dir,
    )
    context.close()
    server, url = create_report_server(report_path.parent, context.args.port)
    output({"report": str(report_path), "url": url, "readOnly": True})
    sys.stdout.flush()
    if context.args.open_report:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def workspace(context: CommandContext) -> int:
    output_dir = context.args.output.resolve()
    if context.args.rotate_access:
        workspace_access_token(context.connection, rotate=True)
    report_path = generate_report(
        context.connection,
        output_dir,
        REPORT_ASSETS,
        context.state_dir,
    )
    context.close()
    server, url = create_workspace_server(
        ROOT,
        context.state_dir,
        context.database_path,
        report_path.parent,
        REPORT_ASSETS,
        context.args.port,
    )
    browser_sync = server.start_browser_sync(trigger="workspace_start")
    output(
        {
            "report": str(report_path),
            "url": url,
            "readOnly": False,
            "agent": "on_demand_chatgpt_login",
            "browserSync": browser_sync,
        }
    )
    sys.stdout.flush()
    if context.args.open_report:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


SERVER_HANDLERS: dict[str, Callable[[CommandContext], int]] = {
    "view": view,
    "workspace": workspace,
}
