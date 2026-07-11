import os
from dataclasses import dataclass


def _env_bool(name, default=False):
    raw = str(os.environ.get(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int_set(name):
    values = set()
    for item in str(os.environ.get(name, "") or "").replace("，", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.add(value)
    return frozenset(values)


def _env_text_set(name):
    return frozenset(
        item.strip()
        for item in str(os.environ.get(name, "") or "").replace("，", ",").split(",")
        if item.strip()
    )


@dataclass(frozen=True)
class AttemptFeatureFlags:
    shadow_write: bool = False
    shadow_bind: bool = False
    recover_report_only: bool = False
    control_modules: frozenset[str] = frozenset()
    control_identity_ids: frozenset[int] = frozenset()
    retention_days: int = 60

    @property
    def production_control_enabled(self):
        return bool(self.control_modules or self.control_identity_ids)


def get_attempt_feature_flags():
    try:
        retention_days = int(os.environ.get("XIUXIAN_ATTEMPT_RETENTION_DAYS", "60") or 60)
    except (TypeError, ValueError):
        retention_days = 60
    return AttemptFeatureFlags(
        shadow_write=_env_bool("XIUXIAN_ATTEMPT_SHADOW_WRITE"),
        shadow_bind=_env_bool("XIUXIAN_ATTEMPT_SHADOW_BIND"),
        recover_report_only=_env_bool("XIUXIAN_ATTEMPT_RECOVER_REPORT_ONLY"),
        control_modules=_env_text_set("XIUXIAN_ATTEMPT_CONTROL_MODULES"),
        control_identity_ids=_env_int_set("XIUXIAN_ATTEMPT_CONTROL_IDENTITIES"),
        retention_days=max(1, min(3650, retention_days)),
    )


__all__ = ["AttemptFeatureFlags", "get_attempt_feature_flags"]
