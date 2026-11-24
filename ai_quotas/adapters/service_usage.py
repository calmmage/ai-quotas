"""Read-only, native-unit observations for the Service usage dashboard."""
import json
import math
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def number(value):
    if isinstance(value, bool):
        raise ValueError("invalid amount")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("invalid amount")
    return value


def get_json(url, headers):
    request = Request(url, headers={**headers, "User-Agent": "ai-quotas/usage", "Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def bearer(name):
    return {"Authorization": "Bearer " + os.environ[name]}


def higgs(*args):
    result = subprocess.run(["higgsfield", "--json", *args], capture_output=True, timeout=15, check=True)
    return json.loads(result.stdout)


def higgsfield(metrics, daily, start, end):
    account = higgs("account", "status")
    metrics["Credits available"] = number(account["credits"])
    cursor = None
    seen = set()
    while True:
        args = ["--cursor", cursor] if cursor else []
        page = higgs("account", "transactions", "--size", "100", *args)
        items = page["items"]
        for item in items:
            timestamp = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            if start <= timestamp < end and item["action"] == "spend":
                daily[timestamp.astimezone(timezone.utc).date().isoformat()] += abs(number(item["credits"]))
        cursor = page.get("cursor")
        if not items or not cursor or timestamp < start:
            break
        if cursor in seen or len(seen) >= 50:
            raise ValueError("incomplete transaction history")
        seen.add(cursor)


def meshy(metrics, daily, start, end):
    headers = bearer("MESHY_API_KEY")
    root = "https://api.meshy.ai/openapi/v1"
    balance = get_json(root + "/balance", headers)
    metrics["Credits available"] = number(balance["balance"])
    for key, label in (("monthly_credits", "Monthly credits left"), ("permanent_credits", "Permanent credits")):
        if balance.get(key) is not None:
            metrics[label] = number(balance[key])
    seen = set()
    for page in range(1, 256):
        query = urlencode(dict(page_size=100, page_num=page, start_time=start.isoformat(), end_time=end.isoformat()))
        tasks = get_json(root + "/usage/tasks?" + query, headers)
        if not isinstance(tasks, list):
            raise ValueError("invalid task list")
        for task in tasks:
            if task["task_id"] in seen:
                continue
            seen.add(task["task_id"])
            day = datetime.fromtimestamp(number(task["created_at"]) / 1000, timezone.utc).date()
            daily[day.isoformat()] += number(task["consumed_credits"])
        if len(tasks) < 100:
            return
    raise ValueError("incomplete task history")


def deepseek(metrics, *_):
    result = get_json("https://api.deepseek.com/user/balance", bearer("DEEPSEEK_API_KEY"))
    for entry in result["balance_infos"]:
        if entry["currency"] in {"CNY", "USD"}:
            metrics[entry["currency"] + " available"] = number(entry["total_balance"])


def elevenlabs(metrics, *_):
    result = get_json("https://api.elevenlabs.io/v1/user/subscription", {"xi-api-key": os.environ["CALMMAGE_ELEVENLABS_API_KEY"]})
    metrics["Characters used this cycle"] = number(result["character_count"])
    metrics["Character allowance"] = number(result["character_limit"])


READERS = {"meshy": meshy, "higgsfield": higgsfield, "deepseek": deepseek, "elevenlabs": elevenlabs}


def observe(ts):
    end = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    start = (end - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for provider, read in READERS.items():
        metrics, daily = {}, defaultdict(float)
        row = dict(ts=ts, provider=provider, window="service_usage", used_percent=None,
                   status="ok", source="provider", metrics=metrics)
        try:
            read(metrics, daily, start, end)
            if not metrics:
                raise ValueError("no billing metrics")
            if provider in {"higgsfield", "meshy"}:
                row["daily"] = {(start + timedelta(days=i)).date().isoformat(): round(daily[(start + timedelta(days=i)).date().isoformat()], 4) for i in range(7)}
        except HTTPError as exc:
            if provider == "meshy" and metrics and exc.code == 403:
                row["reason"] = "Task spending requires a Studio or Enterprise team key (HTTP 403)."
            else:
                row["reason"] = f"Provider returned HTTP {exc.code}"
        except KeyError as exc:
            row["reason"] = "Credential unavailable" if str(exc.args[0]).endswith("_KEY") else "Provider response incomplete"
        except (OSError, ValueError, subprocess.SubprocessError):
            row["reason"] = "Usage reading unavailable"
        if row.get("reason"):
            row["status"] = "partial" if metrics else "unavailable"
        rows.append(row)
    return rows


def snapshot(ts):
    # Only the existing sampler receives these credentials. Browsers read its snapshots.
    launcher = shutil.which("calm-keys")
    project = Path(__file__).resolve().parents[2]
    if launcher and (project / "calm-keys.toml").is_file() and not os.getenv("MESHY_API_KEY"):
        try:
            proc = subprocess.run([launcher, "run", "--scope", "auto", "--", sys.executable,
                               "-m", __name__, ts], cwd=project, capture_output=True, timeout=100)
            if proc.returncode == 0:
                return json.loads(proc.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return observe(ts)


if __name__ == "__main__":
    print(json.dumps(observe(sys.argv[1])))
