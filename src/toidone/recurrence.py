import calendar
from copy import deepcopy
from datetime import date, timedelta


def next_day(day: str, rule: dict, today: date) -> str:
    anchor = date.fromisoformat(rule.get("anchorDay") or day)
    threshold = max(date.fromisoformat(day), today)
    interval = rule["interval"]
    unit = rule["unit"]
    for count in range(1, 100001):
        step = count * interval
        if unit in {"day", "week"}:
            candidate = anchor + timedelta(days=step * (7 if unit == "week" else 1))
        else:
            months = step * (12 if unit == "year" else 1)
            year, month = divmod(anchor.year * 12 + anchor.month - 1 + months, 12)
            month += 1
            candidate = date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))
        if candidate > threshold:
            return candidate.isoformat()
    raise ValueError("recurrence range exceeded")


def next_occurrence(body: dict, today: date) -> dict | None:
    separate = body["usesSeparateRepeats"]
    scheduled = body["scheduledRepeat"] if separate else body["sharedRepeat"]
    deadline = body["deadlineRepeat"] if separate else body["sharedRepeat"]
    if not ((body["scheduledDay"] and scheduled) or (body["deadlineDay"] and deadline)):
        return None
    result = deepcopy(body)
    result["isCompleted"] = False
    result["sortOrder"] = min(body["sortOrder"] + 1, 2**53 - 1)
    for item in result["checklist"]:
        item["isCompleted"] = False
    if not separate:
        base = body["scheduledDay"] or body["deadlineDay"]
        advanced = next_day(base, body["sharedRepeat"], today)
        offset = date.fromisoformat(advanced) - date.fromisoformat(base)
        if body["scheduledDay"]:
            result["scheduledDay"] = advanced
        if body["deadlineDay"]:
            result["deadlineDay"] = (date.fromisoformat(body["deadlineDay"]) + offset).isoformat()
        result["sharedRepeat"]["anchorDay"] = body["sharedRepeat"].get("anchorDay") or base
    else:
        for field, rule in (("scheduledDay", scheduled), ("deadlineDay", deadline)):
            if rule is None:
                result[field] = None
                if field == "deadlineDay":
                    result["deadlineTime"] = result["deadlineTimeZone"] = None
            elif body[field]:
                result[field] = next_day(body[field], rule, today)
                name = "scheduledRepeat" if field == "scheduledDay" else "deadlineRepeat"
                result[name]["anchorDay"] = rule.get("anchorDay") or body[field]
    return result
