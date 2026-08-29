"""生产 nsjail 配置必须在 cgroup v1 与 v2 主机上保留同一资源边界。"""
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def test_sandbox_config_detects_cgroup_v2_without_dropping_limits():
    config = (_ROOT / "deploy" / "sandbox.cfg").read_text(encoding="utf-8")

    assert "detect_cgroupv2: true" in config
    assert "cgroup_mem_max: 4294967296" in config
    assert "cgroup_pids_max: 128" in config
    assert "cgroup_cpu_ms_per_sec: 800" in config
    assert "clone_newnet: true" in config
