import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from model import state as state_module
from model.features import miniapp_common
from model.webapp_core import MiniAppRequestAborted


@pytest.fixture
def pool_harness(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    identity_id = 990370001
    identity = state_module.ensure_identity_registered(identity_id)
    state_module.set_identity_account(identity_id, 7401)
    pool = miniapp_common._MiniAppSessionPool()
    harness = SimpleNamespace(
        pool=pool, identity_id=identity_id, identity=identity, sessions=[],
        request_hook=None, active=0, max_active=0, mutex=threading.Lock(),
        mount_hook=None, close_hook=None,
    )

    class Session:
        def __init__(self):
            self.index = len(harness.sessions)
            self.proxies = {}
            self.calls = 0
            self.closed = False
            self.close_calls = 0
            self.closed_while_active = False
            self.active = False
            harness.sessions.append(self)

        def mount(self, *_args):
            if harness.mount_hook:
                harness.mount_hook(self)

        def request(self, *_args, **_kwargs):
            with harness.mutex:
                self.active = True
                self.calls += 1
                harness.active += 1
                harness.max_active = max(harness.max_active, harness.active)
            try:
                if harness.request_hook:
                    harness.request_hook(self)
                return 200, {"ok": True}
            finally:
                with harness.mutex:
                    self.active = False
                    harness.active -= 1

        def close(self):
            self.closed_while_active |= self.active
            self.close_calls += 1
            self.closed = True
            if harness.close_hook:
                harness.close_hook(self)

    monkeypatch.setattr(miniapp_common, "_MINIAPP_SESSION_POOL", pool)
    monkeypatch.setattr(miniapp_common.requests, "Session", Session)
    yield harness
    pool.close()
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def transport(harness, identity_id=None, **kwargs):
    return miniapp_common.build_pooled_miniapp_transport(
        adapter_key="tower", identity_id=identity_id or harness.identity_id, proxies={}, **kwargs,
    )


def request():
    return {"method": "POST", "url": "https://miniapp.invalid/api/action", "payload": {"initData": "fixture"}}


@pytest.mark.parametrize("change", ["rebound", "replaced"])
def test_new_owner_never_reuses_old_owner_session(pool_harness, change):
    h = pool_harness
    transport(h)(request())
    if change == "rebound":
        state_module.set_identity_account(h.identity_id, 7402)
    else:
        state_module.remove_identity(h.identity_id)
        state_module.ensure_identity_registered(h.identity_id)
        state_module.set_identity_account(h.identity_id, 7401)
    transport(h)(request())
    assert len(h.sessions) == 2
    assert h.sessions[0].closed
    assert h.sessions[1].calls == 1


@pytest.mark.parametrize("change", ["rebound", "removed", "replaced"])
def test_old_transport_cannot_dispatch_after_owner_change(pool_harness, change):
    h = pool_harness
    old = transport(h)
    old(request())
    if change == "rebound":
        state_module.set_identity_account(h.identity_id, 7402)
    else:
        state_module.remove_identity(h.identity_id)
        if change == "replaced":
            state_module.ensure_identity_registered(h.identity_id)
            state_module.set_identity_account(h.identity_id, 7401)
    with pytest.raises(MiniAppRequestAborted):
        old(request())
    assert sum(session.calls for session in h.sessions) == 1


def test_profile_updates_keep_same_owner_session(pool_harness):
    h = pool_harness
    same = transport(h)
    same(request())
    state_module.update_send_as_profile(h.identity_id, username="changed_name")
    h.identity["next_tower_time"] = 12345
    same(request())
    transport(h)(request())
    assert len(h.sessions) == 1
    assert h.sessions[0].calls == 3


def test_close_does_not_close_active_socket_or_remove_serial_exclusion(pool_harness):
    h = pool_harness
    first_started, second_queued, second_started, release = (threading.Event() for _ in range(4))

    def hook(session):
        if session.index == 0:
            first_started.set()
            assert release.wait(3)
        else:
            second_started.set()

    def second_check():
        second_queued.set()
        return True

    h.request_hook = hook
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(transport(h), request())
        second = None
        try:
            assert first_started.wait(1)
            assert h.pool.close(adapter_key="tower", identity_id=h.identity_id) == 1
            closed_early = h.sessions[0].closed
            second = executor.submit(transport(h, operation_check=second_check), request())
            assert second_queued.wait(1)
            overlapped = second_started.wait(0.15)
        finally:
            release.set()
            first.result(2)
            if second:
                second.result(2)
    assert not closed_early
    assert not overlapped
    assert h.max_active == 1
    assert not any(session.closed_while_active for session in h.sessions)
    assert h.sessions[0].closed
    assert len(h.sessions) == 2


def test_removed_owner_is_reclaimed_on_next_pool_use(pool_harness):
    h = pool_harness
    transport(h)(request())
    state_module.remove_identity(h.identity_id)
    transport(h, h.identity_id + 1)(request())
    assert h.sessions[0].closed


def test_idle_cache_is_bounded_under_identity_churn(pool_harness):
    h = pool_harness
    limit = getattr(miniapp_common, "DEFAULT_MINIAPP_SESSION_MAX_ENTRIES", 256)
    for index in range(limit + 5):
        transport(h, h.identity_id + 1 + index)(request())
    assert sum(not session.closed for session in h.sessions) <= limit
    assert len(h.pool._entries) <= limit
    assert not h.pool._request_slots


def test_unregistered_transport_is_invalidated_when_identity_is_registered(pool_harness):
    h = pool_harness
    identity_id = h.identity_id + 1
    old = transport(h, identity_id)
    old(request())
    state_module.set_identity_account(identity_id, 7402)
    with pytest.raises(MiniAppRequestAborted):
        old(request())
    transport(h, identity_id)(request())
    assert len(h.sessions) == 2
    assert h.sessions[0].closed
    assert [session.calls for session in h.sessions] == [1, 1]


def test_idle_ttl_expires_at_boundary_using_monotonic_time(pool_harness):
    h = pool_harness
    now = [100.0]
    h.pool._clock = lambda: now[0]
    h.pool.idle_ttl_sec = 10
    same = transport(h)
    same(request())
    now[0] = 109.999
    assert h.pool.prune() == 0
    assert not h.sessions[0].closed
    now[0] = 110
    assert h.pool.prune() == 1
    assert h.sessions[0].closed
    assert not h.pool._entries
    assert h.pool.prune() == 0
    same(request())
    assert len(h.sessions) == 2
    assert h.sessions[0].close_calls == 1


def test_active_lease_is_not_expired_and_ttl_starts_on_release(pool_harness):
    h = pool_harness
    now = [0.0]
    h.pool._clock = lambda: now[0]
    h.pool.idle_ttl_sec = 10
    with h.pool.lease("tower", h.identity_id, {}, owner=miniapp_common.MiniAppIdentityOwner.capture(h.identity_id)):
        now[0] = 20
        assert h.pool.prune() == 0
        assert not h.sessions[0].closed
    now[0] = 29.999
    assert h.pool.prune() == 0
    now[0] = 30
    assert h.pool.prune() == 1


def test_capacity_evicts_least_recently_released_idle_session(pool_harness):
    h = pool_harness
    now = [0.0]
    h.pool._clock = lambda: now[0]
    h.pool.max_entries = 2
    first, second, third = (transport(h, h.identity_id + index) for index in range(3))
    first(request())
    now[0] = 1
    second(request())
    now[0] = 2
    first(request())
    now[0] = 3
    third(request())
    assert [session.closed for session in h.sessions] == [False, True, False]
    assert [session.calls for session in h.sessions] == [2, 1, 1]
    assert len(h.pool._entries) == 2
    assert not h.pool._request_slots


def test_capacity_rejects_when_all_sessions_are_active_without_breaking_leases(pool_harness):
    h = pool_harness
    h.pool.max_entries = 2
    with h.pool.lease("tower", h.identity_id + 1, {}), \
            h.pool.lease("tower", h.identity_id + 2, {}):
        with pytest.raises(MiniAppRequestAborted, match="session_pool_capacity"):
            transport(h, h.identity_id + 3)(request())
        assert len(h.sessions) == 2
        assert len(h.pool._request_slots) == 2
        assert not any(session.closed for session in h.sessions)
    assert not h.pool._request_slots
    transport(h, h.identity_id + 3)(request())
    assert len(h.sessions) == 3
    assert sum(not session.closed for session in h.sessions) == 2


def test_retired_active_session_still_counts_against_capacity(pool_harness):
    h = pool_harness
    h.pool.max_entries = 1
    with h.pool.lease("tower", h.identity_id, {}, owner=miniapp_common.MiniAppIdentityOwner.capture(h.identity_id)):
        assert h.pool.close() == 1
        assert h.pool.close() == 0
        assert len(h.pool._retired) == 1
        assert not h.sessions[0].closed
        with pytest.raises(MiniAppRequestAborted, match="session_pool_capacity"):
            transport(h, h.identity_id + 1)(request())
        assert len(h.sessions) == 1
    assert h.sessions[0].close_calls == 1
    assert not h.pool._retired
    transport(h, h.identity_id + 1)(request())
    assert len(h.sessions) == 2


@pytest.mark.parametrize("capacity", [1, 2])
def test_closing_runs_outside_pool_lock_but_still_counts_against_capacity(pool_harness, capacity):
    h = pool_harness
    h.pool.max_entries = capacity
    transport(h)(request())
    closing_started, release_close = threading.Event(), threading.Event()

    def close_hook(session):
        if session.index == 0:
            closing_started.set()
            assert release_close.wait(3)

    h.close_hook = close_hook
    with ThreadPoolExecutor(max_workers=2) as executor:
        closing = executor.submit(h.pool.close)
        another = None
        try:
            assert closing_started.wait(1)
            another = executor.submit(transport(h, h.identity_id + 1), request())
            if capacity == 1:
                with pytest.raises(MiniAppRequestAborted, match="session_pool_capacity"):
                    another.result(1)
                assert len(h.sessions) == 1
            else:
                assert another.result(1) == (200, {"ok": True})
            assert len(h.pool._closing) == 1
        finally:
            release_close.set()
            assert closing.result(2) == 1
            if another is not None:
                try:
                    another.result(2)
                except MiniAppRequestAborted:
                    pass
    assert not h.pool._closing
    assert not h.pool._request_slots


def test_initialization_failure_closes_partial_session_and_releases_slot(pool_harness):
    h = pool_harness

    def failed_mount(_session):
        raise RuntimeError("initData=fixture-secret")

    h.mount_hook = failed_mount
    with pytest.raises(MiniAppRequestAborted, match="^session_pool_init_failed$"):
        transport(h)(request())
    assert h.sessions[0].close_calls == 1
    assert not h.sessions[0].calls
    assert not h.pool._entries
    assert not h.pool._request_slots
    h.mount_hook = None
    assert transport(h)(request()) == (200, {"ok": True})


def test_close_exception_does_not_leak_metadata_or_report_secrets(pool_harness, caplog):
    h = pool_harness
    transport(h)(request())

    def failed_close(_session):
        raise RuntimeError("initData=fixture-secret")

    h.close_hook = failed_close
    assert h.pool.close() == 1
    assert "RuntimeError" in caplog.text
    assert "fixture-secret" not in caplog.text
    assert not h.pool._entries
    assert not h.pool._retired
    assert not h.pool._closing
    assert not h.pool._request_slots
    h.close_hook = None
    assert transport(h)(request()) == (200, {"ok": True})


@pytest.mark.parametrize("change", ["rebound", "replaced"])
def test_owner_change_during_request_discards_old_waiter_but_keeps_serial_exclusion(pool_harness, change):
    h = pool_harness
    first_started, old_queued, release = (threading.Event() for _ in range(3))

    def request_hook(session):
        if session.index == 0:
            first_started.set()
            assert release.wait(3)

    def old_check():
        with h.pool._lock:
            slot = h.pool._request_slots.get(("tower", h.identity_id))
            if slot is not None and slot.users > 1:
                old_queued.set()
        return True

    h.request_hook = request_hook
    old = transport(h, operation_check=old_check)
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(transport(h), request())
        waiter = replacement = None
        try:
            assert first_started.wait(1)
            waiter = executor.submit(old, request())
            assert old_queued.wait(1)
            if change == "rebound":
                state_module.set_identity_account(h.identity_id, 7402)
            else:
                state_module.remove_identity(h.identity_id)
                state_module.set_identity_account(h.identity_id, 7401)
            with pytest.raises(MiniAppRequestAborted):
                waiter.result(1)
            replacement = executor.submit(transport(h), request())
            assert h.sessions[0].calls == 1
            assert not h.sessions[0].closed
        finally:
            release.set()
            assert first.result(2) == (200, {"ok": True})
            if waiter is not None:
                try:
                    waiter.result(2)
                except MiniAppRequestAborted:
                    pass
            if replacement is not None:
                assert replacement.result(2) == (200, {"ok": True})
    assert [session.calls for session in h.sessions] == [1, 1]
    assert h.sessions[0].close_calls == 1
    assert h.max_active == 1
    assert not any(session.closed_while_active for session in h.sessions)
    assert not h.pool._request_slots
