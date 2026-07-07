from ..webapp_core import MiniAppAdapter, MiniAppAdapterRegistry
from .cave_treasure_miniapp import build_cave_treasure_miniapp_adapter, build_cave_treasure_miniapp_flow_plan
from .fishing_miniapp import build_fishing_miniapp_adapter, build_fishing_miniapp_flow_plan
from .stargazer_miniapp import build_stargazer_miniapp_adapter, build_stargazer_miniapp_flow_plan
from .tree_miniapp import build_tree_miniapp_adapter, build_tree_miniapp_flow_plan
from .trial_miniapp import build_trial_miniapp_adapter, build_trial_miniapp_flow_plan


DEFAULT_GAME_BOT_USERNAME = "fanrenxiuxian_bot"


def build_world_boss_miniapp_adapter(*, bot_username=DEFAULT_GAME_BOT_USERNAME):
    return MiniAppAdapter(
        game_key="world_boss",
        label="真仙试锋",
        bot_username=bot_username,
        allowed_web_hosts=("t.me", "telegram.me"),
        default_enabled=False,
        manual_only=True,
    )


def build_known_miniapp_registry():
    return MiniAppAdapterRegistry((
        build_cave_treasure_miniapp_adapter(),
        build_fishing_miniapp_adapter(),
        build_trial_miniapp_adapter(),
        build_world_boss_miniapp_adapter(),
        build_stargazer_miniapp_adapter(),
        build_tree_miniapp_adapter(),
    ))


def build_known_miniapp_flow_plans():
    return {
        "cave_treasure": build_cave_treasure_miniapp_flow_plan(),
        "fishing": build_fishing_miniapp_flow_plan(),
        "stargazer": build_stargazer_miniapp_flow_plan(),
        "tree": build_tree_miniapp_flow_plan(),
        "trial": build_trial_miniapp_flow_plan(),
    }


__all__ = [
    "build_known_miniapp_flow_plans",
    "build_known_miniapp_registry",
    "build_cave_treasure_miniapp_adapter",
    "build_stargazer_miniapp_adapter",
    "build_tree_miniapp_adapter",
    "build_trial_miniapp_adapter",
    "build_world_boss_miniapp_adapter",
]
