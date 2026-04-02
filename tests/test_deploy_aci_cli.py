import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from importlib.machinery import SourceFileLoader

import pytest

from deploy_aci_cli_support import (
    CLI_TIMEOUT_SECONDS,
    FAILURE_SCENARIO,
    REPO_ROOT,
    SUCCESS_SCENARIOS,
    canonicalize_dry_run_json,
    read_golden,
    run_cli,
)


ARM_TEMPLATE_BUILDER_PATH = REPO_ROOT / "deploy-aci-arm" / "arm_template_builder.py"


def load_arm_template_builder_module():
    spec = importlib.util.spec_from_file_location(
        "test_arm_template_builder",
        ARM_TEMPLATE_BUILDER_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_deploy_aci_module():
    cli_path = REPO_ROOT / "deploy-aci-arm" / "deploy-aci"
    loader = SourceFileLoader("test_deploy_aci_module", str(cli_path))
    spec = importlib.util.spec_from_loader(
        "test_deploy_aci_module",
        loader,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    original_sys_path = list(sys.path)
    sys.path.insert(0, str(cli_path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_sys_path
    return module


def parse_dry_run_payload(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(canonicalize_dry_run_json(result.stdout))


def resources_by_type(resources: list[dict], resource_type: str) -> list[dict]:
    return [resource for resource in resources if resource["type"] == resource_type]


def resource_by_type_and_name(resources: list[dict], resource_type: str, name: str) -> dict:
    matches = [
        resource
        for resource in resources
        if resource["type"] == resource_type and resource["name"] == name
    ]
    assert matches == [matches[0]]
    return matches[0]


def delete_plan_resources_by_label(phases: list[dict]) -> dict[str, list[str]]:
    return {
        phase["label"]: [resource["name"] for resource in phase["resources"]]
        for phase in phases
    }


def make_successful_az_run_recorder(commands: list[list[str]]):
    def fake_run(*args, **kwargs):
        del kwargs
        command = args[0]
        commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    return fake_run


def assert_ssh_load_balancer_shape(
    load_balancer: dict,
    *,
    public_ip_name: str,
    expected_ip: str,
    subnet_id: str,
    expected_depends_on: list[str],
) -> None:
    assert load_balancer["dependsOn"] == expected_depends_on
    assert load_balancer["sku"] == {"name": "Standard"}
    assert load_balancer["properties"]["frontendIPConfigurations"] == [
        {
            "name": "LoadBalancerFrontEnd",
            "properties": {
                "publicIPAddress": {
                    "id": f"[resourceId('Microsoft.Network/publicIPAddresses', '{public_ip_name}')]"
                }
            },
        }
    ]
    assert load_balancer["properties"]["backendAddressPools"] == [
        {
            "name": "BackendPool",
            "properties": {
                "loadBalancerBackendAddresses": [
                    {
                        "name": f"{load_balancer['name']}-backend-address",
                        "properties": {
                            "ipAddress": expected_ip,
                            "subnet": {"id": subnet_id},
                        },
                    }
                ]
            },
        }
    ]
    assert load_balancer["properties"]["probes"] == [
        {
            "name": "ssh-health-probe",
            "properties": {
                "port": 22,
                "protocol": "Tcp",
                "intervalInSeconds": 5,
                "numberOfProbes": 2,
            },
        }
    ]
    assert load_balancer["properties"]["loadBalancingRules"] == [
        {
            "name": "ssh-rule",
            "properties": {
                "backendAddressPool": {
                    "id": f"[concat(resourceId('Microsoft.Network/loadBalancers', '{load_balancer['name']}'), '/backendAddressPools/BackendPool')]"
                },
                "backendPort": 22,
                "enableFloatingIP": False,
                "frontendIPConfiguration": {
                    "id": f"[concat(resourceId('Microsoft.Network/loadBalancers', '{load_balancer['name']}'), '/frontendIPConfigurations/LoadBalancerFrontEnd')]"
                },
                "frontendPort": 22,
                "idleTimeoutInMinutes": 4,
                "probe": {
                    "id": f"[concat(resourceId('Microsoft.Network/loadBalancers', '{load_balancer['name']}'), '/probes/ssh-health-probe')]"
                },
                "protocol": "Tcp",
            },
        }
    ]


def test_arm_resource_lookup_helpers_use_type_and_name() -> None:
    resources = [
        {"type": "Type.B", "name": "second"},
        {"type": "Type.A", "name": "first"},
        {"type": "Type.A", "name": "third"},
    ]

    assert [resource["name"] for resource in resources_by_type(resources, "Type.A")] == [
        "first",
        "third",
    ]
    assert resource_by_type_and_name(resources, "Type.B", "second") == {
        "type": "Type.B",
        "name": "second",
    }


def assert_common_public_template_shape(payload: dict, expected_name: str) -> dict:
    assert payload["$schema"] == "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
    assert payload["contentVersion"] == "1.0.0.0"
    assert payload["parameters"] == {}
    assert payload["variables"] == {}
    assert len(payload["resources"]) == 1

    container_group = payload["resources"][0]
    assert container_group["type"] == "Microsoft.ContainerInstance/containerGroups"
    assert container_group["apiVersion"] == "2022-10-01-preview"
    assert container_group["name"] == f"{expected_name}-1"
    assert container_group["location"] == "northeurope"
    assert container_group["identity"] == {"type": "SystemAssigned"}
    assert "dependsOn" not in container_group

    properties = container_group["properties"]
    assert properties["restartPolicy"] == "Never"
    assert properties["osType"] == "Linux"
    assert properties["volumes"] == []
    assert properties["ipAddress"] == {
        "ports": [{"port": "22", "protocol": "TCP"}],
        "type": "Public",
    }
    assert len(properties["containers"]) == 1

    container = properties["containers"][0]
    assert container["name"] == f"{expected_name}-0"
    assert container["properties"] == {
        "command": [
            "/bin/sh",
            "-c",
            "echo Fabric_NodeIPOrFQDN=$Fabric_NodeIPOrFQDN >> /aci_env && echo UVM_SECURITY_CONTEXT_DIR=$UVM_SECURITY_CONTEXT_DIR >> /aci_env && mkdir -p /root/.ssh/ && gpg --import /etc/pki/rpm-gpg/MICROSOFT-RPM-GPG-KEY && tdnf update -y && tdnf install -y openssh-server ca-certificates && tail -f /dev/null",
        ],
        "environmentVariables": [],
        "image": "ghcr.io/example/image:latest",
        "ports": [{"port": 22, "protocol": "TCP"}],
        "resources": {"requests": {"cpu": 4, "memoryInGB": 16}},
        "securityContext": {"privileged": True},
        "volumeMounts": [],
    }
    return properties


def test_canonicalize_dry_run_json_uses_jq_sorting(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='''{
  "a": {
    "c": 3,
    "d": 4
  },
  "b": 1
}
''',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    stdout = 'Creating new VNet: sample-vnet/default\n{"b": 1, "a": {"d": 4, "c": 3}}\n'

    assert canonicalize_dry_run_json(stdout) == (
        '{\n'
        '  "a": {\n'
        '    "c": 3,\n'
        '    "d": 4\n'
        '  },\n'
        '  "b": 1\n'
        '}\n'
    )


def test_canonicalize_dry_run_json_rejects_trailing_output() -> None:
    stdout = 'Creating new VNet: sample-vnet/default\n{"a": 1}\nextra output\n'

    with pytest.raises(ValueError, match="exactly one decodable JSON object"):
        canonicalize_dry_run_json(stdout)


def test_canonicalize_dry_run_json_rejects_multiple_json_objects() -> None:
    stdout = 'Creating new VNet: sample-vnet/default\n{"a": 1}\n{"b": 2}\n'

    with pytest.raises(ValueError, match="exactly one decodable JSON object"):
        canonicalize_dry_run_json(stdout)


def test_canonicalize_dry_run_json_rejects_missing_json() -> None:
    stdout = "Creating new VNet: sample-vnet/default\nno json here\n"

    with pytest.raises(ValueError, match="exactly one decodable JSON object"):
        canonicalize_dry_run_json(stdout)


def test_run_cli_sets_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_cli("--image", "ghcr.io/example/image:latest", "--resource-group", "rg-test")

    assert captured["timeout"] == CLI_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("golden_name", "args"),
    SUCCESS_SCENARIOS,
    ids=[golden_name for golden_name, _ in SUCCESS_SCENARIOS],
)
def test_dry_run_matches_golden(golden_name: str, args: list[str]) -> None:
    result = run_cli(*args)

    assert result.returncode == 0, result.stderr or result.stdout
    assert canonicalize_dry_run_json(result.stdout) == read_golden(f"{golden_name}.json")


def test_existing_vnet_bool_bug_is_pinned() -> None:
    _, args = FAILURE_SCENARIO
    result = run_cli(*args)

    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout == read_golden(FAILURE_SCENARIO[0])


def test_builder_rejects_unknown_sku_values() -> None:
    module = load_arm_template_builder_module()

    with pytest.raises(ValueError, match="Unsupported ACI SKU"):
        module.aci_sku_name("premium")


def test_dry_run_standard_sku_emits_expected_public_template() -> None:
    result = run_cli(
        "--image",
        "ghcr.io/example/image:latest",
        "--resource-group",
        "rg-test",
        "--name",
        "sku-standard",
        "--create-vnet",
        "False",
        "--create-nat",
        "False",
        "--sku",
        "standard",
    )

    payload = parse_dry_run_payload(result)
    properties = assert_common_public_template_shape(payload, expected_name="sku-standard")

    assert properties["sku"] == "Standard"
    assert "confidentialComputeProperties" not in properties


def test_validate_args_requires_image_for_create_mode() -> None:
    module = load_deploy_aci_module()
    parser = module.build_parser()
    args = parser.parse_args(["--resource-group", "rg-test", "--name", "create-demo"])

    with pytest.raises(SystemExit, match="2"):
        module.validate_args(parser, args)


def test_validate_args_allows_missing_image_for_delete_mode() -> None:
    module = load_deploy_aci_module()
    parser = module.build_parser()
    args = parser.parse_args(
        ["--delete", "--resource-group", "rg-test", "--name", "delete-demo"]
    )

    module.validate_args(parser, args)


def test_validate_args_skips_create_nat_validation_for_delete_mode() -> None:
    module = load_deploy_aci_module()
    parser = module.build_parser()
    args = parser.parse_args(
        [
            "--delete",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-existing-vnet",
            "--create-vnet",
            "False",
            "--vnet-subnet",
            "existing-vnet/existing-subnet",
        ]
    )

    module.validate_args(parser, args)


def test_build_generated_resources_matches_existing_public_dry_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_deploy_aci_module()

    args = module.build_parser().parse_args(
        [
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-shared-public",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
        ]
    )

    resources, context = module.build_generated_resources(args)

    payload = {"resources": [resource.to_dict() for resource in resources]}
    assert [resource["type"] for resource in payload["resources"]] == [
        "Microsoft.ContainerInstance/containerGroups"
    ]
    assert context["vnet_name"] is None
    assert context["subnet_name"] is None
    assert capsys.readouterr().out == ""


def test_build_generated_resources_new_vnet_without_nat_returns_context_and_vnet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_deploy_aci_module()

    args = module.build_parser().parse_args(
        [
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-shared-vnet",
            "--create-nat",
            "False",
        ]
    )

    resources, context = module.build_generated_resources(args)

    payload = {"resources": [resource.to_dict() for resource in resources]}
    assert [resource["type"] for resource in payload["resources"]] == [
        "Microsoft.Network/virtualNetworks",
        "Microsoft.ContainerInstance/containerGroups",
    ]
    assert context == {
        "vnet_name": "delete-shared-vnet-vnet",
        "subnet_name": "default",
    }
    assert capsys.readouterr().out == ""


def test_build_generated_resources_existing_vnet_ssh_lb_reuses_context_without_vnet_resource(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_deploy_aci_module()
    ssh_key_path = tmp_path / "id_rsa.pub"
    ssh_key_path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDc test@example\n")

    args = module.build_parser().parse_args(
        [
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-shared-existing",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
            "--vnet-subnet",
            "existing-vnet/existing-subnet",
            "--access-mode",
            "ssh-lb",
            "--ssh-key",
            str(ssh_key_path),
        ]
    )

    resources, context = module.build_generated_resources(args)

    payload = {"resources": [resource.to_dict() for resource in resources]}
    assert [resource["type"] for resource in payload["resources"]] == [
        "Microsoft.ContainerInstance/containerGroups",
        "Microsoft.Network/publicIPAddresses",
        "Microsoft.Network/loadBalancers",
    ]
    assert context == {
        "vnet_name": "existing-vnet",
        "subnet_name": "existing-subnet",
    }
    assert capsys.readouterr().out == ""


def test_delete_plan_for_new_vnet_ssh_lb_uses_dependency_safe_phase_order() -> None:
    module = load_deploy_aci_module()

    args = module.build_parser().parse_args(
        [
            "--delete",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-plan",
            "--num-containers",
            "2",
            "--access-mode",
            "ssh-lb",
        ]
    )

    resources, _ = module.build_generated_resources(args)
    phases = module.build_delete_plan(resources)

    assert [phase["label"] for phase in phases] == [
        "containerGroups",
        "natGateways",
        "natPublicIps",
        "loadBalancers",
        "loadBalancerPublicIps",
        "virtualNetworks",
    ]
    assert delete_plan_resources_by_label(phases) == {
        "containerGroups": ["delete-plan-1", "delete-plan-2"],
        "natGateways": ["delete-plan-nat"],
        "natPublicIps": ["delete-plan-ip"],
        "loadBalancers": ["delete-plan-1-lb", "delete-plan-2-lb"],
        "loadBalancerPublicIps": [
            "delete-plan-1-lb-ip",
            "delete-plan-2-lb-ip",
        ],
        "virtualNetworks": ["delete-plan-vnet"],
    }


def test_delete_plan_for_existing_vnet_ssh_lb_omits_virtual_network_delete() -> None:
    module = load_deploy_aci_module()

    args = module.build_parser().parse_args(
        [
            "--delete",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-existing",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
            "--vnet-subnet",
            "existing-vnet/existing-subnet",
            "--access-mode",
            "ssh-lb",
        ]
    )

    resources, _ = module.build_generated_resources(args)
    phases = module.build_delete_plan(resources)

    assert delete_plan_resources_by_label(phases) == {
        "containerGroups": ["delete-existing-1"],
        "natGateways": [],
        "natPublicIps": [],
        "loadBalancers": ["delete-existing-1-lb"],
        "loadBalancerPublicIps": ["delete-existing-1-lb-ip"],
        "virtualNetworks": [],
    }


def test_delete_plan_for_existing_vnet_exec_omits_existing_virtual_network_delete() -> None:
    module = load_deploy_aci_module()

    args = module.build_parser().parse_args(
        [
            "--delete",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-existing-exec",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
            "--vnet-subnet",
            "existing-vnet/existing-subnet",
        ]
    )

    resources, _ = module.build_generated_resources(args)
    phases = module.build_delete_plan(resources)

    assert delete_plan_resources_by_label(phases) == {
        "containerGroups": ["delete-existing-exec-1"],
        "natGateways": [],
        "natPublicIps": [],
        "loadBalancers": [],
        "loadBalancerPublicIps": [],
        "virtualNetworks": [],
    }


def test_delete_plan_nat_public_ip_bucket_uses_resource_provenance_when_name_ends_with_lb() -> None:
    module = load_deploy_aci_module()

    args = module.build_parser().parse_args(
        [
            "--delete",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-edge-lb",
            "--access-mode",
            "ssh-lb",
        ]
    )

    resources, _ = module.build_generated_resources(args)
    phases = module.build_delete_plan(resources)

    assert delete_plan_resources_by_label(phases) == {
        "containerGroups": ["delete-edge-lb-1"],
        "natGateways": ["delete-edge-lb-nat"],
        "natPublicIps": ["delete-edge-lb-ip"],
        "loadBalancers": ["delete-edge-lb-1-lb"],
        "loadBalancerPublicIps": ["delete-edge-lb-1-lb-ip"],
        "virtualNetworks": ["delete-edge-lb-vnet"],
    }


def test_delete_plan_public_only_contains_container_groups() -> None:
    module = load_deploy_aci_module()

    args = module.build_parser().parse_args(
        [
            "--delete",
            "--resource-group",
            "rg-test",
            "--name",
            "public-only",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
        ]
    )

    resources, _ = module.build_generated_resources(args)
    phases = module.build_delete_plan(resources)

    assert delete_plan_resources_by_label(phases) == {
        "containerGroups": ["public-only-1"],
        "natGateways": [],
        "natPublicIps": [],
        "loadBalancers": [],
        "loadBalancerPublicIps": [],
        "virtualNetworks": [],
    }


def test_delete_plan_new_vnet_without_ssh_lb_keeps_nat_before_vnet() -> None:
    module = load_deploy_aci_module()

    args = module.build_parser().parse_args(
        [
            "--delete",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-new-vnet",
        ]
    )

    resources, _ = module.build_generated_resources(args)
    phases = module.build_delete_plan(resources)

    assert delete_plan_resources_by_label(phases) == {
        "containerGroups": ["delete-new-vnet-1"],
        "natGateways": ["delete-new-vnet-nat"],
        "natPublicIps": ["delete-new-vnet-ip"],
        "loadBalancers": [],
        "loadBalancerPublicIps": [],
        "virtualNetworks": ["delete-new-vnet-vnet"],
    }


def test_delete_plan_reuses_same_generated_resources_as_create_dry_run(
    tmp_path: Path,
) -> None:
    module = load_deploy_aci_module()
    ssh_key_path = tmp_path / "id_rsa.pub"
    ssh_key_path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDc test@example\n")

    create_result = run_cli(
        "--image",
        "ghcr.io/example/image:latest",
        "--resource-group",
        "rg-test",
        "--name",
        "same-graph",
        "--num-containers",
        "2",
        "--access-mode",
        "ssh-lb",
        "--ssh-key",
        str(ssh_key_path),
    )
    dry_run_pairs = [
        (resource["type"], resource["name"])
        for resource in parse_dry_run_payload(create_result)["resources"]
    ]

    delete_args = module.build_parser().parse_args(
        [
            "--delete",
            "--resource-group",
            "rg-test",
            "--name",
            "same-graph",
            "--num-containers",
            "2",
            "--access-mode",
            "ssh-lb",
        ]
    )

    delete_resources, _ = module.build_generated_resources(delete_args)
    delete_pairs = [
        (resource.to_dict()["type"], resource.to_dict()["name"])
        for resource in delete_resources
    ]

    assert delete_pairs == dry_run_pairs
    assert delete_plan_resources_by_label(module.build_delete_plan(delete_resources)) == {
        "containerGroups": ["same-graph-1", "same-graph-2"],
        "natGateways": ["same-graph-nat"],
        "natPublicIps": ["same-graph-ip"],
        "loadBalancers": ["same-graph-1-lb", "same-graph-2-lb"],
        "loadBalancerPublicIps": ["same-graph-1-lb-ip", "same-graph-2-lb-ip"],
        "virtualNetworks": ["same-graph-vnet"],
    }


def test_execute_delete_plan_runs_phase_order_and_tolerates_missing_resources(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_deploy_aci_module()
    commands: list[list[str]] = []

    def fake_delete(resource_group: str, resource_type: str, name: str) -> None:
        commands.append([resource_group, resource_type, name])
        if name == "demo-2-lb":
            raise module.ResourceAlreadyMissing(name)

    monkeypatch.setattr(module, "delete_azure_resource", fake_delete)

    phases = [
        {
            "label": "containerGroups",
            "resources": [
                {"type": "Microsoft.ContainerInstance/containerGroups", "name": "demo-1"}
            ],
        },
        {
            "label": "natGateways",
            "resources": [{"type": "Microsoft.Network/natGateways", "name": "demo-nat"}],
        },
        {
            "label": "natPublicIps",
            "resources": [
                {"type": "Microsoft.Network/publicIPAddresses", "name": "demo-ip"}
            ],
        },
        {
            "label": "loadBalancers",
            "resources": [{"type": "Microsoft.Network/loadBalancers", "name": "demo-2-lb"}],
        },
        {
            "label": "loadBalancerPublicIps",
            "resources": [
                {"type": "Microsoft.Network/publicIPAddresses", "name": "demo-2-lb-ip"}
            ],
        },
        {
            "label": "virtualNetworks",
            "resources": [
                {"type": "Microsoft.Network/virtualNetworks", "name": "demo-vnet"}
            ],
        },
    ]

    failures = module.execute_delete_plan("rg-test", phases)

    assert failures == []
    assert commands == [
        ["rg-test", "Microsoft.ContainerInstance/containerGroups", "demo-1"],
        ["rg-test", "Microsoft.Network/natGateways", "demo-nat"],
        ["rg-test", "Microsoft.Network/publicIPAddresses", "demo-ip"],
        ["rg-test", "Microsoft.Network/loadBalancers", "demo-2-lb"],
        ["rg-test", "Microsoft.Network/publicIPAddresses", "demo-2-lb-ip"],
        ["rg-test", "Microsoft.Network/virtualNetworks", "demo-vnet"],
    ]
    assert capsys.readouterr().out == "Skipping missing resource: demo-2-lb\n"


@pytest.mark.parametrize(
    ("failures", "expected_exit_code"),
    [([], 0), (["demo-1"], 1)],
    ids=["delete-success", "delete-failure"],
)
def test_main_delete_mode_exits_based_on_delete_failures(
    monkeypatch: pytest.MonkeyPatch,
    failures: list[str],
    expected_exit_code: int,
) -> None:
    module = load_deploy_aci_module()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy-aci",
            "--delete",
            "--resource-group",
            "rg-test",
            "--name",
            "delete-main",
        ],
    )
    monkeypatch.setattr(
        module,
        "execute_delete_plan",
        lambda resource_group, phases: failures,
    )

    with pytest.raises(SystemExit) as exc_info:
        module.main()

    assert exc_info.value.code == expected_exit_code


def test_execute_delete_plan_records_failures_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_deploy_aci_module()
    commands: list[str] = []

    def fake_delete(resource_group: str, resource_type: str, name: str) -> None:
        del resource_group, resource_type
        commands.append(name)
        if name == "demo-nat":
            raise subprocess.CalledProcessError(2, ["az", "resource", "delete"])

    monkeypatch.setattr(module, "delete_azure_resource", fake_delete)

    failures = module.execute_delete_plan(
        "rg-test",
        [
            {
                "label": "containerGroups",
                "resources": [
                    {"type": "Microsoft.ContainerInstance/containerGroups", "name": "demo-1"}
                ],
            },
            {
                "label": "natGateways",
                "resources": [{"type": "Microsoft.Network/natGateways", "name": "demo-nat"}],
            },
            {
                "label": "virtualNetworks",
                "resources": [
                    {"type": "Microsoft.Network/virtualNetworks", "name": "demo-vnet"}
                ],
            },
        ],
    )

    assert failures == ["demo-nat"]
    assert commands == ["demo-1", "demo-nat", "demo-vnet"]
    assert "Delete failed for demo-nat:" in capsys.readouterr().out


def test_delete_azure_resource_raises_resource_already_missing_for_missing_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_deploy_aci_module()

    def fake_run(*args, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=3,
            stdout="",
            stderr=(
                "ERROR: (ResourceNotFound) The Resource "
                "'Microsoft.Network/loadBalancers/demo-lb' under resource group "
                "'rg-test' was not found."
            ),
        )

    monkeypatch.setattr(module, "run", fake_run)

    with pytest.raises(module.ResourceAlreadyMissing):
        module.delete_azure_resource(
            "rg-test", "Microsoft.Network/loadBalancers", "demo-lb"
        )


def test_delete_azure_resource_preserves_missing_resource_group_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_deploy_aci_module()

    def fake_run(*args, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=3,
            stdout="",
            stderr="ERROR: (ResourceGroupNotFound) Resource group 'rg-test' could not be found.",
        )

    monkeypatch.setattr(module, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        module.delete_azure_resource(
            "rg-test", "Microsoft.Network/loadBalancers", "demo-lb"
        )


def test_render_delete_command_for_existing_vnet_ssh_lb() -> None:
    module = load_deploy_aci_module()
    parser = module.build_parser()
    args = parser.parse_args(
        [
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "cleanup-demo",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
            "--vnet-subnet",
            "existing-vnet/existing-subnet",
            "--access-mode",
            "ssh-lb",
            "--num-containers",
            "2",
            "--cpus",
            "8",
            "--ram",
            "32",
            "--sku",
            "standard",
            "--tcb-ports",
            "22,443",
        ]
    )

    assert module.render_delete_command(args) == [
        "deploy-aci",
        "--resource-group",
        "rg-test",
        "--name",
        "cleanup-demo",
        "--num-containers",
        "2",
        "--create-vnet",
        "False",
        "--vnet-subnet",
        "existing-vnet/existing-subnet",
        "--access-mode",
        "ssh-lb",
        "--delete",
    ]


def test_main_public_ip_flow_prints_cleanup_command_with_only_relevant_topology_flags(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_deploy_aci_module()
    commands: list[list[str]] = []

    monkeypatch.setattr(module, "run", make_successful_az_run_recorder(commands))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy-aci",
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "public-cleanup",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
        ],
    )

    module.main()

    captured = capsys.readouterr()
    assert commands[0][:7] == [
        "az",
        "deployment",
        "group",
        "create",
        "--resource-group",
        "rg-test",
        "--template-file",
    ]
    assert commands[1] == [
        "az",
        "container",
        "show",
        "--resource-group",
        "rg-test",
        "--name",
        "public-cleanup-1",
        "--query",
        "ipAddress.ip",
    ]
    assert "Cleanup command:\n" in captured.out
    assert captured.out.rstrip().endswith(
        "deploy-aci --resource-group rg-test --name public-cleanup --create-vnet False --delete"
    )
    assert "--create-nat False" not in captured.out
    assert captured.err == ""


def test_main_ssh_lb_flow_prints_cleanup_command_with_topology_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_deploy_aci_module()
    commands: list[list[str]] = []
    ssh_key_path = tmp_path / "id_rsa.pub"
    ssh_key_path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDc test@example\n")

    monkeypatch.setattr(module, "run", make_successful_az_run_recorder(commands))
    monkeypatch.setattr(
        module,
        "get_container_group_private_ip",
        lambda resource_group, container_group_name: {
            "ssh-cleanup-1": "10.0.0.4",
            "ssh-cleanup-2": "10.0.0.5",
        }[container_group_name],
    )
    monkeypatch.setattr(
        module,
        "update_load_balancer_backend_address",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        module,
        "get_public_ip_address",
        lambda resource_group, public_ip_name: {
            "ssh-cleanup-1-lb-ip": "52.160.1.10",
            "ssh-cleanup-2-lb-ip": "52.160.1.11",
        }[public_ip_name],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy-aci",
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "ssh-cleanup",
            "--access-mode",
            "ssh-lb",
            "--ssh-key",
            str(ssh_key_path),
            "--create-nat",
            "False",
            "--num-containers",
            "2",
        ],
    )

    module.main()

    captured = capsys.readouterr()
    assert len(commands) == 1
    assert commands[0][:7] == [
        "az",
        "deployment",
        "group",
        "create",
        "--resource-group",
        "rg-test",
        "--template-file",
    ]
    assert "Cleanup command:\n" in captured.out
    assert captured.out.rstrip().endswith(
        "deploy-aci --resource-group rg-test --name ssh-cleanup --num-containers 2 --create-nat False --access-mode ssh-lb --delete"
    )
    assert captured.err == ""


def test_dry_run_confidential_sku_emits_expected_public_template() -> None:
    result = run_cli(
        "--image",
        "ghcr.io/example/image:latest",
        "--resource-group",
        "rg-test",
        "--name",
        "sku-confidential",
        "--create-vnet",
        "False",
        "--create-nat",
        "False",
        "--sku",
        "confidential",
    )

    payload = parse_dry_run_payload(result)
    properties = assert_common_public_template_shape(payload, expected_name="sku-confidential")
    arm_template_builder = load_arm_template_builder_module()

    assert properties["sku"] == "Confidential"
    assert properties["confidentialComputeProperties"] == {
        "ccePolicy": arm_template_builder.CCE_POLICY
    }


def test_access_mode_ssh_lb_requires_ssh_key() -> None:
    result = run_cli(
        "--image",
        "ghcr.io/example/image:latest",
        "--resource-group",
        "rg-test",
        "--access-mode",
        "ssh-lb",
    )

    assert result.returncode == 2
    assert "--access-mode ssh-lb requires --ssh-key" in result.stderr


def test_access_mode_ssh_lb_requires_vnet_mode(tmp_path: Path) -> None:
    ssh_key_path = tmp_path / "id_rsa.pub"
    ssh_key_path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDc test@example\n")

    result = run_cli(
        "--image",
        "ghcr.io/example/image:latest",
        "--resource-group",
        "rg-test",
        "--name",
        "existing-ssh-lb",
        "--access-mode",
        "ssh-lb",
        "--ssh-key",
        str(ssh_key_path),
        "--create-vnet",
        "False",
        "--create-nat",
        "False",
    )

    assert result.returncode == 2
    assert "--access-mode ssh-lb requires VNet mode" in result.stderr


def test_dry_run_ssh_lb_existing_vnet_uses_provided_subnet_without_vnet_resource(
    tmp_path: Path,
) -> None:
    ssh_key_path = tmp_path / "id_rsa.pub"
    ssh_key_path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDc test@example\n")

    result = run_cli(
        "--image",
        "ghcr.io/example/image:latest",
        "--resource-group",
        "rg-test",
        "--name",
        "existing-ssh-lb",
        "--access-mode",
        "ssh-lb",
        "--ssh-key",
        str(ssh_key_path),
        "--create-vnet",
        "False",
        "--create-nat",
        "False",
        "--vnet-subnet",
        "existing-vnet/existing-subnet",
        "--num-containers",
        "2",
    )

    payload = parse_dry_run_payload(result)
    resources = payload["resources"]

    assert resources_by_type(resources, "Microsoft.Network/virtualNetworks") == []
    assert len(resources_by_type(resources, "Microsoft.ContainerInstance/containerGroups")) == 2
    assert len(resources_by_type(resources, "Microsoft.Network/publicIPAddresses")) == 2
    assert len(resources_by_type(resources, "Microsoft.Network/loadBalancers")) == 2

    first_group = resource_by_type_and_name(
        resources, "Microsoft.ContainerInstance/containerGroups", "existing-ssh-lb-1"
    )
    second_group = resource_by_type_and_name(
        resources, "Microsoft.ContainerInstance/containerGroups", "existing-ssh-lb-2"
    )
    assert "dependsOn" not in first_group
    assert "dependsOn" not in second_group
    assert first_group["properties"]["subnetIds"] == [
        {
            "id": "[resourceId('Microsoft.Network/virtualNetworks/subnets', 'existing-vnet', 'existing-subnet')]"
        }
    ]
    assert second_group["properties"]["subnetIds"] == [
        {
            "id": "[resourceId('Microsoft.Network/virtualNetworks/subnets', 'existing-vnet', 'existing-subnet')]"
        }
    ]
    assert first_group["properties"]["ipAddress"]["ip"] == "10.0.0.4"
    assert second_group["properties"]["ipAddress"]["ip"] == "10.0.0.5"

    subnet_id = "[resourceId('Microsoft.Network/virtualNetworks/subnets', 'existing-vnet', 'existing-subnet')]"
    first_lb = resource_by_type_and_name(
        resources, "Microsoft.Network/loadBalancers", "existing-ssh-lb-1-lb"
    )
    second_lb = resource_by_type_and_name(
        resources, "Microsoft.Network/loadBalancers", "existing-ssh-lb-2-lb"
    )
    assert_ssh_load_balancer_shape(
        first_lb,
        public_ip_name="existing-ssh-lb-1-lb-ip",
        expected_ip="10.0.0.4",
        subnet_id=subnet_id,
        expected_depends_on=[
            "[resourceId('Microsoft.Network/publicIPAddresses', 'existing-ssh-lb-1-lb-ip')]"
        ],
    )
    assert_ssh_load_balancer_shape(
        second_lb,
        public_ip_name="existing-ssh-lb-2-lb-ip",
        expected_ip="10.0.0.5",
        subnet_id=subnet_id,
        expected_depends_on=[
            "[resourceId('Microsoft.Network/publicIPAddresses', 'existing-ssh-lb-2-lb-ip')]"
        ],
    )


def test_dry_run_ssh_lb_emits_per_node_load_balancers(tmp_path: Path) -> None:
    ssh_key_path = tmp_path / "id_rsa.pub"
    ssh_key_path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDc test@example\n")

    result = run_cli(
        "--image",
        "ghcr.io/example/image:latest",
        "--resource-group",
        "rg-test",
        "--name",
        "ssh-lb-sample",
        "--access-mode",
        "ssh-lb",
        "--ssh-key",
        str(ssh_key_path),
        "--create-nat",
        "False",
        "--num-containers",
        "2",
    )

    payload = parse_dry_run_payload(result)
    resources = payload["resources"]

    assert len(resources_by_type(resources, "Microsoft.Network/virtualNetworks")) == 1
    assert len(resources_by_type(resources, "Microsoft.ContainerInstance/containerGroups")) == 2
    assert len(resources_by_type(resources, "Microsoft.Network/publicIPAddresses")) == 2
    assert len(resources_by_type(resources, "Microsoft.Network/loadBalancers")) == 2

    first_group = resource_by_type_and_name(
        resources, "Microsoft.ContainerInstance/containerGroups", "ssh-lb-sample-1"
    )
    second_group = resource_by_type_and_name(
        resources, "Microsoft.ContainerInstance/containerGroups", "ssh-lb-sample-2"
    )
    assert first_group["name"] == "ssh-lb-sample-1"
    assert second_group["name"] == "ssh-lb-sample-2"
    assert first_group["properties"]["ipAddress"] == {
        "ports": [{"port": "22", "protocol": "TCP"}],
        "type": "Private",
        "ip": "10.0.0.4",
    }
    assert second_group["properties"]["ipAddress"] == {
        "ports": [{"port": "22", "protocol": "TCP"}],
        "type": "Private",
        "ip": "10.0.0.5",
    }

    first_public_ip = resource_by_type_and_name(
        resources, "Microsoft.Network/publicIPAddresses", "ssh-lb-sample-1-lb-ip"
    )
    second_public_ip = resource_by_type_and_name(
        resources, "Microsoft.Network/publicIPAddresses", "ssh-lb-sample-2-lb-ip"
    )

    first_lb = resource_by_type_and_name(
        resources, "Microsoft.Network/loadBalancers", "ssh-lb-sample-1-lb"
    )
    second_lb = resource_by_type_and_name(
        resources, "Microsoft.Network/loadBalancers", "ssh-lb-sample-2-lb"
    )
    assert first_lb["name"] == "ssh-lb-sample-1-lb"
    assert second_lb["name"] == "ssh-lb-sample-2-lb"

    expected_vnet_dependency = "[resourceId('Microsoft.Network/virtualNetworks', 'ssh-lb-sample-vnet')]"
    subnet_id = "[resourceId('Microsoft.Network/virtualNetworks/subnets', 'ssh-lb-sample-vnet', 'default')]"

    for expected_ip, public_ip, load_balancer in [
        ("10.0.0.4", first_public_ip, first_lb),
        ("10.0.0.5", second_public_ip, second_lb),
    ]:
        assert_ssh_load_balancer_shape(
            load_balancer,
            public_ip_name=public_ip["name"],
            expected_ip=expected_ip,
            subnet_id=subnet_id,
            expected_depends_on=[
                f"[resourceId('Microsoft.Network/publicIPAddresses', '{public_ip['name']}')]",
                expected_vnet_dependency,
            ],
        )


def test_dry_run_standard_sku_ssh_lb_keeps_private_topology(tmp_path: Path) -> None:
    ssh_key_path = tmp_path / "id_rsa.pub"
    ssh_key_path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDc test@example\n")

    result = run_cli(
        "--image",
        "ghcr.io/example/image:latest",
        "--resource-group",
        "rg-test",
        "--name",
        "ssh-lb-standard",
        "--access-mode",
        "ssh-lb",
        "--ssh-key",
        str(ssh_key_path),
        "--create-nat",
        "False",
        "--sku",
        "standard",
    )

    payload = parse_dry_run_payload(result)
    resources = payload["resources"]

    assert len(resources_by_type(resources, "Microsoft.Network/virtualNetworks")) == 1
    assert len(resources_by_type(resources, "Microsoft.ContainerInstance/containerGroups")) == 1
    assert len(resources_by_type(resources, "Microsoft.Network/publicIPAddresses")) == 1
    assert len(resources_by_type(resources, "Microsoft.Network/loadBalancers")) == 1

    container_group = resource_by_type_and_name(
        resources, "Microsoft.ContainerInstance/containerGroups", "ssh-lb-standard-1"
    )
    assert container_group["properties"]["sku"] == "Standard"
    assert "confidentialComputeProperties" not in container_group["properties"]
    assert container_group["properties"]["ipAddress"] == {
        "ports": [{"port": "22", "protocol": "TCP"}],
        "type": "Private",
        "ip": "10.0.0.4",
    }

    load_balancer = resource_by_type_and_name(
        resources, "Microsoft.Network/loadBalancers", "ssh-lb-standard-1-lb"
    )
    assert_ssh_load_balancer_shape(
        load_balancer,
        public_ip_name="ssh-lb-standard-1-lb-ip",
        expected_ip="10.0.0.4",
        subnet_id="[resourceId('Microsoft.Network/virtualNetworks/subnets', 'ssh-lb-standard-vnet', 'default')]",
        expected_depends_on=[
            "[resourceId('Microsoft.Network/publicIPAddresses', 'ssh-lb-standard-1-lb-ip')]",
            "[resourceId('Microsoft.Network/virtualNetworks', 'ssh-lb-standard-vnet')]",
        ],
    )


def test_post_deploy_exec_mode_prints_exec_instructions(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_deploy_aci_module()

    module.print_post_deploy_access(
        resource_group="rg-test",
        deployment_name="sample-deploy",
        num_containers=2,
        access_mode="exec",
        ssh_key_path=None,
        vnet_name="sample-vnet",
        subnet_name="default",
    )

    captured = capsys.readouterr()
    assert captured.out == (
        "Deployed\n"
        "Connect to container 1 using:\n"
        "az container exec --resource-group rg-test --name sample-deploy-1 --exec-command /bin/bash\n"
        "\n"
        "Connect to container 2 using:\n"
        "az container exec --resource-group rg-test --name sample-deploy-2 --exec-command /bin/bash\n"
        "\n"
    )
    assert captured.err == ""


def test_print_post_deploy_access_includes_cleanup_command_for_exec_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_deploy_aci_module()

    module.print_post_deploy_access(
        resource_group="rg-test",
        deployment_name="cleanup-demo",
        num_containers=1,
        access_mode="exec",
        ssh_key_path=None,
        vnet_name="cleanup-demo-vnet",
        subnet_name="default",
        delete_command=[
            "deploy-aci",
            "--resource-group",
            "rg-test",
            "--name",
            "cleanup-demo",
            "--delete",
        ],
    )

    captured = capsys.readouterr()
    assert captured.out == (
        "Deployed\n"
        "Connect to container 1 using:\n"
        "az container exec --resource-group rg-test --name cleanup-demo-1 --exec-command /bin/bash\n"
        "\n"
        "Cleanup command:\n"
        "deploy-aci --resource-group rg-test --name cleanup-demo --delete\n"
    )
    assert captured.err == ""


def test_post_deploy_ssh_lb_warns_updates_backend_and_prints_ssh(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_deploy_aci_module()
    backend_updates: list[tuple[str, str, str, str, str, str]] = []

    def fake_get_private_ip(resource_group: str, container_group_name: str) -> str:
        return {
            "sample-deploy-1": "10.0.0.44",
            "sample-deploy-2": "10.0.0.5",
        }[container_group_name]

    def fake_update_backend(
        resource_group: str,
        lb_name: str,
        backend_name: str,
        ip_address: str,
        vnet_name: str,
        subnet_name: str,
    ) -> None:
        backend_updates.append(
            (resource_group, lb_name, backend_name, ip_address, vnet_name, subnet_name)
        )

    def fake_get_public_ip(resource_group: str, public_ip_name: str) -> str:
        return {
            "sample-deploy-1-lb-ip": "52.160.1.10",
            "sample-deploy-2-lb-ip": "52.160.1.11",
        }[public_ip_name]

    monkeypatch.setattr(module, "get_container_group_private_ip", fake_get_private_ip)
    monkeypatch.setattr(module, "update_load_balancer_backend_address", fake_update_backend)
    monkeypatch.setattr(module, "get_public_ip_address", fake_get_public_ip)

    module.print_post_deploy_access(
        resource_group="rg-test",
        deployment_name="sample-deploy",
        num_containers=2,
        access_mode="ssh-lb",
        ssh_key_path="~/.ssh/id_test",
        vnet_name="sample-vnet",
        subnet_name="default",
    )

    captured = capsys.readouterr()
    assert backend_updates == [
        (
            "rg-test",
            "sample-deploy-1-lb",
            "sample-deploy-1-lb-backend-address",
            "10.0.0.44",
            "sample-vnet",
            "default",
        )
    ]
    assert captured.out == (
        "Deployed\n"
        "Warning: sample-deploy-1 requested private IP 10.0.0.4 but Azure assigned 10.0.0.44. Updating load balancer backend address.\n"
        "Connect to container 1 using:\n"
        "ssh -i ~/.ssh/id_test root@52.160.1.10 -p 22\n"
        "\n"
        "Connect to container 2 using:\n"
        "ssh -i ~/.ssh/id_test root@52.160.1.11 -p 22\n"
        "\n"
    )
    assert captured.err == ""


def test_post_deploy_ssh_lb_prints_manual_retry_when_backend_update_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_deploy_aci_module()

    monkeypatch.setattr(
        module,
        "get_container_group_private_ip",
        lambda resource_group, container_group_name: "10.0.0.44",
    )

    def failing_update_backend(
        resource_group: str,
        lb_name: str,
        backend_name: str,
        ip_address: str,
        vnet_name: str,
        subnet_name: str,
    ) -> None:
        raise subprocess.CalledProcessError(returncode=3, cmd=["az", "network", "lb"])

    monkeypatch.setattr(module, "update_load_balancer_backend_address", failing_update_backend)
    monkeypatch.setattr(
        module,
        "get_public_ip_address",
        lambda resource_group, public_ip_name: "52.160.1.10",
    )

    module.print_post_deploy_access(
        resource_group="rg-test",
        deployment_name="sample-deploy",
        num_containers=1,
        access_mode="ssh-lb",
        ssh_key_path="~/.ssh/id_test",
        vnet_name="sample-vnet",
        subnet_name="default",
    )

    captured = capsys.readouterr()
    assert "Manual retry:" in captured.out
    assert (
        "az network lb address-pool address update --resource-group rg-test --lb-name sample-deploy-1-lb"
        in captured.out
    )
    assert "ssh -i ~/.ssh/id_test root@52.160.1.10 -p 22" in captured.out
    assert captured.err == ""


def test_post_deploy_ssh_lb_falls_back_to_public_ip_resource_name(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_deploy_aci_module()

    monkeypatch.setattr(
        module,
        "get_container_group_private_ip",
        lambda resource_group, container_group_name: "10.0.0.4",
    )
    monkeypatch.setattr(
        module,
        "update_load_balancer_backend_address",
        lambda *args, **kwargs: None,
    )

    def failing_public_ip_lookup(resource_group: str, public_ip_name: str) -> str:
        raise subprocess.CalledProcessError(returncode=1, cmd=["az", "network", "public-ip", "show"])

    monkeypatch.setattr(module, "get_public_ip_address", failing_public_ip_lookup)

    module.print_post_deploy_access(
        resource_group="rg-test",
        deployment_name="sample-deploy",
        num_containers=1,
        access_mode="ssh-lb",
        ssh_key_path="~/.ssh/id_test",
        vnet_name="sample-vnet",
        subnet_name="default",
    )

    captured = capsys.readouterr()
    assert captured.out == (
        "Deployed\n"
        "Connect to container 1 using:\n"
        "Resolve public IP resource sample-deploy-1-lb-ip manually, then run: ssh -i ~/.ssh/id_test root@<public-ip> -p 22\n"
        "\n"
    )
    assert captured.err == ""


@pytest.mark.parametrize(
    ("private_ip_result", "expected_warning"),
    [
        (
            subprocess.CalledProcessError(returncode=1, cmd=["az", "container", "show"]),
            "Warning: unable to determine actual private IP for sample-deploy-1. Skipping load balancer backend update.",
        ),
        (
            "",
            "Warning: sample-deploy-1 did not report a private IP. Skipping load balancer backend update.",
        ),
    ],
)
def test_post_deploy_ssh_lb_private_ip_lookup_failure_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    private_ip_result: str | subprocess.CalledProcessError,
    expected_warning: str,
) -> None:
    module = load_deploy_aci_module()
    backend_updates: list[tuple[str, str, str, str, str, str]] = []

    def fake_get_private_ip(resource_group: str, container_group_name: str) -> str:
        if isinstance(private_ip_result, Exception):
            raise private_ip_result
        return private_ip_result

    def fake_update_backend(
        resource_group: str,
        lb_name: str,
        backend_name: str,
        ip_address: str,
        vnet_name: str,
        subnet_name: str,
    ) -> None:
        backend_updates.append(
            (resource_group, lb_name, backend_name, ip_address, vnet_name, subnet_name)
        )

    monkeypatch.setattr(module, "get_container_group_private_ip", fake_get_private_ip)
    monkeypatch.setattr(module, "update_load_balancer_backend_address", fake_update_backend)
    monkeypatch.setattr(
        module,
        "get_public_ip_address",
        lambda resource_group, public_ip_name: "52.160.1.10",
    )

    module.print_post_deploy_access(
        resource_group="rg-test",
        deployment_name="sample-deploy",
        num_containers=1,
        access_mode="ssh-lb",
        ssh_key_path="~/.ssh/id_test",
        vnet_name="sample-vnet",
        subnet_name="default",
    )

    captured = capsys.readouterr()
    assert backend_updates == []
    assert captured.out == (
        "Deployed\n"
        f"{expected_warning}\n"
        "Connect to container 1 using:\n"
        "ssh -i ~/.ssh/id_test root@52.160.1.10 -p 22\n"
        "\n"
    )
    assert captured.err == ""
