"""Node rotator tests: rotation, runtime blacklist, stats persistence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from node_rotator import NodeRotator  # noqa: E402


class _FakeController:
    def __init__(self, nodes):
        self.nodes = nodes
        self.selected = None
        self.put_calls = 0

    def get(self, *args, **kwargs):
        import json as _json
        class Resp:
            status_code = 200
            def json(self):
                return {"all": self._owner.nodes}
        resp = Resp()
        resp._owner = self
        return resp

    def put(self, url, json=None, timeout=None):
        self.put_calls += 1
        self.selected = json["name"]
        class Resp:
            status_code = 204
        return Resp()


class _FakeSession:
    def __init__(self, controller):
        self.controller = controller

    def get(self, url, timeout=None):
        return self.controller.get(url, timeout=timeout)

    def put(self, url, json=None, timeout=None):
        return self.controller.put(url, json=json, timeout=timeout)


def make_rotator(nodes, tmp_path):
    controller = _FakeController(nodes)
    rotator = NodeRotator(
        controller="http://127.0.0.1:9090",
        group="PROXY",
        blacklist_path=Path(tmp_path) / "blacklist.json",
        switch_interval=0,
        max_consecutive_failures=3,
    )
    rotator._session = _FakeSession(controller)
    rotator.discover_nodes()
    return rotator, controller


class TestRotation:
    def test_round_robin(self, tmp_path):
        rotator, _ = make_rotator(["n1", "n2", "n3"], tmp_path)
        assert rotator.next_node() == "n1"
        assert rotator.next_node() == "n2"
        assert rotator.next_node() == "n3"
        assert rotator.next_node() == "n1"  # wraps

    def test_switch_calls_controller(self, tmp_path):
        rotator, controller = make_rotator(["n1", "n2"], tmp_path)
        node = rotator.rotate()
        assert node == "n1"
        assert controller.selected == "n1"
        assert controller.put_calls == 1

    def test_blacklisted_node_skipped(self, tmp_path):
        rotator, _ = make_rotator(["n1", "n2"], tmp_path)
        rotator.mark_failure("n1", blocked=True)
        assert rotator.next_node() == "n2"
        assert rotator.next_node() == "n2"  # n1 stays blacklisted

    def test_full_blacklist_resets(self, tmp_path):
        rotator, _ = make_rotator(["n1", "n2"], tmp_path)
        rotator.mark_failure("n1", blocked=True)
        rotator.mark_failure("n2", blocked=True)
        # 全部黑名单 -> 清空重探（宁可重探也不整体失败）
        assert rotator.next_node() in ("n1", "n2")


class TestFailureTracking:
    def test_consecutive_failures_blacklist(self, tmp_path):
        rotator, _ = make_rotator(["n1", "n2"], tmp_path)
        rotator.mark_failure("n1")
        rotator.mark_failure("n1")
        rotator.mark_failure("n1")
        assert "n1" in rotator._runtime_blacklist

    def test_success_resets_counter(self, tmp_path):
        rotator, _ = make_rotator(["n1", "n2"], tmp_path)
        rotator.mark_failure("n1")
        rotator.mark_failure("n1")
        rotator.mark_success("n1")
        rotator.mark_failure("n1")
        assert "n1" not in rotator._runtime_blacklist

    def test_stats_persist(self, tmp_path):
        rotator, _ = make_rotator(["n1"], tmp_path)
        rotator.mark_success("n1")
        rotator.mark_failure("n1", blocked=True)
        rotator.save_stats()
        data = json.loads((Path(tmp_path) / "blacklist.json").read_text(encoding="utf-8"))
        assert data["nodes"]["n1"]["ok"] == 1
        assert data["nodes"]["n1"]["blocked"] == 1

    def test_stats_loaded_for_health_preference(self, tmp_path):
        (Path(tmp_path) / "blacklist.json").write_text(
            json.dumps({"nodes": {"n1": {"ok": 10, "fail": 0, "blocked": 0}}}),
            encoding="utf-8",
        )
        rotator, _ = make_rotator(["n1", "n2"], tmp_path)
        assert rotator._stats["n1"]["ok"] == 10
