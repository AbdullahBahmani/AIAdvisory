"""Local load and distributed rate-limit harness.

Two planner items are addressed here:

* **P5-05 — "distributed rate limiting ... pending".** The rate limiter stores its counter in
  the application database with an atomic upsert, so in principle it is shared by every API
  replica that points at the same database. In principle is not evidence. This harness starts
  N real API processes against ONE shared database and proves that the per-principal limit is
  enforced **globally** — the total number of admitted requests must be capped at the
  configured capacity, not at capacity-per-instance. If the limit were per-process, the
  admitted count would scale with the number of replicas, and that is exactly the failure
  mode this check exists to catch.

* **P6-07 — "load ... unverified".** The same harness measures latency percentiles and
  throughput under concurrency.

Everything here is LOCAL and disposable: synthetic data only, a throwaway database, throwaway
credentials, and processes that are always torn down.

Usage:
    python -m scripts.load_check --database-url postgresql+psycopg://... --instances 3
    python -m scripts.load_check --database-url sqlite:///./load.sqlite3 --instances 1

Exit code is non-zero if the distributed rate limit is not enforced globally, or if any
request failed for a reason other than the expected 429.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = "/api/v1/assessments"


def _provision_credential(database_url: str, organization: str, subject: str) -> str:
    """Create a throwaway organization and API credential; return the raw token once."""
    script = (
        "from scripts.admin import main; import sys; sys.argv = "
        f"['admin', 'issue', '--organization', {organization!r}, '--subject', {subject!r}, "
        "'--role', 'analyst']; main()"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"credential provisioning failed:\n{completed.stdout}\n{completed.stderr}"
        )
    for line in completed.stdout.splitlines():
        if "Token (save now" in line:
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"could not read the issued token from:\n{completed.stdout}")


def _start_instance(port: int, database_url: str, capacity: int, log: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "ADVISORY_DEPLOYMENT_MODE": "local",
        "ADVISORY_DEMO_MODE": "0",  # demo mode bypasses the limiter entirely
        "ADVISORY_REQUESTS_PER_MINUTE": str(capacity),
        "PYTHONPATH": str(REPO_ROOT),
    }
    handle = log.open("wb")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )


def _wait_healthy(port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def _request(port: int, path: str, token: str) -> tuple[int, float]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (urllib.error.URLError, OSError) as error:
        status = 0
        print(f"request error: {error}", file=sys.stderr)
    return status, (time.perf_counter() - started) * 1000


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def evaluate_report(report: dict) -> list[str]:
    """Turn a load report into a list of failures. Empty list means the check passed.

    The central assertion is that the per-principal rate limit is enforced **globally** across
    every instance sharing the database: the number of admitted requests must not exceed the
    configured capacity. A per-process limiter would admit up to `capacity * instances`.
    """
    failures: list[str] = []
    capacity = report["configured_capacity"]
    instances = report["instances"]
    admitted = report["admitted_2xx"]

    if report["connection_errors"]:
        failures.append(f"{report['connection_errors']} requests failed to connect")
    if report["other_statuses"]:
        failures.append(f"unexpected status codes: {report['other_statuses']}")
    if admitted > capacity:
        failures.append(
            f"the per-principal limit was NOT enforced globally: {admitted} requests were "
            f"admitted against a capacity of {capacity} across {instances} instances "
            f"(a per-process limiter would admit up to {capacity * instances})"
        )
    if report["rate_limited_429"] == 0 and report["total_requests"] > capacity:
        failures.append("no request was rate limited although the request count exceeded capacity")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Local load and distributed rate-limit harness")
    parser.add_argument("--database-url", help="shared database URL for all API instances")
    parser.add_argument("--instances", type=int, default=2, help="number of API replicas")
    parser.add_argument(
        "--capacity", type=int, default=20, help="requests per minute per principal"
    )
    parser.add_argument("--requests", type=int, default=60, help="total concurrent requests")
    parser.add_argument("--path", default=DEFAULT_PATH, help="GET path to exercise")
    parser.add_argument("--base-port", type=int, default=8711)
    args = parser.parse_args()

    if not 1 <= args.instances <= 8:
        parser.error("--instances must be 1..8")
    if not 1 <= args.capacity <= 10000:
        parser.error("--capacity must be 1..10000")

    temporary: tempfile.TemporaryDirectory | None = None
    database_url = args.database_url
    if not database_url:
        temporary = tempfile.TemporaryDirectory(prefix="aiadvisory-load-")
        database_url = f"sqlite:///{Path(temporary.name) / 'load.sqlite3'}"

    processes: list[subprocess.Popen] = []
    log_dir = Path(tempfile.mkdtemp(prefix="aiadvisory-load-logs-"))
    try:
        # Migrate before starting anything, so every replica sees the same schema.
        migrate = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env={**os.environ, "DATABASE_URL": database_url},
            capture_output=True,
            text=True,
            timeout=600,
        )
        if migrate.returncode != 0:
            print(f"migration failed:\n{migrate.stderr}", file=sys.stderr)
            return 1

        token = _provision_credential(database_url, "LoadCheck Org", "load-check-subject")

        ports = list(range(args.base_port, args.base_port + args.instances))
        for port in ports:
            processes.append(
                _start_instance(port, database_url, args.capacity, log_dir / f"instance-{port}.log")
            )
        for port in ports:
            if not _wait_healthy(port):
                print(f"instance on port {port} never became healthy", file=sys.stderr)
                log_file = log_dir / f"instance-{port}.log"
                if log_file.exists():
                    print(f"--- {log_file.name} ---", file=sys.stderr)
                    print(log_file.read_text(errors="replace")[-4000:], file=sys.stderr)
                return 1

        print("=" * 74)
        print("Load and distributed rate-limit check")
        print(f"  instances          : {args.instances} (sharing one database)")
        print(f"  per-principal limit: {args.capacity} requests/minute")
        print(f"  total requests     : {args.requests}")
        print("=" * 74)

        started = time.perf_counter()
        latencies: list[float] = []
        statuses: dict[int, int] = {}
        with ThreadPoolExecutor(max_workers=max(8, args.requests // 2)) as pool:
            futures = [
                pool.submit(_request, ports[index % len(ports)], args.path, token)
                for index in range(args.requests)
            ]
            for future in futures:
                status, latency = future.result()
                statuses[status] = statuses.get(status, 0) + 1
                latencies.append(latency)
        elapsed = time.perf_counter() - started

        admitted = sum(count for status, count in statuses.items() if 200 <= status < 300)
        limited = statuses.get(429, 0)
        other = {
            status: count
            for status, count in statuses.items()
            if status != 0 and not (200 <= status < 300) and status != 429
        }

        report = {
            "instances": args.instances,
            "configured_capacity": args.capacity,
            "total_requests": args.requests,
            "admitted_2xx": admitted,
            "rate_limited_429": limited,
            "other_statuses": other,
            "connection_errors": statuses.get(0, 0),
            "elapsed_seconds": round(elapsed, 3),
            "throughput_rps": round(args.requests / elapsed, 2) if elapsed else 0.0,
            "latency_ms": {
                "min": round(min(latencies), 2) if latencies else 0.0,
                "p50": round(_percentile(latencies, 0.50), 2),
                "p95": round(_percentile(latencies, 0.95), 2),
                "p99": round(_percentile(latencies, 0.99), 2),
                "max": round(max(latencies), 2) if latencies else 0.0,
                "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            },
        }
        print(json.dumps(report, indent=2))

        failures = evaluate_report(report)

        print("-" * 74)
        if failures:
            for failure in failures:
                print(f"FAIL  {failure}")
            return 1
        print(
            f"PASS  the limit is enforced across {args.instances} instance(s): "
            f"{admitted} admitted (<= {args.capacity}), {limited} rate limited"
        )
        print(
            "NOTE  This is a local, synthetic check with a throwaway database and throwaway "
            "credentials.\n      It is NOT a production capacity test, and it says nothing "
            "about ingress/WAF limits."
        )
        return 0
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
