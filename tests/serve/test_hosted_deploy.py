"""deploy/: the container image builds (when Docker is available), and the
AWS template and scripts say what HOSTED.md says they say. Nothing here
talks to AWS."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _cfn():
    yaml = pytest.importorskip("yaml")

    class Loader(yaml.SafeLoader):
        pass

    def tag(loader, suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return {suffix: loader.construct_scalar(node)}
        if isinstance(node, yaml.SequenceNode):
            return {suffix: loader.construct_sequence(node, deep=True)}
        return {suffix: loader.construct_mapping(node, deep=True)}
    Loader.add_multi_constructor("!", tag)
    return yaml.load((DEPLOY / "aws" / "cloudformation.yml").read_text(), Loader=Loader)


def test_template_runs_exactly_one_task_and_never_two():
    t = _cfn()["Resources"]
    svc = t["Service"]["Properties"]
    assert svc["DesiredCount"] == 1
    assert svc["DeploymentConfiguration"]["MaximumPercent"] == 100
    assert svc["DeploymentConfiguration"]["MinimumHealthyPercent"] == 0


def test_template_exposes_only_tls_and_keeps_the_task_private_to_the_alb():
    t = _cfn()["Resources"]
    assert t["HttpsListener"]["Properties"]["Protocol"] == "HTTPS"
    assert "TLS13" in t["HttpsListener"]["Properties"]["SslPolicy"]
    assert t["HttpRedirect"]["Properties"]["DefaultActions"][0]["Type"] == "redirect"
    task_in = t["TaskSg"]["Properties"]["SecurityGroupIngress"]
    assert len(task_in) == 1 and "SourceSecurityGroupId" in task_in[0] and "CidrIp" not in task_in[0]
    efs_in = t["EfsSg"]["Properties"]["SecurityGroupIngress"]
    assert len(efs_in) == 1 and "SourceSecurityGroupId" in efs_in[0]
    container = t["TaskDefinition"]["Properties"]["ContainerDefinitions"][0]
    assert [m["ContainerPort"] for m in container["PortMappings"]] == [8080]   # not the admin port
    assert container["ReadonlyRootFilesystem"] is True
    assert t["Efs"]["Properties"]["Encrypted"] is True
    vol = t["TaskDefinition"]["Properties"]["Volumes"][0]["EFSVolumeConfiguration"]
    assert vol["TransitEncryption"] == "ENABLED" and vol["AuthorizationConfig"]["IAM"] == "ENABLED"
    # the pepper arrives as a secret, never as a plain environment value
    assert [s["Name"] for s in container["Secrets"]] == ["TRADEFLOOR_HOSTED_PEPPER"]
    assert all("PEPPER" not in e["Name"] for e in container["Environment"])


def test_template_lints_clean_when_cfn_lint_is_installed():
    if shutil.which("cfn-lint") is None and not (Path(os.sys.executable).parent / "cfn-lint").exists():
        pytest.skip("cfn-lint not installed")
    exe = shutil.which("cfn-lint") or str(Path(os.sys.executable).parent / "cfn-lint")
    r = subprocess.run([exe, str(DEPLOY / "aws" / "cloudformation.yml")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_deploy_script_refuses_without_the_owners_go_ahead():
    env = {k: v for k, v in os.environ.items() if k != "TRADEFLOOR_DEPLOY_CONFIRM"}
    r = subprocess.run(["bash", str(DEPLOY / "aws" / "deploy.sh")], capture_output=True, text=True,
                       env=env)
    assert r.returncode == 2 and "refusing" in r.stderr


def test_compose_publishes_on_loopback_only():
    text = (DEPLOY / "docker-compose.yml").read_text()
    ports = [line.strip() for line in text.splitlines() if line.strip().startswith('- "') and ":8080" in line]
    assert ports and all(p.startswith('- "127.0.0.1:') for p in ports)


def test_image_keeps_the_goldens_out_and_floats_portable():
    ignore = (DEPLOY / "Dockerfile.dockerignore").read_text().splitlines()
    assert ignore[ignore.index("**")] == "**" and "!python/**" in ignore
    assert not any(line.startswith("!rust/goldens") for line in ignore)
    dockerfile = (DEPLOY / "Dockerfile").read_text()
    assert "target-cpu" not in dockerfile.replace("forbids target-cpu=native", "")
    assert "USER 10001" in dockerfile


def test_example_plans_file_loads():
    from tradefloor.serve.hosted.plans import DEFAULT_PLANS, load_plans
    plans = load_plans(DEPLOY / "plans.example.json")
    assert plans == DEFAULT_PLANS
    assert json.loads((DEPLOY / "plans.example.json").read_text())


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker is not installed on this machine")
def test_docker_image_builds_and_starts():
    probe = subprocess.run(["docker", "info"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("Docker is installed but its daemon is not running")
    tag = "tradefloor-hosted:test"
    r = subprocess.run(["docker", "build", "-f", str(DEPLOY / "Dockerfile"), "-t", tag, str(ROOT)],
                       capture_output=True, text=True, timeout=3600)
    assert r.returncode == 0, r.stderr[-4000:]
    r = subprocess.run(["docker", "run", "--rm", "--read-only", "--entrypoint", "python", tag, "-c",
                        "import tradefloor, tradefloor.serve.hosted.app, tradefloor.serve.core; "
                        "print(tradefloor.preset_names()[-1])"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
