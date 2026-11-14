"""玄骨所需的窄接口；不依赖应用装配入口。"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .expedition_store import FubenFlowCollection


AsyncCall = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class XuanguMessages:
    game_command: AsyncCall
    audit: AsyncCall
    group_message: AsyncCall
    notice_by_chat: AsyncCall
    listener_account_id: Callable[[Any], int]
    is_listener_self: Callable[[Any, int], bool]
    group_ids: Callable[[], list[int]]
    definitely_unsent: Callable[[int, str], bool] = lambda _identity, _command: False


@dataclass(frozen=True)
class XuanguIdentities:
    resolve_selector: Callable[[str], int | None]
    participation_enabled: Callable[[int, str], bool]
    preflight_error: Callable[[int], str] = lambda _identity: ""


@dataclass(frozen=True)
class XuanguCommands:
    open: str
    join: str
    enter: str
    act: str
    finish: str
    disband: str
    role: str


@dataclass(frozen=True)
class XuanguContext:
    flows: FubenFlowCollection
    messages: XuanguMessages
    identities: XuanguIdentities
    commands: XuanguCommands
    sleep: Callable[[float], Awaitable[Any]]
    spawn: Callable[[Awaitable[Any]], Any]
    finish_choice: str = "收拢遗宝"
    target: Callable[[], tuple[int, int]] | None = None
