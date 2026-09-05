from __future__ import annotations

import json
from typing import Any


def output(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _query_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "resourceId": item["resourceId"],
        "title": item["title"],
        "url": item["canonicalUrl"],
        "brief": item["brief"],
        "whyKept": item["whyKept"],
        "nextAction": item["nextAction"],
        "collections": [value["name"] for value in item["collections"]],
        "tabInstances": len(item["tabs"]),
    }


def _dedupe_output(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": plan["policy"],
        "planHash": plan["planHash"],
        "summary": plan["summary"],
        "browsers": {
            browser: {
                "beforeCaptureId": browser_plan["beforeCaptureId"],
                "plannedClosures": len(browser_plan["targets"]),
                "targetsHash": browser_plan["targetsHash"],
            }
            for browser, browser_plan in plan["browsers"].items()
        },
    }


def _archive_output(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": plan["policy"],
        "planHash": plan["planHash"],
        "summary": plan["summary"],
        "browsers": {
            browser: {
                "beforeCaptureId": browser_plan["beforeCaptureId"],
                "plannedClosures": len(browser_plan["targets"]),
                "targetsHash": browser_plan["targetsHash"],
            }
            for browser, browser_plan in plan["browsers"].items()
        },
    }
