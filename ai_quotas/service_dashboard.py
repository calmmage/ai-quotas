"""Project existing sampler observations into native-unit service cards."""
from datetime import datetime, timedelta, timezone


def summarize_usage(rows):
    latest, balances = {}, {}
    since = (datetime.now(timezone.utc).date() - timedelta(days=29)).isoformat()
    for row in rows:
        service, window = row.get("provider"), row.get("window")
        if window == "service_usage":
            fields = {k: row[k] for k in ("status", "metrics", "daily", "reason") if k in row}
        elif service == "openrouter" and window == "credits" and row.get("used") is not None:
            fields = {"metrics": {"USD used by key": row["used"]}, "status": "ok"}
        elif service == "openrouter" and window == "unknown":
            fields = {"metrics": {}, "status": "unavailable", "reason": "API key usage unavailable"}
        else:
            continue
        stamp = datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        if stamp >= latest.get(service, {}).get("ts", ""):
            latest[service] = dict(fields, ts=stamp)
        balance = fields.get("metrics", {}).get("Credits available")
        day = stamp[:10]
        if balance is not None and day >= since:
            points = balances.setdefault(service, {})
            if day not in points or points[day][0] < stamp:
                points[day] = (stamp, balance)
    for service, days in balances.items():
        latest[service]["balance_history"] = {day: pair[1] for day, pair in sorted(days.items())}
    return latest
