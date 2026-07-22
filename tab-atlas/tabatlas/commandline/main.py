from __future__ import annotations

from .context import CommandContext
from .handlers import COMMAND_HANDLERS
from .parser import build_parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    context = CommandContext.create(args)
    try:
        handler = COMMAND_HANDLERS.get(args.command)
        if handler is not None:
            return handler(context)
    finally:
        context.close_quietly()
    parser.error("Unsupported command")
    return 2
