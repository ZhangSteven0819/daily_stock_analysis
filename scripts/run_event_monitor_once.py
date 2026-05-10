#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run one lightweight EventMonitor cycle with persistent rule state.

Designed for GitHub Actions cron jobs or VPS cron jobs that should check
intraday stock events every 15 minutes without running the full AI analysis
pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.events import (
    AlertRule,
    AlertStatus,
    EventMonitor,
    PriceAlert,
    PriceChangeAlert,
    TriggeredAlert,
    VolumeAlert,
    parse_event_alert_rules,
    run_event_monitor_once,
    validate_event_alert_rule,
)
from src.config import get_config, setup_env
from src.core.trading_calendar import is_market_open
from src.notification import NotificationService


CN_TZ = ZoneInfo("Asia/Shanghai")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one event-monitor cycle and persist alert state."
    )
    parser.add_argument(
        "--state-file",
        default="data/event_monitor_state.json",
        help="Path to persisted monitor rule state.",
    )
    parser.add_argument(
        "--skip-market-hours-check",
        action="store_true",
        help="Run even if it is outside A-share trading hours.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not send notifications; only print what would be sent.",
    )
    return parser.parse_args()


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_cn_market_session(now: Optional[datetime] = None) -> bool:
    current = now.astimezone(CN_TZ) if now and now.tzinfo else now or datetime.now(CN_TZ)
    if not is_market_open("cn", current.date()):
        return False

    current_time = current.time()
    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)
    return (
        morning_start <= current_time <= morning_end
        or afternoon_start <= current_time <= afternoon_end
    )


def rule_identity(rule: Dict[str, Any]) -> Tuple[Any, ...]:
    alert_type = str(rule.get("alert_type", "")).strip()
    stock_code = str(rule.get("stock_code", "")).strip()
    if alert_type == "price_cross":
        return (
            stock_code,
            alert_type,
            str(rule.get("direction", "above")).lower(),
            float(rule.get("price", 0.0)),
        )
    if alert_type == "price_change_percent":
        return (
            stock_code,
            alert_type,
            str(rule.get("direction", "up")).lower(),
            float(rule.get("change_pct", 0.0)),
        )
    if alert_type == "volume_spike":
        return (
            stock_code,
            alert_type,
            float(rule.get("multiplier", 2.0)),
        )
    return (stock_code, alert_type)


def restore_stateful_rules(
    configured_rules: Iterable[Dict[str, Any]],
    saved_rules: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    saved_by_key = {}
    for entry in saved_rules:
        try:
            validate_event_alert_rule(entry)
            saved_by_key[rule_identity(entry)] = entry
        except Exception:
            continue

    merged: List[Dict[str, Any]] = []
    for configured in configured_rules:
        validate_event_alert_rule(configured)
        key = rule_identity(configured)
        candidate = dict(configured)
        saved = saved_by_key.get(key)
        if saved:
            for field in ("status", "created_at", "triggered_at", "ttl_hours", "description"):
                if field in saved:
                    candidate[field] = saved[field]
        merged.append(candidate)
    return merged


def instantiate_rule(rule: Dict[str, Any]) -> AlertRule:
    alert_type = str(rule.get("alert_type", "")).strip()
    stock_code = str(rule.get("stock_code", "")).strip()
    common: Dict[str, Any] = {
        "stock_code": stock_code,
        "description": str(rule.get("description", "") or ""),
    }

    if alert_type == "price_cross":
        obj: AlertRule = PriceAlert(
            direction=str(rule.get("direction", "above")).lower(),
            price=float(rule.get("price", 0.0)),
            **common,
        )
    elif alert_type == "price_change_percent":
        obj = PriceChangeAlert(
            direction=str(rule.get("direction", "up")).lower(),
            change_pct=float(rule.get("change_pct", 0.0)),
            **common,
        )
    elif alert_type == "volume_spike":
        obj = VolumeAlert(
            multiplier=float(rule.get("multiplier", 2.0)),
            **common,
        )
    else:
        raise ValueError(f"unsupported alert_type: {alert_type}")

    raw_status = str(rule.get("status", AlertStatus.ACTIVE.value)).strip() or AlertStatus.ACTIVE.value
    obj.status = AlertStatus(raw_status)
    if rule.get("created_at") is not None:
        obj.created_at = float(rule["created_at"])
    if rule.get("triggered_at") is not None:
        obj.triggered_at = float(rule["triggered_at"])
    if rule.get("ttl_hours") is not None:
        obj.ttl_hours = float(rule["ttl_hours"])
    return obj


def monitor_to_state(monitor: EventMonitor) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for rule in monitor.rules:
        item = {
            "stock_code": rule.stock_code,
            "alert_type": rule.alert_type.value,
            "description": rule.description,
            "status": rule.status.value,
            "created_at": rule.created_at,
            "triggered_at": rule.triggered_at,
            "ttl_hours": rule.ttl_hours,
        }
        if isinstance(rule, PriceAlert):
            item["direction"] = rule.direction
            item["price"] = rule.price
        elif isinstance(rule, PriceChangeAlert):
            item["direction"] = rule.direction
            item["change_pct"] = rule.change_pct
        elif isinstance(rule, VolumeAlert):
            item["multiplier"] = rule.multiplier
        payload.append(item)
    return payload


def render_plain_text_alert(triggered: List[TriggeredAlert]) -> str:
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    lines = [
        "盘中监测提醒",
        f"时间：{now}",
        f"触发数量：{len(triggered)}",
    ]
    for index, item in enumerate(triggered, start=1):
        rule = item.rule
        if isinstance(rule, PriceAlert):
            detail = f"价格{rule.direction} {rule.price:g}，当前 {item.current_value}"
        elif isinstance(rule, PriceChangeAlert):
            detail = (
                f"涨跌幅{rule.direction} {rule.change_pct:.2f}%，"
                f"当前 {float(item.current_value):+.2f}%"
            )
        elif isinstance(rule, VolumeAlert):
            detail = f"成交量放大，当前值 {item.current_value}"
        else:
            detail = str(item.current_value or rule.description or "")
        lines.append(f"{index}. {rule.stock_code}：{detail}")
    lines.append("说明：该提醒来自预设盘中监控规则，请结合持仓计划与风险边界处理。")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    setup_env()

    if not args.skip_market_hours_check and not is_cn_market_session():
        print(
            json.dumps(
                {
                    "status": "skipped-outside-market-session",
                    "checked_at": datetime.now(CN_TZ).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    config = get_config()
    if not getattr(config, "agent_event_monitor_enabled", False):
        print(
            json.dumps(
                {"status": "skipped-disabled", "reason": "AGENT_EVENT_MONITOR_ENABLED=false"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    raw_rules = getattr(config, "agent_event_alert_rules_json", "")
    configured_rules = parse_event_alert_rules(raw_rules)
    state_path = Path(args.state_file).resolve()
    saved_state = load_json(state_path, [])
    merged_rules = restore_stateful_rules(configured_rules, saved_state)

    monitor = EventMonitor()
    for entry in merged_rules:
        monitor.add_alert(instantiate_rule(entry))

    triggered = run_event_monitor_once(monitor)
    save_json(state_path, monitor_to_state(monitor))

    result: Dict[str, Any] = {
        "status": "ok",
        "checked_at": datetime.now(CN_TZ).isoformat(),
        "state_file": str(state_path),
        "configured_rule_count": len(configured_rules),
        "active_rule_count": len(monitor.rules),
        "triggered_count": len(triggered),
    }

    if not triggered:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    message = render_plain_text_alert(triggered)
    result["message"] = message

    if args.dry_run:
        result["status"] = "triggered-dry-run"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    sent = NotificationService().send(message)
    result["sent"] = bool(sent)
    result["status"] = "triggered-sent" if sent else "triggered-no-channel"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
