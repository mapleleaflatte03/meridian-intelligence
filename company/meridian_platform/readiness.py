#!/usr/bin/env python3
"""
One-command operational readiness check for Meridian.

Usage:
  python3 company/meridian_platform/readiness.py
  python3 company/meridian_platform/readiness.py --json
"""
import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys

from treasury import treasury_snapshot


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANY_DIR = os.path.dirname(PLATFORM_DIR)
WORKSPACE = os.path.dirname(COMPANY_DIR)
NIGHT_SHIFT_DIR = os.path.join(WORKSPACE, "night-shift")
SUBSCRIPTIONS_PY = os.path.join(COMPANY_DIR, "subscriptions.py")
CI_VERTICAL_PY = os.path.join(PLATFORM_DIR, "ci_vertical.py")


def _run(cmd, cwd=WORKSPACE, timeout=30):
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": 124,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
        }


def _latest_brief():
    briefs = sorted(glob.glob(os.path.join(NIGHT_SHIFT_DIR, "brief-*.md")))
    if not briefs:
        return {"exists": False, "path": None, "date": None}
    path = briefs[-1]
    return {
        "exists": True,
        "path": path,
        "date": os.path.basename(path).replace("brief-", "").replace(".md", ""),
    }


def _delivery_targets():
    result = _run([sys.executable, SUBSCRIPTIONS_PY, "deliver-list"], timeout=15)
    targets = []
    if result["stdout"]:
        targets = [line.strip() for line in result["stdout"].splitlines() if line.strip()]
    return {
        "ok": result["ok"],
        "count": len(targets),
        "targets": targets,
        "stderr": result["stderr"],
    }


def collect():
    runtime_health = _run(["openclaw", "health"], timeout=20)
    pong = _run(
        ["openclaw", "agent", "--agent", "main", "--message", "respond with PONG", "--timeout", "15000"],
        timeout=25,
    )
    preflight = _run([sys.executable, CI_VERTICAL_PY, "preflight"], timeout=20)
    treasury = treasury_snapshot()
    brief = _latest_brief()
    targets = _delivery_targets()

    runtime_ok = runtime_health["ok"] and pong["ok"] and pong["stdout"] == "PONG"
    preflight_ok = preflight["ok"]
    treasury_blocked = not treasury.get("above_reserve", False)

    if not runtime_ok:
        verdict = "ENGINEERING_BLOCKED_RUNTIME"
    elif treasury_blocked:
        verdict = "OWNER_BLOCKED_TREASURY"
    elif not brief["exists"]:
        verdict = "READY_TO_RUN_CYCLE"
    else:
        verdict = "READY_FOR_CONTROLLED_DELIVERY_CHECK"

    return {
        "checked_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict,
        "runtime": {
            "health_ok": runtime_health["ok"],
            "pong_ok": pong["ok"] and pong["stdout"] == "PONG",
            "pong_output": pong["stdout"],
            "health_stderr": runtime_health["stderr"],
            "pong_stderr": pong["stderr"],
        },
        "treasury": {
            "balance_usd": round(treasury["balance_usd"], 2),
            "reserve_floor_usd": round(treasury["reserve_floor_usd"], 2),
            "runway_usd": round(treasury["runway_usd"], 2),
            "blocked": treasury_blocked,
            "shortfall_usd": round(treasury["shortfall_usd"], 2),
        },
        "preflight": {
            "ok": preflight_ok,
            "returncode": preflight["returncode"],
            "summary": preflight["stdout"].splitlines()[-1] if preflight["stdout"] else "",
        },
        "brief": brief,
        "delivery_targets": targets,
    }


def print_report(report):
    print(f"Meridian readiness — {report['checked_at']}")
    print(f"Verdict: {report['verdict']}")
    print("")
    print(
        "Runtime: "
        + ("OK" if report["runtime"]["health_ok"] and report["runtime"]["pong_ok"] else "BLOCKED")
    )
    print(
        "Treasury: "
        + (
            f"BLOCKED runway ${report['treasury']['runway_usd']:.2f}"
            if report["treasury"]["blocked"]
            else f"OK runway ${report['treasury']['runway_usd']:.2f}"
        )
    )
    print(
        "Preflight: "
        + ("OK" if report["preflight"]["ok"] else f"BLOCKED ({report['preflight']['summary']})")
    )
    if report["brief"]["exists"]:
        print(f"Latest brief: {report['brief']['date']} ({report['brief']['path']})")
    else:
        print("Latest brief: none")
    print(f"Deliverable targets today: {report['delivery_targets']['count']}")

    if report["verdict"] == "ENGINEERING_BLOCKED_RUNTIME":
        print("Next action: stabilize runtime before attempting pipeline execution.")
    elif report["verdict"] == "OWNER_BLOCKED_TREASURY":
        print(
            "Next action: owner must recapitalize treasury or lower reserve floor before any budget-gated phase can run."
        )
    elif report["verdict"] == "READY_TO_RUN_CYCLE":
        print("Next action: trigger one controlled pipeline cycle to generate a fresh brief.")
    else:
        print("Next action: run dry-run delivery checks, then one controlled delivery cycle.")


def main():
    parser = argparse.ArgumentParser(description="Operational readiness check for Meridian")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    report = collect()
    if args.json:
        print(json.dumps(report, indent=2))
        return
    print_report(report)


if __name__ == "__main__":
    main()
