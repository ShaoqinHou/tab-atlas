from __future__ import annotations

from collections.abc import Callable

from .browser_commands import BROWSER_HANDLERS
from .catalog_commands import CATALOG_HANDLERS
from .context import CommandContext
from .mutation_commands import MUTATION_HANDLERS
from .server_commands import SERVER_HANDLERS


CommandHandler = Callable[[CommandContext], int]

COMMAND_HANDLERS: dict[str, CommandHandler] = {
    **CATALOG_HANDLERS,
    **BROWSER_HANDLERS,
    **MUTATION_HANDLERS,
    **SERVER_HANDLERS,
}
