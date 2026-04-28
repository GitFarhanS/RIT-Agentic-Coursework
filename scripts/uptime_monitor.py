#!/usr/bin/env python3
"""
Poll an HTTP endpoint and record uptime metrics to JSON.

Rotman/RIT defaults are supported out of the box:
  python scripts/uptime_monitor.py

Custom endpoint example:
  python scripts/uptime_monitor.py \
    --url "http://192.168.64.9:9999/v1/case" \
    --interval 30 \
    --output data/uptime_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_headers(
    header_name: str,
    header_value: str | None,
    header_prefix: str,
    include_json_accept: bool,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if include_json_accept:
        headers["Accept"] = "application/json"
    if header_value:
        headers[header_name] = f"{header_prefix}{header_value}"
    return headers


def single_check(url: str, timeout: float, headers: dict[str, str]) -> tuple[bool, int | None, str, str | None, int | None]:
    req = urllib.request.Request(url=url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            body_bytes = resp.read()
            case_status: str | None = None
            tick: int | None = None
            if body_bytes:
                try:
                    payload = json.loads(body_bytes.decode("utf-8"))
                    if isinstance(payload, dict):
                        raw_status = payload.get("status")
                        if isinstance(raw_status, str) and raw_status.strip():
                            case_status = raw_status.strip().upper()
                        raw_tick = payload.get("tick")
                        if raw_tick is not None:
                            tick = int(raw_tick)
                except Exception:
                    # Non-JSON body is fine; we still record availability.
                    pass
            if 200 <= code < 400:
                return True, code, "ok", case_status, tick
            return False, code, f"http_{code}", case_status, tick
    except urllib.error.HTTPError as exc:
        return False, exc.code, f"http_error_{exc.code}", None, None
    except urllib.error.URLError as exc:
        return False, None, f"url_error_{exc.reason}", None, None
    except TimeoutError:
        return False, None, "timeout", None, None
    except Exception as exc:  # defensive catch for uninterrupted polling
        return False, None, f"exception_{type(exc).__name__}: {exc}", None, None


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_report(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    default_rotman_host = os.environ.get("ROTMAN_HOST", "http://192.168.64.9:9999/v1").rstrip("/")
    default_url = f"{default_rotman_host}/case"
    parser = argparse.ArgumentParser(description="Poll an endpoint and record uptime in JSON.")
    parser.add_argument(
        "--url",
        default=default_url,
        help=f"Endpoint URL to poll (default: {default_url}).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="Seconds between checks (default: 30).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="HTTP request timeout in seconds (default: 8).",
    )
    parser.add_argument(
        "--output",
        default="data/uptime_report.json",
        help="Path to JSON report output (default: data/uptime_report.json).",
    )
    parser.add_argument(
        "--max-checks",
        type=int,
        default=0,
        help="Stop after N checks (0 means run forever).",
    )
    parser.add_argument(
        "--api-key-env",
        default="ROTMAN_API_KEY",
        help="Env var name containing API key/token (default: ROTMAN_API_KEY).",
    )
    parser.add_argument(
        "--header-name",
        default="X-API-KEY",
        help="Header name used for API auth (default: X-API-KEY).",
    )
    parser.add_argument(
        "--header-prefix",
        default="",
        help='Prefix prepended to API key (default: "").',
    )
    parser.add_argument(
        "--accept-json",
        action="store_true",
        help="Send Accept: application/json header.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    api_key = os.environ.get(args.api_key_env)
    headers = make_headers(args.header_name, api_key, args.header_prefix, args.accept_json)

    stop = {"value": False}

    def _handle_signal(_sig: int, _frame: Any) -> None:
        stop["value"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    existing = load_existing(output_path)
    checks: list[dict[str, Any]] = existing.get("checks", [])
    started_at = existing.get("started_at", utc_now_iso())
    total_checks = int(existing.get("total_checks", len(checks)))
    up_checks = int(existing.get("up_checks", sum(1 for c in checks if c.get("up"))))
    down_checks = int(existing.get("down_checks", sum(1 for c in checks if not c.get("up"))))
    known_statuses = ("ACTIVE", "PAUSED", "STOPPED")
    status_counts: dict[str, int] = existing.get("status_counts") or {
        k: sum(1 for c in checks if c.get("case_status") == k) for k in known_statuses
    }
    for key in known_statuses:
        status_counts.setdefault(key, 0)
    status_counts.setdefault("UNKNOWN", sum(1 for c in checks if not c.get("case_status")))

    run_count = 0
    while not stop["value"]:
        run_count += 1
        ok, status_code, detail, case_status, tick = single_check(args.url, args.timeout, headers)
        status_bucket = case_status if case_status in known_statuses else "UNKNOWN"
        event = {
            "time": utc_now_iso(),
            "up": ok,
            "status_code": status_code,
            "detail": detail,
            "case_status": case_status,
            "tick": tick,
        }
        checks.append(event)
        total_checks += 1
        if ok:
            up_checks += 1
        else:
            down_checks += 1
        status_counts[status_bucket] = int(status_counts.get(status_bucket, 0)) + 1

        uptime_percent = (up_checks / total_checks * 100.0) if total_checks else 0.0
        status_percentages = {
            k: round((v / total_checks * 100.0), 4) if total_checks else 0.0 for k, v in status_counts.items()
        }
        payload = {
            "url": args.url,
            "started_at": started_at,
            "updated_at": utc_now_iso(),
            "total_checks": total_checks,
            "up_checks": up_checks,
            "down_checks": down_checks,
            "uptime_percent": round(uptime_percent, 4),
            "status_counts": status_counts,
            "status_percentages": status_percentages,
            "last_check": event,
            "checks": checks,
        }
        write_report(output_path, payload)

        print(
            f"[{event['time']}] up={ok} http={status_code} detail={detail} "
            f"case_status={status_bucket} tick={tick} "
            f"uptime={payload['uptime_percent']:.4f}% checks={total_checks}",
            flush=True,
        )

        if args.max_checks > 0 and run_count >= args.max_checks:
            break
        time.sleep(max(0.0, args.interval))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
