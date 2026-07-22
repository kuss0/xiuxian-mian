"""Declarative command-surface catalog for MiniApp migration governance."""

from __future__ import annotations

from dataclasses import dataclass


CATALOG_VERSION = "2026-07-22"


@dataclass(frozen=True)
class CommandGroup:
    category: str
    key: str
    label: str
    commands: tuple[str, ...]

    def safe_summary(self):
        return {
            "category": self.category,
            "key": self.key,
            "label": self.label,
            "commands": list(self.commands),
            "command_count": len(self.commands),
        }


CATEGORY_META = (
    ("integrated", "已整合", "已进入统一 MiniApp 信息架构；本目录只描述归属，不代表本地已自动化所有动作。"),
    ("sect_locked", "锁定（宗门专属）", "保留宗门资格边界，不能因为进入统一页面而绕过宗门校验。"),
    ("external_miniapp", "外部入口（Mini App）", "独立外部 MiniApp 入口；自动化支持度需逐项验证。"),
    ("pending_migration", "待迁移", "尚未纳入统一 MiniApp 的候选命令。"),
    ("chat_preserved", "群内保留", "明确继续保留群内交互，不应被普通 MiniApp 迁移批量替代。"),
)


COMMAND_GROUPS = (
    CommandGroup("integrated", "identity", "状态/灵根/战力", (".状态", ".我的灵根", ".战力")),
    CommandGroup("integrated", "retreat", "闭关修炼", (".闭关修炼", ".深度闭关", ".查看闭关")),
    CommandGroup("integrated", "alchemy", "丹房炼丹", (".丹药", ".丹方", ".炼制")),
    CommandGroup("integrated", "inventory_forge", "储物袋/法宝/炼器", (".储物袋", ".学习", ".法宝", ".炼器", ".修理", ".一键修理")),
    CommandGroup("integrated", "formation_seclusion", "阵法/隐修/御宝", (".我的阵法", ".布阵", ".撤阵", ".吐纳养法", ".避世", ".入世")),
    CommandGroup("integrated", "dwelling", "洞府管理", (".洞府", ".开辟洞府", ".升级灵脉", ".布置景观", ".洞府留言")),
    CommandGroup("integrated", "sect", "宗门/宝库", (".我的宗门", ".宗门列表", ".宗门点卯", ".宗门宝库", ".兑换")),
    CommandGroup("integrated", "star_palace", "星宫天机", (".观星", ".侍妾卜算", ".改换星移", ".启阵", ".助阵", ".晋升星宫长老")),
    CommandGroup("integrated", "yuanying_wendao", "本命元婴/问道", (".元婴状态", ".元婴闭关", ".元婴出窍", ".元婴归窍", ".问道")),
    CommandGroup("integrated", "small_world", "紫府小世界", (".小世界", ".收割香火", ".显灵", ".神迹", ".升级神庙", ".神识淬炼")),
    CommandGroup("integrated", "pet_puppet", "灵兽/傀儡/大衍诀", (".我的灵兽", ".寻觅灵兽", ".大衍诀", ".傀儡", ".制造傀儡", ".傀儡护法")),
    CommandGroup("integrated", "social", "侍妾/道侣/社交", (".我的侍妾", ".红尘寻缘", ".入梦", ".远航状态", ".请侍妾护法", ".婉影")),
    CommandGroup("integrated", "sword", "青元剑诀/剑阵", (".合成剑诀", ".参悟剑诀", ".炼剑", ".参悟剑阵", ".布下剑阵")),
    CommandGroup("integrated", "tower_dummy", "琉璃塔/木人阁", (".闯塔", ".继续闯塔", ".重置古塔", ".琉璃塔榜", ".切磋木人")),
    CommandGroup("integrated", "ranking", "榜单", (".排行榜", ".修为榜", ".灵石榜", ".恶人榜", ".冤魂榜", ".琉璃塔榜")),
    CommandGroup("sect_locked", "tianxing", "天星宗司命盘", (".天星宗帮助", ".天机", ".观命", ".定命", ".推命", ".改命", ".天机盘", ".消劫")),
    CommandGroup("sect_locked", "lingxiao", "凌霄宫云阶", (".凌霄宫", ".天阶状态", ".问心台", ".登天阶", ".引九天罡风", ".借天门势", ".晋升凌霄长老", ".晋升凌霄太上长老")),
    CommandGroup("sect_locked", "yinluo_banner", "阴罗宗阴罗幡", (".我的阴罗幡", ".升级阴罗幡", ".每日献祭", ".囚禁魂魄", ".收取精华", ".血洗山林", ".召唤魔影", ".化功为煞")),
    CommandGroup("sect_locked", "heart_seal", "阴罗/合欢心印", (".我的炉鼎", ".魔音灌脑", ".强索元阴", ".挣脱心印")),
    CommandGroup("external_miniapp", "sect_garden", "宗门灵圃", (".小药园", ".观星台", ".灵树")),
    CommandGroup("external_miniapp", "outer_games", "外府游戏", (".钓鱼", ".天机试炼", ".诸天杯", ".2048", ".修仙路", ".鬼赌坊")),
    CommandGroup("external_miniapp", "world_boss", "世界 Boss", (".世界boss",)),
    CommandGroup("pending_migration", "breakthrough", "进阶突破", (".筑基", ".结丹", ".凝结元婴", ".化神准备", ".突破化神")),
    CommandGroup("pending_migration", "sect_mission", "宗门任务/外交", (".宗门任务", ".提交任务", ".宗门外交")),
    CommandGroup("pending_migration", "market", "万宝楼/拍卖", (".万宝楼", ".上架", ".购买", ".拍卖", ".竞拍")),
    CommandGroup("chat_preserved", "replica", "多人副本/秘境", (".虚天殿", ".昆吾山", ".坠魔谷", ".黄龙山", ".血色试炼")),
    CommandGroup("chat_preserved", "duel", "斗法/对决", (".斗法", ".决斗", ".对决", ".神识对决")),
    CommandGroup("chat_preserved", "high_risk_social", "夺舍/咒术/双修", (".夺舍", ".下咒", ".血咒四方一天世界", ".闭关双修", ".缔结同参", ".结印", ".双修 温养", ".双修 采补", ".种下心印")),
    CommandGroup("chat_preserved", "gambling", "赌坊/牌局", (".鬼赌坊", ".德州局", ".对赌", ".应战", ".开盘", ".跟注")),
    CommandGroup("chat_preserved", "admin", "管理命令", (".洞府入口", ".admin")),
)


ALLOWED_MULTI_SURFACE = {
    ".鬼赌坊": ("external_miniapp", "chat_preserved"),
}


def _normalize_command(value):
    return " ".join(str(value or "").strip().split())


def _catalog_occurrences():
    occurrences = {}
    for group in COMMAND_GROUPS:
        for command in group.commands:
            normalized = _normalize_command(command)
            occurrences.setdefault(normalized, []).append({
                "category": group.category,
                "group_key": group.key,
                "group_label": group.label,
            })
    return occurrences


def build_command_catalog_snapshot():
    category_rows = []
    for key, label, note in CATEGORY_META:
        groups = [group.safe_summary() for group in COMMAND_GROUPS if group.category == key]
        category_rows.append({
            "key": key,
            "label": label,
            "note": note,
            "groups": groups,
            "group_count": len(groups),
            "command_count": sum(group["command_count"] for group in groups),
        })
    occurrences = _catalog_occurrences()
    multi_group_commands = sorted(command for command, rows in occurrences.items() if len(rows) > 1)
    multi_surface_commands = sorted(
        command
        for command, rows in occurrences.items()
        if len({row["category"] for row in rows}) > 1
    )
    return {
        "version": CATALOG_VERSION,
        "categories": category_rows,
        "summary": {
            "category_count": len(category_rows),
            "group_count": len(COMMAND_GROUPS),
            "command_occurrences": sum(len(group.commands) for group in COMMAND_GROUPS),
            "unique_commands": len(occurrences),
            "multi_group_commands": multi_group_commands,
            "multi_surface_commands": multi_surface_commands,
        },
        "allowed_multi_surface": {
            command: list(categories)
            for command, categories in sorted(ALLOWED_MULTI_SURFACE.items())
        },
    }


def validate_command_catalog(*, flow_plans=None, entry_probe_commands=None):
    flow_plans = dict(flow_plans or {})
    entry_probe_commands = {
        str(key): _normalize_command(value)
        for key, value in dict(entry_probe_commands or {}).items()
    }
    occurrences = _catalog_occurrences()
    issues = []

    for command, rows in sorted(occurrences.items()):
        if not command.startswith("."):
            issues.append({"level": "error", "code": "malformed_command", "command": command})
        categories = tuple(sorted({row["category"] for row in rows}))
        if len(categories) > 1 and tuple(sorted(ALLOWED_MULTI_SURFACE.get(command, ()))) != categories:
            issues.append({
                "level": "error",
                "code": "unapproved_multi_surface",
                "command": command,
                "categories": list(categories),
            })

    replacement_commands = {}
    for key, plan in sorted(flow_plans.items()):
        commands = getattr(plan, "replaces_commands", ())
        if isinstance(plan, dict):
            commands = plan.get("replaces_commands") or ()
        for command in commands or ():
            normalized = _normalize_command(command)
            replacement_commands.setdefault(normalized, []).append(str(key))
            if normalized not in occurrences:
                issues.append({
                    "level": "warn",
                    "code": "flow_replacement_uncatalogued",
                    "command": normalized,
                    "flow_keys": [str(key)],
                })

    automated_entry_commands = set(entry_probe_commands.values()) | set(replacement_commands)
    external_commands = {
        command
        for command, rows in occurrences.items()
        if any(row["category"] == "external_miniapp" for row in rows)
    }
    unsupported_external = sorted(external_commands - automated_entry_commands)
    for command in unsupported_external:
        issues.append({
            "level": "info",
            "code": "external_entry_not_automated",
            "command": command,
        })

    error_count = sum(item["level"] == "error" for item in issues)
    warn_count = sum(item["level"] == "warn" for item in issues)
    return {
        "status": "error" if error_count else ("warn" if warn_count else "ok"),
        "summary": {
            "errors": error_count,
            "warnings": warn_count,
            "info": sum(item["level"] == "info" for item in issues),
            "catalogued_flow_replacements": sum(command in occurrences for command in replacement_commands),
            "flow_replacement_commands": len(replacement_commands),
            "external_entry_commands": len(external_commands),
            "automated_external_entries": len(external_commands & automated_entry_commands),
            "unautomated_external_entries": len(unsupported_external),
        },
        "issues": issues,
        "checklist": [
            {"key": "syntax", "passed": not any(item["code"] == "malformed_command" for item in issues)},
            {"key": "surface_ownership", "passed": not any(item["code"] == "unapproved_multi_surface" for item in issues)},
            {"key": "flow_replacement_coverage", "passed": not any(item["code"] == "flow_replacement_uncatalogued" for item in issues)},
            {"key": "external_automation_inventory", "passed": not unsupported_external, "advisory": True},
        ],
    }


__all__ = [
    "ALLOWED_MULTI_SURFACE",
    "CATALOG_VERSION",
    "CATEGORY_META",
    "COMMAND_GROUPS",
    "CommandGroup",
    "build_command_catalog_snapshot",
    "validate_command_catalog",
]
