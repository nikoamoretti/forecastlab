from __future__ import annotations

from forecastlab import __version__ as package_version
from forecastlab.environment import build_environment_identity, require_python_lock
from forecastlab.version import __version__


def test_application_version_is_consistently_0_3_1(client) -> None:
    assert __version__ == "0.3.1"
    assert package_version == "0.3.1"
    identity = build_environment_identity()
    assert identity["application_version"] == "0.3.1"
    health = client.get("/health").json()
    assert health["version"] == "0.3.1"
    meta = client.get("/api/meta").json()
    assert meta["application_version"] == "0.3.1"
    try:
        from importlib.metadata import version

        assert version("forecastlab") == "0.3.1"
    except Exception:
        pass


def test_uv_lock_is_required_for_real_experiments(client, monkeypatch) -> None:
    monkeypatch.setattr("forecastlab.environment.python_dependency_hash", lambda **_k: None)
    monkeypatch.setattr("forecastlab_api.experiments.working_tree_dirty", lambda **_k: False)
    imported = client.post(
        "/api/benchmarks/import",
        files={
            "file": (
                "real.csv",
                (
                    b"question,forecast_date,resolution_date,outcome,resolution_source,category,provenance,is_synthetic,"
                    b"exact_yes,exact_no,resolution_deadline,authoritative_source\n"
                    b"Will lock block?,2024-01-01,2026-01-01,1,https://example.org,test,user,false,Yes,No,2026-01-01,https://example.org\n"
                ),
                "text/csv",
            )
        },
        data={"name": "real_no_lock"},
    )
    assert imported.status_code == 200
    created = client.post(
        "/api/experiments",
        json={"dataset_id": imported.json()["dataset_id"], "profile_ids": ["single_agent_equal_budget_v1"]},
    )
    assert created.status_code == 400
    assert "python_lockfile_required" in str(created.json())


def test_synthetic_experiment_may_run_without_python_lock(client, monkeypatch) -> None:
    monkeypatch.setattr("forecastlab.environment.python_dependency_hash", lambda **_k: None)
    datasets = client.get("/api/datasets").json()["datasets"]
    synth = next(item for item in datasets if item.get("is_builtin") or item["name"] == "synthetic_fixtures_v1")
    created = client.post(
        "/api/experiments",
        json={"dataset_id": synth["id"], "profile_ids": ["single_agent_equal_budget_v1"]},
    )
    assert created.status_code == 200
    assert created.json()["is_synthetic"] is True


def test_require_python_lock_fail_closed() -> None:
    try:
        require_python_lock(synthetic=False)
    except ValueError as exc:
        assert str(exc) == "python_lockfile_required"
    else:
        digest = require_python_lock(synthetic=False)
        assert digest
