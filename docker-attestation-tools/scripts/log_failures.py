#!/usr/bin/env python3
import argparse
import subprocess
import time
import datetime
import pathlib
import shlex
import sys


def log_failure(log_dir, workspace, iteration, returnobj):
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    fail_dir = log_dir / f"fail_{iteration}_ts{ts}"
    fail_dir.mkdir(parents=True, exist_ok=False)
    if returnobj is not None:
        with open(fail_dir / "out", "w") as f:
            f.write(returnobj.stdout)
    if workspace.exists():
        workspace.rename(fail_dir / "workspace")
    return fail_dir


def main():
    p = argparse.ArgumentParser(
        description="Repeatedly run a command until duration elapses; on failure dump output to logs."
    )
    p.add_argument(
        "--duration",
        type=float,
        default=0.5,
        help="Total duration to run (hours)",
    )
    p.add_argument(
        "--log-dir",
        default="failed-logs",
        help="Directory to write failure logs",
    )
    p.add_argument(
        "--stop-on-fail",
        action="store_true",
        help="Stop after first failure (default: keep looping)",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Sleep seconds between runs",
    )
    p.add_argument(
        "--workspace-dir",
        type=str,
        default="workspace",
        help="Workspace directory, moved when a failure occcurs",
    )
    p.add_argument(
      "command",
      help = "Executable",
    )
    p.add_argument(
      "args_remainder",
      nargs=argparse.REMAINDER,
      help="Command to run (shell-style string or -- use --args ... form)",
    )
    args = p.parse_args()

    log_dir = pathlib.Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    workspace = pathlib.Path(args.workspace_dir)

    deadline = time.time() + args.duration * 3600
    iteration = 0
    failures = 0

    cmd = [args.command] + args.args_remainder if args.args_remainder else cmd

    print(f"Starting loop for up to {args.duration:.2f}hr: {' '.join(cmd)}")
    while time.time() < deadline:
        iteration += 1
        start = time.time()
        try:
            cp = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        except Exception as e:
            failures += 1
            log_path = log_failure(log_dir, workspace, iteration, None)
            print(
                f"[ITER {iteration}] EXCEPTION -> logged to {log_path}", file=sys.stderr
            )
            if args.stop_on_fail:
                break
            continue

        if cp.returncode != 0:
            failures += 1
            log_path = log_failure(log_dir, workspace, iteration, cp)
            print(f"[ITER {iteration}] FAIL rc={cp.returncode} -> {log_path}")
            if args.stop_on_fail:
                break
        else:
            dur = time.time() - start
            print(f"[ITER {iteration}] OK (rc=0, {dur:.3f}s)")

        if args.sleep:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(args.sleep, max(0, remaining)))

    print(f"Done. Iterations={iteration} Failures={failures}")
    if failures:
        print(f"Failure logs in: {log_dir}")


if __name__ == "__main__":
    main()
