"""infra/scripts/deploy.sh with fake `docker` and `curl` executables on PATH (research.md R7)."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

BASH = shutil.which("bash") or "/bin/bash"
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "scripts" / "deploy.sh"
COMPOSE = ROOT / "infra" / "docker-compose.yml"
OLD_SHA = "1111111"
NEW_SHA = "2222222"

FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
if [[ "$*" == *"run --rm migrate"* && "${FAKE_MIGRATE_FAIL:-0}" == "1" ]]; then
  echo "migration failed" >&2
  exit 1
fi
if [[ "$*" == "pull ghcr.io/omar9564/disclosly-sandbox:"* \
  && "${FAKE_SANDBOX_PULL_FAIL:-0}" == "1" ]]; then
  exit 1
fi
if [[ "$*" == *"up -d --no-deps api"* ]]; then
  grep '^API_IMAGE_TAG=' "$FAKE_ENV_FILE" | cut -d= -f2- > "$FAKE_RUNNING"
fi
exit 0
"""

FAKE_CURL = """#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
if [[ "${FAKE_HEALTH_DOWN:-0}" == "1" ]]; then
  exit 7
fi
version="${FAKE_HEALTH_VERSION:-$(cat "$FAKE_RUNNING")}"
printf '{"status":"ok","version":"%s","service":"disclosly-api"}' "$version"
"""


@dataclass
class Sandbox:
    deploy_dir: Path
    env_file: Path
    log: Path
    running: Path
    env: dict[str, str]

    def run(self, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed script path, test-controlled args
            [BASH, str(SCRIPT), *args],
            env={**self.env, **extra_env},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def tag(self) -> str:
        for line in self.env_file.read_text().splitlines():
            if line.startswith("API_IMAGE_TAG="):
                return line.split("=", 1)[1]
        raise AssertionError("API_IMAGE_TAG missing from .env")

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    deploy_dir = tmp_path / "disclosly"
    (deploy_dir / ".deploy").mkdir(parents=True)
    env_file = deploy_dir / ".env"
    env_file.write_text(
        f"APP_ENV=production\nAPI_IMAGE_TAG={OLD_SHA}\nAPI_DOMAIN=api.example.test\n"
    )
    running = tmp_path / "running"
    running.write_text(OLD_SHA)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"docker": FAKE_DOCKER, "curl": FAKE_CURL}.items():
        exe = bin_dir / name
        exe.write_text(body)
        exe.chmod(0o755)
    log = tmp_path / "calls.log"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "DEPLOY_DIR": str(deploy_dir),
        "DEPLOY_HEALTH_TIMEOUT_S": "2",
        "DEPLOY_POLL_INTERVAL_S": "0.2",
        "FAKE_LOG": str(log),
        "FAKE_ENV_FILE": str(env_file),
        "FAKE_RUNNING": str(running),
    }
    return Sandbox(deploy_dir, env_file, log, running, env)


def test_success_switches_to_new_version(sandbox: Sandbox) -> None:
    result = sandbox.run(NEW_SHA)

    assert result.returncode == 0, result.stderr
    assert sandbox.tag() == NEW_SHA
    assert (sandbox.deploy_dir / ".deploy" / "previous").read_text().strip() == OLD_SHA
    calls = sandbox.calls()
    docker_calls = [c for c in calls if c.startswith("docker")]
    assert "pull api" in docker_calls[0]
    assert "--profile migrate run --rm migrate" in docker_calls[1]
    assert docker_calls[2] == "docker pull ghcr.io/omar9564/disclosly-sandbox:main"
    assert docker_calls[3].endswith("up -d --no-deps api sandbox")
    assert len(docker_calls) == 4
    assert any("https://api.example.test/healthz" in c for c in calls if c.startswith("curl"))


def test_sandbox_image_tag_comes_from_env(sandbox: Sandbox) -> None:
    sandbox.env_file.write_text(sandbox.env_file.read_text() + "SANDBOX_IMAGE_TAG=abc1234\n")
    assert sandbox.run(NEW_SHA).returncode == 0
    assert "docker pull ghcr.io/omar9564/disclosly-sandbox:abc1234" in sandbox.calls()


def test_failed_sandbox_pull_still_deploys(sandbox: Sandbox) -> None:
    result = sandbox.run(NEW_SHA, FAKE_SANDBOX_PULL_FAIL="1")

    assert result.returncode == 0, result.stderr
    assert "sandbox image pull failed" in result.stderr
    assert sandbox.tag() == NEW_SHA


def test_migrate_failure_keeps_old_version(sandbox: Sandbox) -> None:
    result = sandbox.run(NEW_SHA, FAKE_MIGRATE_FAIL="1")

    assert result.returncode != 0
    assert sandbox.tag() == OLD_SHA
    assert sandbox.running.read_text().strip() == OLD_SHA
    assert not any("up -d" in c for c in sandbox.calls())


def test_health_check_failure_rolls_back(sandbox: Sandbox) -> None:
    result = sandbox.run(NEW_SHA, FAKE_HEALTH_DOWN="1")

    assert result.returncode != 0
    assert "rolled back" in result.stderr
    assert sandbox.tag() == OLD_SHA
    assert sandbox.running.read_text().strip() == OLD_SHA
    up_calls = [c for c in sandbox.calls() if "up -d --no-deps api" in c]
    assert len(up_calls) == 2


def test_wrong_version_after_switch_rolls_back(sandbox: Sandbox) -> None:
    # The container came up but still reports the old version (e.g. the image tag was wrong).
    result = sandbox.run(NEW_SHA, FAKE_HEALTH_VERSION=OLD_SHA)

    assert result.returncode != 0
    assert sandbox.tag() == OLD_SHA


@pytest.mark.parametrize("bad", ["", "not-a-sha", "ABCDEFG", "123456", "a" * 41, "abc1234; rm -rf"])
def test_rejects_invalid_sha(sandbox: Sandbox, bad: str) -> None:
    result = sandbox.run(bad)

    assert result.returncode != 0
    assert sandbox.tag() == OLD_SHA
    assert sandbox.calls() == []


def test_requires_one_argument(sandbox: Sandbox) -> None:
    result = sandbox.run()

    assert result.returncode != 0
    assert "usage" in result.stderr.lower()


def test_migrate_service_sets_up_graph_checkpoints_after_alembic() -> None:
    # deploy.sh runs the `migrate` service; a failure in either step fails the deploy.
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    command = services["migrate"]["command"]

    assert command[:2] == ["sh", "-c"]
    assert command[2] == "alembic upgrade head && python -m agent.checkpoint setup"


def test_only_the_sandbox_service_holds_the_docker_socket() -> None:
    config = yaml.safe_load(COMPOSE.read_text())
    services = config["services"]
    with_socket = [
        name
        for name, service in services.items()
        if any("docker.sock" in str(v) for v in service.get("volumes", []))
    ]
    assert with_socket == ["sandbox"]
    assert config["networks"]["sandbox_net"]["internal"] is True
    assert services["sandbox"]["networks"] == ["sandbox_net"]
    assert "sandbox_net" in services["api"]["networks"]
    joined = [n for n, s in services.items() if "sandbox_net" in s.get("networks", [])]
    assert sorted(joined) == ["api", "sandbox"]
    assert "ports" not in services["sandbox"]
    assert services["sandbox"]["entrypoint"][:2] == ["uvicorn", "sandbox.service:app"]
