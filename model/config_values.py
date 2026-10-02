"""Shared coercion for persisted, UI and command configuration values."""


def coerce_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "on", "open", "enable", "enabled", "开", "开启", "启用"}:
        return True
    if text in {"", "0", "false", "no", "n", "off", "close", "disable", "disabled", "关", "关闭", "禁用"}:
        return False
    return bool(default)
