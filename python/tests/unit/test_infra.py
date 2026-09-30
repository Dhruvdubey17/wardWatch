"""Rules the compose stack and its dashboards must keep."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
INFRA = REPO / "infra"
LONG_RUNNING = {
    "postgres",
    "kafka",
    "ingest",
    "fhir-service",
    "scorer",
    "frontend",
    "prometheus",
    "grafana",
}


def compose() -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load((INFRA / "docker-compose.yml").read_text())
    return document


def defined_metrics() -> set[str]:
    names: set[str] = set()
    sources = [*REPO.glob("python/*/src/**/*.py"), *REPO.glob("ingest/src/**/*.cpp")]
    for source in sources:
        names.update(re.findall(r"wardwatch_[a-z0-9_]+", source.read_text()))
    return names


def test_project_and_names_carry_the_wardwatch_prefix() -> None:
    document = compose()
    assert document["name"] == "wardwatch"


def test_every_service_has_a_healthcheck() -> None:
    services = compose()["services"]
    assert LONG_RUNNING | {"kafka-init", "simulator"} == set(services)
    for name, service in services.items():
        assert "healthcheck" in service, name


def test_every_published_port_binds_loopback() -> None:
    for name, service in compose()["services"].items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port}"


def test_the_simulator_runs_only_in_the_demo_profile() -> None:
    services = compose()["services"]
    assert services["simulator"]["profiles"] == ["demo"]
    assert all("profiles" not in services[name] for name in LONG_RUNNING)


def test_no_credential_is_hardcoded() -> None:
    text = (INFRA / "docker-compose.yml").read_text()
    for match in re.finditer(r"PASSWORD: (\S+)", text):
        assert match.group(1).startswith("${WARDWATCH_"), match.group(0)


def test_prometheus_scrapes_ingest_fhir_and_scorer() -> None:
    config = yaml.safe_load((INFRA / "prometheus" / "prometheus.yml").read_text())
    targets = {
        job["job_name"]: job["static_configs"][0]["targets"] for job in config["scrape_configs"]
    }
    assert targets == {
        "ingest": ["ingest:9464"],
        "fhir-service": ["fhir-service:8000"],
        "scorer": ["scorer:9465"],
    }


@pytest.mark.parametrize("name", ["pipeline", "latency", "alerting"])
def test_dashboards_query_only_metrics_the_services_export(name: str) -> None:
    dashboard = json.loads((INFRA / "grafana" / "dashboards" / f"{name}.json").read_text())
    exported = defined_metrics()
    assert dashboard["panels"]
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == "wardwatch-prometheus"
        for target in panel["targets"]:
            for metric in re.findall(r"wardwatch_[a-z0-9_]+", target["expr"]):
                base = re.sub(r"_(bucket|count|sum)$", "", metric)
                assert metric in exported or base in exported, f"{name}: {metric}"


def test_dashboard_datasource_matches_provisioning() -> None:
    provisioned = yaml.safe_load(
        (INFRA / "grafana" / "provisioning" / "datasources" / "prometheus.yml").read_text()
    )
    assert [source["uid"] for source in provisioned["datasources"]] == ["wardwatch-prometheus"]


REQUIRED_JOBS = {
    "cpp-release",
    "cpp-asan",
    "cpp-tsan",
    "cpp-coverage",
    "fuzz",
    "python-lint",
    "python-unit",
    "python-integration",
    "ml-smoke",
    "frontend-lint",
    "frontend-unit",
    "frontend-e2e",
    "stack-smoke",
    "prose-lint",
}


def workflow() -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(
        (REPO / ".github" / "workflows" / "ci.yml").read_text()
    )
    return document


def test_ci_has_every_job_from_the_brief() -> None:
    assert set(workflow()["jobs"]) >= REQUIRED_JOBS


def test_no_ci_job_may_fail() -> None:
    for name, job in workflow()["jobs"].items():
        assert "continue-on-error" not in job, name
        for step in job.get("steps", []):
            assert "continue-on-error" not in step, name


def test_the_aggregate_check_needs_every_other_job() -> None:
    jobs = workflow()["jobs"]
    assert set(jobs["ci-passed"]["needs"]) == set(jobs) - {"ci-passed"}


def test_ci_uses_the_same_kafka_as_the_stack() -> None:
    images = re.findall(
        r"apache/kafka:\S+", (REPO / ".github" / "workflows" / "ci.yml").read_text()
    )
    assert images
    assert set(images) == {compose()["services"]["kafka"]["image"]}


def test_linux_builds_use_clang_19() -> None:
    # Ubuntu 24.04's default clang 18 cannot compile libstdc++'s std::expected.
    workflow_text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    installs = re.findall(r"apt-get install -y ([^\n]*)", workflow_text)
    clang_installs = [line for line in installs if "clang" in line]
    assert clang_installs
    for line in clang_installs:
        packages = line.split()
        assert "clang-19" in packages, line
        assert "clang" not in packages, line
    assert workflow_text.count("/usr/lib/llvm-19/bin") == len(clang_installs)
    dockerfile = (REPO / "ingest" / "Dockerfile").read_text()
    assert "clang-19" in dockerfile
    assert "ENV PATH=/usr/lib/llvm-19/bin:$PATH" in dockerfile


def test_postgres_stays_off_the_hosts_own_postgres_port() -> None:
    # A developer's own Postgres usually holds 127.0.0.1:5432, and make up failed on it.
    (published,) = compose()["services"]["postgres"]["ports"]
    assert published == "127.0.0.1:${WARDWATCH_POSTGRES_HOST_PORT:-15432}:5432"


def test_python_images_build_the_environment_where_it_runs() -> None:
    # Console script shebangs hold the build-time interpreter path, so a venv
    # built in one place and copied to another cannot start its services.
    dockerfile = (REPO / "python" / "Dockerfile").read_text()
    built = re.search(r"UV_PROJECT_ENVIRONMENT=(\S+)", dockerfile)
    assert built is not None
    assert f"COPY --from=build {built.group(1)} {built.group(1)}" in dockerfile
    assert f"ENV PATH={built.group(1)}/bin:$PATH" in dockerfile


# The first major of each action whose runtime is Node 24; older majors run on
# Node 20, which GitHub is retiring from its runners.
NODE24_MAJORS = {
    "actions/checkout": 5,
    "actions/setup-node": 5,
    "actions/cache": 5,
    "actions/upload-artifact": 5,
    "astral-sh/setup-uv": 7,
    "pnpm/action-setup": 5,
    "docker/setup-buildx-action": 4,
    "docker/build-push-action": 7,
}


def test_ci_actions_run_on_node_24() -> None:
    workflow_text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    used = re.findall(r"uses: ([\w.-]+/[\w.-]+)@v(\d+)", workflow_text)
    assert {action for action, _ in used} == set(NODE24_MAJORS)
    for action, major in used:
        assert int(major) >= NODE24_MAJORS[action], f"{action}@v{major}"


def test_sanitizer_and_coverage_jobs_install_the_clang_runtimes() -> None:
    # Without libclang-rt-19-dev the link fails: libclang_rt.asan.a is not found.
    jobs = workflow()["jobs"]
    for name in ("cpp-asan", "cpp-tsan", "cpp-coverage", "fuzz"):
        installs = [step.get("run", "") for step in jobs[name]["steps"]]
        assert any("libclang-rt-19-dev" in run for run in installs), name
