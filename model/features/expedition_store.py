"""副本流程集合的读写与查询，保留既有运行状态格式。"""

import time
from dataclasses import dataclass
from typing import Callable


def cleanup_collection_items(collection, now=None):
    now = float(now or time.time())
    changed = False
    for item_id, item in list((collection or {}).items()):
        if not isinstance(item, dict) or (not item.get("requires_reconciliation") and 0 < float(item.get("expires_at") or 0) <= now):
            collection.pop(item_id, None)
            changed = True
    return changed


def upsert_flow(flow, get_collection, save_collection, merge_existing=None):
    if not isinstance(flow, dict):
        return False
    flow_id = str(flow.get("flow_id") or "").strip()
    if not flow_id:
        return False
    collection = get_collection()
    old_flow_id = str(flow.get("previous_flow_id") or "").strip()
    if old_flow_id and old_flow_id != flow_id:
        collection.pop(old_flow_id, None)
        flow.pop("previous_flow_id", None)
    existing = collection.get(flow_id)
    if merge_existing and isinstance(existing, dict) and existing is not flow:
        merge_existing(flow, existing)
    collection[flow_id] = flow
    save_collection(collection)
    return True


def normalize_phases(phases):
    if phases is None:
        return None
    if isinstance(phases, str):
        return {phases}
    return {str(phase or "") for phase in phases if str(phase or "")}


def flow_sort_value(flow, fields):
    for field in fields or ():
        value = float((flow or {}).get(field) or 0)
        if value > 0:
            return value
    return 0


def find_flow(collection, fuben_chat_id=0, room_id="", phases=None, sort_fields=None):
    room_id = str(room_id or "").strip()
    fuben_chat_id = int(fuben_chat_id or 0)
    target_phases = normalize_phases(phases)
    matches = [
        flow for flow in (collection or {}).values()
        if isinstance(flow, dict)
        and (not fuben_chat_id or int(flow.get("fuben_chat_id") or 0) == fuben_chat_id)
        and (not room_id or str(flow.get("room_id") or "").strip() == room_id)
        and (target_phases is None or str(flow.get("phase") or "") in target_phases)
    ]
    if not matches:
        return None
    sort_fields = sort_fields or ("updated_at", "opened_at", "created_at")
    return max(matches, key=lambda item: flow_sort_value(item, sort_fields))


@dataclass(frozen=True)
class FubenFlowStore:
    load_run_state: Callable[[], dict]
    save_run_state: Callable[[dict], None]

    def get_state(self, state_key, collection_key):
        auto_state = self.load_run_state().get(state_key)
        if not isinstance(auto_state, dict):
            auto_state = {}
        if not isinstance(auto_state.get(collection_key), dict):
            auto_state[collection_key] = {}
        return auto_state

    def save_state(self, state_key, auto_state, collection_key):
        # 每次重读顶层，避免覆盖其他副本在等待发送期间写入的状态。
        run_state = self.load_run_state()
        run_state[state_key] = auto_state if isinstance(auto_state, dict) else {collection_key: {}}
        self.save_run_state(run_state)

    def get_collection(self, state_key, collection_key):
        return self.get_state(state_key, collection_key)[collection_key]

    def save_collection(self, state_key, collection_key, collection):
        auto_state = self.get_state(state_key, collection_key)
        auto_state[collection_key] = collection if isinstance(collection, dict) else {}
        self.save_state(state_key, auto_state, collection_key)

    def collection(self, state_key, collection_key):
        return FubenFlowCollection(self, state_key, collection_key)


@dataclass(frozen=True)
class FubenFlowCollection:
    store: FubenFlowStore
    state_key: str
    collection_key: str

    def load(self):
        return self.store.get_collection(self.state_key, self.collection_key)

    def save(self, collection):
        self.store.save_collection(self.state_key, self.collection_key, collection)

    def upsert(self, flow, merge_existing=None):
        return upsert_flow(flow, self.load, self.save, merge_existing)

    def cleanup(self, now=None):
        collection = self.load()
        changed = cleanup_collection_items(collection, now)
        if changed:
            self.save(collection)
        return changed

    def active(self, now):
        return [flow for flow in self.load().values()
                if isinstance(flow, dict) and (flow.get("requires_reconciliation") or float(flow.get("expires_at") or 0) > now)]
