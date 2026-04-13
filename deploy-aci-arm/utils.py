import argparse
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import hashlib
import os
import sys
import textwrap

import arm_template_builder as tb


@dataclass
class ActionContext:
    dry_run: bool
    verbose: bool
    use_existing_resource_group: bool
    storage_key: str | None = None


class DeploymentActionKind(Enum):
    DEPLOY_ARM = "deploy_arm"
    RESOURCE_GROUP = "create_resource_group"
    STORAGE_ACCOUNT = "create_storage_account"
    STORAGE_SHARE = "create_storage_share"
    FETCH_STORAGE_ACCOUNT_KEY = "fetch_storage_account_key"
    LOAD_BALANCER_BACKEND_FIXUP = "load_balancer_backend_fixup"
    PRINT_SSH_ACCESS = "print_ssh_access"


class DeploymentAction:
    def __init__(self, kind: DeploymentActionKind):
        self.kind = kind


class DeployArmAction(DeploymentAction):
    def __init__(self, resource_group: str, template):
        super().__init__(DeploymentActionKind.DEPLOY_ARM)
        self.resource_group = resource_group
        self.template = template


class ResourceGroupAction(DeploymentAction):
    def __init__(self, resource_group: str, region: str):
        super().__init__(DeploymentActionKind.RESOURCE_GROUP)
        self.resource_group = resource_group
        self.region = region


class StorageAccountAction(DeploymentAction):
    def __init__(self, resource_group: str, account_name: str, region: str):
        super().__init__(DeploymentActionKind.STORAGE_ACCOUNT)
        self.resource_group = resource_group
        self.account_name = account_name
        self.region = region


class StorageShareAction(DeploymentAction):
    def __init__(self, resource_group: str, account_name: str, share_name: str):
        super().__init__(DeploymentActionKind.STORAGE_SHARE)
        self.resource_group = resource_group
        self.account_name = account_name
        self.share_name = share_name


class FetchStorageAccountKeyAction(DeploymentAction):
    def __init__(self, resource_group: str, account_name: str):
        super().__init__(DeploymentActionKind.FETCH_STORAGE_ACCOUNT_KEY)
        self.resource_group = resource_group
        self.account_name = account_name


class LoadBalancerBackendFixupAction(DeploymentAction):
    def __init__(
        self,
        resource_group: str,
        container_group_name: str,
        load_balancer_name: str,
        requested_private_ip: str,
        vnet_name: str,
        subnet_name: str,
    ):
        super().__init__(DeploymentActionKind.LOAD_BALANCER_BACKEND_FIXUP)
        self.resource_group = resource_group
        self.container_group_name = container_group_name
        self.load_balancer_name = load_balancer_name
        self.requested_private_ip = requested_private_ip
        self.vnet_name = vnet_name
        self.subnet_name = subnet_name


class PrintSSHAccessAction(DeploymentAction):
    def __init__(
        self,
        resource_group: str,
        ssh_key_path: str,
        public_ip_names: list[str],
    ):
        super().__init__(DeploymentActionKind.PRINT_SSH_ACCESS)
        self.resource_group = resource_group
        self.ssh_key_path = ssh_key_path
        self.public_ip_names = public_ip_names


@dataclass(frozen=True)
class ParsedAzureFileMount:
    share_name: str
    mount_path: str


def effective_deployment_resource_group(args: argparse.Namespace) -> str:
    if args.resource_group is not None:
        return args.resource_group
    return f"{args.resource_group_prefix}-{args.name}"


def parse_azure_file_mount_spec(spec: str) -> ParsedAzureFileMount:
    values: dict[str, str] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if entry == "":
            continue
        key, separator, value = entry.partition("=")
        if separator == "":
            raise ValueError("must use key=value pairs separated by commas")
        normalized_key = key.strip()
        if normalized_key not in {"share", "path"}:
            raise ValueError(f"contains unsupported key '{normalized_key}'")
        if normalized_key in values:
            raise ValueError(f"contains duplicate key '{normalized_key}'")
        values[normalized_key] = value.strip()

    share_name = values.get("share", "").strip()
    mount_path = values.get("path", "").strip()
    return ParsedAzureFileMount(
        share_name=share_name,
        mount_path=mount_path,
    )


def validate_parsed_azure_file_mount(
    parser: argparse.ArgumentParser,
    mount: ParsedAzureFileMount,
    mount_index: int,
) -> None:
    mount_label = f"--azure-file-mount #{mount_index + 1}"
    if mount.share_name == "":
        parser.error(f"{mount_label} requires share=<name>")
    if mount.mount_path == "":
        parser.error(f"{mount_label} requires path=<absolute-path>")
    if not mount.mount_path.startswith("/"):
        parser.error(f"{mount_label} path must be an absolute path")


def derived_share_name(share_prefix: str, node_index: int) -> str:
    return f"{share_prefix.rstrip('-')}-{node_index + 1}"


def derived_storage_account_name(args: argparse.Namespace) -> str:
    seed = f"{effective_deployment_resource_group(args)}-{args.name}".lower()
    sanitized = "".join(ch for ch in seed if ch.isalnum())
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6]
    prefix = sanitized[:18]
    name = f"{prefix}{digest}"
    return (name or f"aci{digest}")[:24]


def build_per_node_azure_file_share(
    args: argparse.Namespace,
    cidx: int,
    build_context: dict[str, object],
) -> Callable[[ActionContext], tb.AzureFileMount] | None:
    mount_specs = args.azure_file_mount
    if len(mount_specs) == 0:
        return None

    if len(mount_specs) == 1:
        mount_spec = mount_specs[0]
    else:
        mount_spec = mount_specs[cidx]

    mount = parse_azure_file_mount_spec(mount_spec)
    storage_account_name = build_context.get("storage_account_name")
    if storage_account_name is None:
        storage_account_name = derived_storage_account_name(args)
        build_context["actions"].append(
            StorageAccountAction(
                resource_group=effective_deployment_resource_group(args),
                account_name=storage_account_name,
                region=args.region,
            )
        )
        build_context["storage_account_name"] = storage_account_name
        build_context["storage_account_key"] = None
        build_context["actions"].append(
            FetchStorageAccountKeyAction(
                resource_group=effective_deployment_resource_group(args),
                account_name=storage_account_name,
            )
        )

    share_name = (
        derived_share_name(mount.share_name, cidx)
        if args.azure_file_share_prefix
        else mount.share_name
    )
    share_id = (storage_account_name, share_name)
    if "storage_shares" not in build_context:
        build_context["storage_shares"] = set()
    if share_id not in build_context["storage_shares"]:
        build_context["storage_shares"].add(share_id)
        build_context["actions"].append(
            StorageShareAction(
                resource_group=effective_deployment_resource_group(args),
                account_name=storage_account_name,
                share_name=share_name,
            )
        )

    def build_mount(context: ActionContext) -> tb.AzureFileMount:
        return tb.AzureFileMount(
            storage_account_name=storage_account_name,
            share_name=share_name,
            volume_name=f"azurefiles{cidx + 1}",
            mount_path=mount.mount_path,
            storage_account_key=context.storage_key,
        )

    return build_mount


def get_ssh_key(ssh_key: str) -> str:
    with open(os.path.expanduser(ssh_key), "r") as f:
        return f.read().strip()


def new_vnet_with_nat(
    vnet_name: str,
    subnet_name: str,
    nat_name: str,
    pub_ip_name: str,
    region: str,
) -> list:
    pub_ip = tb.ResourcePublicIP(
        name=pub_ip_name,
        region=region,
        sku="Standard",
        allocation_method="Static",
    )

    address_space = "10.0.0.0/16"

    nat_gateway = tb.ResourceNAT(
        name=nat_name,
        region=region,
        public_ip=pub_ip,
        address_space=address_space,
    )

    vnet = tb.ResourceVNet(
        name=vnet_name,
        region=region,
        address_space=address_space,
        subnets=[
            tb.VNetSubnet(
                name=subnet_name,
                address_prefix="10.0.0.0/24",
                nat_gateway=nat_gateway,
                delegations=[
                    {
                        "name": "aci-delegation",
                        "properties": {
                            "serviceName": "Microsoft.ContainerInstance/containerGroups"
                        },
                    }
                ],
            )
        ],
    )

    return [pub_ip, nat_gateway, vnet]


def load_balancer_name(container_group_name: str) -> str:
    return f"{container_group_name}-lb"


def load_balancer_public_ip_name(container_group_name: str) -> str:
    return f"{container_group_name}-lb-ip"


def load_balancer_backend_address_name(load_balancer_name: str) -> str:
    return f"{load_balancer_name}-backend-address"


def ssh_private_key_path(ssh_key_path: str) -> str:
    if ssh_key_path.endswith(".pub"):
        return ssh_key_path[: -len(".pub")]
    return ssh_key_path


def default_container_name():
    return f"test-{os.urandom(2).hex()}"


EPILOG = textwrap.dedent(
    """
Example usage:

Deploy a container into an existing resource group:
deploy-aci --image ghcr.io/myrepo/myimage --resource-group my-rg --name deploy1 --use-existing-resource-group --ssh-key ~/.ssh/id_rsa.pub

Deploy 5 large containers into a new vnet with a NAT gateway:
deploy-aci --image ghcr.io/myrepo/myimage --resource-group my-rg --name deploy2 --ssh-key ~/.ssh/id_rsa.pub --cpus 64 --ram 256 --num-containers 5 --vnet-subnet myvnet/mysubnet

Deploy a container into an existing vnet and subnet (myvnet/mysubnet):
deploy-aci --image ghcr.io/myrepo/myimage --resource-group my-rg --name deploy3 --ssh-key ~/.ssh/id_rsa.pub --vnet-subnet myvnet/mysubnet --create-vnet False

Deploy a 2-node cluster into a managed derived resource group with one shared storage account and per-node Azure Files shares:
deploy-aci --image ghcr.io/myrepo/myimage --resource-group-prefix my-rg --name cluster2 --ssh-key ~/.ssh/id_rsa.pub --sku standard --num-containers 2 --azure-file-share-prefix --azure-file-mount share=workspace,path=/mnt/workspace

Deploy into an exact named resource group and let deploy-aci create/delete it:
deploy-aci --image ghcr.io/myrepo/myimage --resource-group cluster2-rg --name cluster2 --ssh-key ~/.ssh/id_rsa.pub --sku standard --num-containers 2

Delete a managed deployment by deleting its target resource group:
deploy-aci --resource-group-prefix my-rg --name cluster2 --delete
"""
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Start up a ACI deployment with sensible defaults.",
        epilog=EPILOG,
    )
    parser.add_argument("--image", help="The image to use for all containers")
    parser.add_argument(
        "--azure-auth", help="Use az command to do authentication", action="store_true"
    )
    resource_group_group = parser.add_mutually_exclusive_group(required=True)
    resource_group_group.add_argument(
        "--resource-group",
        help="Deploy into this exact resource group name.",
    )
    resource_group_group.add_argument(
        "--resource-group-prefix",
        help=(
            "Use a derived resource group named "
            "<resource-group-prefix>-<name>."
        ),
    )
    parser.add_argument(
        "--ssh-key",
        help="SSH public key for the deployment. Required unless --delete is set.",
        default=None,
    )
    parser.add_argument(
        "--name",
        help="The name for the deployment",
        default=default_container_name(),
    )
    parser.add_argument(
        "--region",
        help="The region to use for the container",
        default="northeurope",
    )
    parser.add_argument(
        "--cpus",
        type=int,
        help="The number of CPUs to allocate to each container",
        default=4,
    )
    parser.add_argument(
        "--ram",
        type=int,
        help="The amount of RAM (GB) to allocate to each container",
        default=16,
    )
    parser.add_argument(
        "--tcp-ports",
        type=str,
        help="Comma separated list of ports to open on each container",
        default="22",
    )
    parser.add_argument(
        "--udp-ports",
        type=str,
        help="Comma separated list of UDP ports to open on each container",
    )
    parser.add_argument(
        "--num-containers",
        type=int,
        default=1,
        help="Number of container groups to deploy",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned Azure commands and the rendered ARM template without executing them.",
    )
    parser.add_argument(
        "--sku",
        choices=["confidential", "standard"],
        default="confidential",
        help="ACI SKU to deploy",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the target resource group for this deployment when managed by deploy-aci",
    )
    parser.add_argument(
        "--use-existing-resource-group",
        action="store_true",
        help=(
            "Use the target resource group as pre-existing. When set, deploy-aci "
            "does not create or delete the resource group."
        ),
    )
    parser.add_argument(
        "--azure-file-mount",
        action="append",
        default=[],
        help=(
            "Repeatable Azure Files mount spec using key=value pairs. "
            "Supported keys: share, path. "
            "One mount broadcasts to all nodes; multiple mounts must match --num-containers. "
            "deploy-aci always creates one new storage account per deployment and reuses it across nodes."
        ),
    )
    parser.add_argument(
        "--azure-file-share-prefix",
        action="store_true",
        help=(
            "Treat share=... as a prefix and derive per-node shares like prefix-1, prefix-2. "
            "When omitted, share=... is used as-is."
        ),
    )
    parser.add_argument(
        "--access-mode",
        choices=["exec", "ssh-lb"],
        default=argparse.SUPPRESS,
        help="Deprecated: how to access deployed containers",
    )
    parser.add_argument(
        "--create-nat",
        default=argparse.SUPPRESS,
        help="Deprecated: create a NAT gateway for the VNet",
    )
    parser.add_argument(
        "--create-vnet",
        default=argparse.SUPPRESS,
        help="Deprecated: create a new VNet for the deployment",
    )
    parser.add_argument(
        "--vnet-subnet",
        type=str,
        default=argparse.SUPPRESS,
        help=(
            "Deprecated: the name of the vnet and subnet to use, in the form vnet/subnet. "
            "If not specified, and --create-vnet is set, it will use <name>-vnet/default. "
            "Existing VNets remain same-resource-group only, including with derived deployment resource groups."
        ),
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    deprecated_arg_names = [
        "access_mode",
        "create_nat",
        "create_vnet",
        "vnet_subnet",
    ]
    used_deprecated_args = [
        f"--{name.replace('_', '-')}"
        for name in deprecated_arg_names
        if hasattr(args, name)
    ]
    if used_deprecated_args:
        print(
            "WARNING: deprecated arguments in use: " + ", ".join(used_deprecated_args),
            file=sys.stderr,
        )

    if not args.delete and not args.image:
        parser.error("--image is required unless --delete is set")

    if not args.delete and not args.ssh_key:
        parser.error("--ssh-key is required unless --delete is set")

    if args.num_containers < 1:
        parser.error("--num-containers must be at least 1")

    if args.delete and args.use_existing_resource_group:
        parser.error("--delete does not support --use-existing-resource-group")

    if args.use_existing_resource_group and args.resource_group_prefix is not None:
        parser.error(
            "--use-existing-resource-group requires --resource-group, not --resource-group-prefix"
        )

    parsed_mounts = []
    for mount_index, mount_spec in enumerate(args.azure_file_mount):
        try:
            parsed_mount = parse_azure_file_mount_spec(mount_spec)
        except ValueError as exc:
            parser.error(f"--azure-file-mount #{mount_index + 1} {exc}")
        validate_parsed_azure_file_mount(parser, parsed_mount, mount_index)
        parsed_mounts.append(parsed_mount)

    if len(parsed_mounts) > 1 and len(parsed_mounts) != args.num_containers:
        parser.error("multiple --azure-file-mount values must match --num-containers")

    if args.azure_file_share_prefix and len(parsed_mounts) == 0:
        parser.error(
            "--azure-file-share-prefix requires at least one --azure-file-mount"
        )
