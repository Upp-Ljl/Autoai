from __future__ import annotations

from sat2_relay.autonomy import ParallelAutonomyController, RouteStatus
from sat2_relay.models import RepoRoute


class _MetaDB:
    def __init__(self, status: str):
        self.status = status

    def get_meta(self, key: str):
        if key.endswith(":status"):
            return self.status
        return None


class _NoTouchService:
    repo_config = None

    def refresh_config(self):
        raise AssertionError("terminal route must not touch config/GitHub during bootstrap")


def _route() -> RepoRoute:
    return RepoRoute(
        route_id="vision",
        mentor_role="S1",
        worker_role="S2",
        pr_number=101,
        progress_file="collaboration/routes/vision/progress.yaml",
        progress_ref="relay/vision",
        task_root=".sat2/routes/vision/tasks",
        bootstrap_task_file=".sat2/routes/vision/tasks/V-01.yml",
        signal_mode="progress",
    )


def test_complete_route_never_reboots_bootstrap_task():
    controller = ParallelAutonomyController(_NoTouchService(), _MetaDB(RouteStatus.COMPLETE.value))
    assert controller._bootstrap(_route()) is None


def test_blocked_route_never_reboots_bootstrap_task():
    controller = ParallelAutonomyController(_NoTouchService(), _MetaDB(RouteStatus.BLOCKED.value))
    assert controller._bootstrap(_route()) is None
