import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "deploy-aci-arm" / "deploy-aci"
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
CLI_TIMEOUT_SECONDS = 30

SUCCESS_SCENARIOS = [
    (
        "default-new-vnet-with-nat",
        [
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "baseline-nat",
        ],
    ),
    (
        "new-vnet-without-nat",
        [
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "baseline-no-nat",
            "--create-nat",
            "False",
            "--vnet-subnet",
            "custom-vnet/custom-subnet",
        ],
    ),
    (
        "existing-vnet-with-bool-workaround",
        [
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "baseline-existing",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
            "--vnet-subnet",
            "existing-vnet/existing-subnet",
        ],
    ),
    (
        "public-ip-with-bool-workaround",
        [
            "--image",
            "ghcr.io/example/image:latest",
            "--resource-group",
            "rg-test",
            "--name",
            "baseline-public",
            "--create-vnet",
            "False",
            "--create-nat",
            "False",
        ],
    ),
]

FAILURE_SCENARIO = (
    "existing-vnet-bool-bug.stdout.txt",
    [
        "--image",
        "ghcr.io/example/image:latest",
        "--resource-group",
        "rg-test",
        "--name",
        "baseline-bug",
        "--create-vnet",
        "False",
        "--vnet-subnet",
        "existing-vnet/existing-subnet",
    ],
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args, "--dry-run"],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=CLI_TIMEOUT_SECONDS,
    )


def canonicalize_dry_run_json(stdout: str) -> str:
    decoder = json.JSONDecoder()
    offset = 0

    for line in stdout.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("{"):
            json_start = offset + (len(line) - len(stripped))
            break
        offset += len(line)
    else:
        raise ValueError(
            "stdout must contain exactly one decodable JSON object payload after optional leading log lines"
        )

    candidate = stdout[json_start:]
    try:
        payload, end_index = decoder.raw_decode(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "stdout must contain exactly one decodable JSON object payload after optional leading log lines"
        ) from exc

    if not isinstance(payload, dict) or candidate[end_index:].strip():
        raise ValueError(
            "stdout must contain exactly one decodable JSON object payload after optional leading log lines"
        )

    result = subprocess.run(
        ["jq", "-S", "."],
        input=candidate[:end_index] + "\n",
        check=True,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )
    return result.stdout


def read_golden(name: str) -> str:
    path = GOLDEN_DIR / name
    if not path.exists():
        raise AssertionError(f"missing golden file: {path}. Run tests/generate_arm_goldens.py")
    return path.read_text(encoding="utf-8")
