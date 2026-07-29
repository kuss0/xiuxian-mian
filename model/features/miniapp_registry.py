from ..webapp_core import MiniAppAdapterRegistry
from .cave_treasure_miniapp import build_cave_treasure_miniapp_adapter, build_cave_treasure_miniapp_flow_plan
from .fate_cards_miniapp import build_fate_cards_miniapp_adapter, build_fate_cards_miniapp_flow_plan
from .fishing_miniapp import build_fishing_miniapp_adapter, build_fishing_miniapp_flow_plan
from .stargazer_miniapp import build_stargazer_miniapp_adapter, build_stargazer_miniapp_flow_plan
from .spirit_beast_miniapp import build_spirit_beast_miniapp_adapter, build_spirit_beast_miniapp_flow_plan
from .tree_miniapp import build_tree_miniapp_adapter, build_tree_miniapp_flow_plan
from .trial_miniapp import build_trial_miniapp_adapter, build_trial_miniapp_flow_plan
from .tower_miniapp import build_tower_miniapp_adapter, build_tower_miniapp_flow_plan
from .world_boss_miniapp import (
    build_nangongque_miniapp_adapter,
    build_nangongque_miniapp_flow_plan,
    build_world_boss_miniapp_adapter,
    build_world_boss_miniapp_flow_plan,
)


def build_known_miniapp_registry():
    return MiniAppAdapterRegistry((
        build_cave_treasure_miniapp_adapter(),
        build_fate_cards_miniapp_adapter(),
        build_fishing_miniapp_adapter(),
        build_trial_miniapp_adapter(),
        build_world_boss_miniapp_adapter(),
        build_nangongque_miniapp_adapter(),
        build_spirit_beast_miniapp_adapter(),
        build_stargazer_miniapp_adapter(),
        build_tree_miniapp_adapter(),
        build_tower_miniapp_adapter(),
    ))


def build_known_miniapp_flow_plans():
    return {
        "cave_treasure": build_cave_treasure_miniapp_flow_plan(),
        "fate_cards": build_fate_cards_miniapp_flow_plan(),
        "fishing": build_fishing_miniapp_flow_plan(),
        "stargazer": build_stargazer_miniapp_flow_plan(),
        "tree": build_tree_miniapp_flow_plan(),
        "trial": build_trial_miniapp_flow_plan(),
        "world_boss": build_world_boss_miniapp_flow_plan(),
        "world_boss_nangongque": build_nangongque_miniapp_flow_plan(),
        "spirit_beast": build_spirit_beast_miniapp_flow_plan(),
        "tower": build_tower_miniapp_flow_plan(),
    }


__all__ = [
    "build_known_miniapp_flow_plans",
    "build_known_miniapp_registry",
    "build_cave_treasure_miniapp_adapter",
    "build_fate_cards_miniapp_adapter",
    "build_stargazer_miniapp_adapter",
    "build_tree_miniapp_adapter",
    "build_trial_miniapp_adapter",
    "build_nangongque_miniapp_adapter",
    "build_spirit_beast_miniapp_adapter",
    "build_spirit_beast_miniapp_flow_plan",
    "build_world_boss_miniapp_adapter",
    "build_tower_miniapp_adapter",
]
