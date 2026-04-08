import argparse
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY_ACI_DIR = REPO_ROOT / "deploy-aci-arm"
DEPLOY_ACI_PATH = DEPLOY_ACI_DIR / "deploy-aci"


def load_deploy_aci_module():
    module_name = "deploy_aci_cli_under_test"
    sys.path.insert(0, str(DEPLOY_ACI_DIR))
    try:
        loader = importlib.machinery.SourceFileLoader(module_name, str(DEPLOY_ACI_PATH))
        spec = importlib.util.spec_from_loader(module_name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


@pytest.fixture()
def deploy_aci_module():
    return load_deploy_aci_module()


@pytest.fixture()
def azure_file_key_file(tmp_path: pathlib.Path) -> pathlib.Path:
    key_file = tmp_path / "azure-file.key"
    key_file.write_text("super-secret-key\n", encoding="utf-8")
    return key_file


def parse_args(deploy_aci_module, *argv: str) -> argparse.Namespace:
    return deploy_aci_module.build_parser().parse_args(list(argv))


def test_validate_args_rejects_partial_azure_file_config(
    deploy_aci_module, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = deploy_aci_module.build_parser()
    args = parse_args(
        deploy_aci_module,
        "--resource-group",
        "rg",
        "--image",
        "image",
        "--azure-file-storage-account",
        "mystorage",
    )

    with pytest.raises(SystemExit):
        deploy_aci_module.validate_args(parser, args)

    assert "Azure Files mount requires all of" in capsys.readouterr().err


def test_validate_args_rejects_relative_azure_file_mount_path(
    deploy_aci_module,
    azure_file_key_file: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = deploy_aci_module.build_parser()
    args = parse_args(
        deploy_aci_module,
        "--resource-group",
        "rg",
        "--image",
        "image",
        "--azure-file-storage-account",
        "mystorage",
        "--azure-file-share",
        "myshare",
        "--azure-file-mount-path",
        "mnt/azure",
        "--azure-file-account-key-file",
        str(azure_file_key_file),
    )

    with pytest.raises(SystemExit):
        deploy_aci_module.validate_args(parser, args)

    assert "must be an absolute path" in capsys.readouterr().err


def test_build_generated_resources_emits_azure_file_volume_and_mount(
    deploy_aci_module,
    azure_file_key_file: pathlib.Path,
) -> None:
    parser = deploy_aci_module.build_parser()
    args = parse_args(
        deploy_aci_module,
        "--resource-group",
        "rg",
        "--image",
        "ghcr.io/example/image",
        "--sku",
        "standard",
        "--create-vnet",
        "False",
        "--create-nat",
        "False",
        "--azure-file-storage-account",
        "mystorage",
        "--azure-file-share",
        "myshare",
        "--azure-file-mount-path",
        "/mnt/azure",
        "--azure-file-account-key-file",
        str(azure_file_key_file),
    )
    deploy_aci_module.validate_args(parser, args)

    resources, _ = deploy_aci_module.build_generated_resources(args)
    container_group = next(
        resource
        for resource in resources
        if isinstance(resource, deploy_aci_module.tb.ResourceACIGroup)
    )
    rendered = container_group.to_dict()

    assert rendered["properties"]["volumes"] == [
        {
            "name": "azurefiles",
            "azureFile": {
                "shareName": "myshare",
                "storageAccountName": "mystorage",
                "storageAccountKey": "[parameters('azureFileStorageAccountKey')]",
            },
        }
    ]
    assert rendered["properties"]["containers"][0]["properties"]["volumeMounts"] == [
        {
            "name": "azurefiles",
            "mountPath": "/mnt/azure",
            "readOnly": False,
        }
    ]


def test_dry_run_template_parameterizes_azure_file_secret(
    deploy_aci_module,
    azure_file_key_file: pathlib.Path,
) -> None:
    parser = deploy_aci_module.build_parser()
    args = parse_args(
        deploy_aci_module,
        "--resource-group",
        "rg",
        "--image",
        "ghcr.io/example/image",
        "--sku",
        "standard",
        "--create-vnet",
        "False",
        "--create-nat",
        "False",
        "--azure-file-storage-account",
        "mystorage",
        "--azure-file-share",
        "myshare",
        "--azure-file-mount-path",
        "/mnt/azure",
        "--azure-file-account-key-file",
        str(azure_file_key_file),
    )
    deploy_aci_module.validate_args(parser, args)

    resources, _ = deploy_aci_module.build_generated_resources(args)
    mount_config = deploy_aci_module.build_azure_file_mount_config(args)
    template = deploy_aci_module.tb.ARMTemplate(
        resources,
        parameters=deploy_aci_module.arm_parameters_for_mount(mount_config),
    )
    rendered = json.loads(template.to_json())

    assert rendered["parameters"] == {
        "azureFileStorageAccountKey": {
            "type": "secureString",
            "metadata": {
                "description": "Storage account key for Azure Files mount"
            },
        }
    }
    assert "super-secret-key" not in template.to_json()


def test_main_uses_parameters_file_without_printing_secret(
    deploy_aci_module,
    azure_file_key_file: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands = []

    def fake_run(command, check=False, capture_output=False, text=False):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(deploy_aci_module, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy-aci",
            "--resource-group",
            "rg",
            "--image",
            "ghcr.io/example/image",
            "--sku",
            "standard",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
            "--name",
            "sample",
            "--azure-file-storage-account",
            "mystorage",
            "--azure-file-share",
            "myshare",
            "--azure-file-mount-path",
            "/mnt/azure",
            "--azure-file-account-key-file",
            str(azure_file_key_file),
        ],
    )

    deploy_aci_module.main()

    deploy_command = next(
        command
        for command in commands
        if command[:4] == ["az", "deployment", "group", "create"]
    )
    assert "--parameters" in deploy_command
    assert "super-secret-key" not in " ".join(deploy_command)

    output = capsys.readouterr().out
    assert "super-secret-key" not in output
