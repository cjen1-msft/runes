from deploy_aci_cli_support import (
    FAILURE_SCENARIO,
    GOLDEN_DIR,
    SUCCESS_SCENARIOS,
    canonicalize_dry_run_json,
    run_cli,
)


def main() -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    for golden_name, args in SUCCESS_SCENARIOS:
        result = run_cli(*args)
        if result.returncode != 0:
            raise SystemExit(
                f"scenario {golden_name} failed with exit code {result.returncode}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        (GOLDEN_DIR / f"{golden_name}.json").write_text(
            canonicalize_dry_run_json(result.stdout),
            encoding="utf-8",
        )

    failure_golden_name, failure_args = FAILURE_SCENARIO
    failure_result = run_cli(*failure_args)
    if failure_result.returncode != 1:
        raise SystemExit(
            f"failure scenario returned {failure_result.returncode}, expected 1\n"
            f"stdout:\n{failure_result.stdout}\n"
            f"stderr:\n{failure_result.stderr}"
        )

    (GOLDEN_DIR / failure_golden_name).write_text(
        failure_result.stdout,
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
