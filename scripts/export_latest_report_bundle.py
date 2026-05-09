#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export the latest daily analysis batch into a compact JSON/Markdown bundle.

This bundle is designed for a lightweight Cloudflare Worker chat layer:
- GitHub Actions runs daily_stock_analysis
- this script extracts the newest batch from SQLite
- Actions POST the JSON to Cloudflare
- the Worker answers follow-up questions based on the stored report
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.history_service import HistoryService
from src.storage import DatabaseManager


def _safe_load_json(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _truncate(text: Optional[str], limit: int = 220) -> str:
    if not text:
        return ""
    value = " ".join(str(text).split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _as_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "N/A"):
            return None
        return float(value)
    except Exception:
        return None


def _market_snapshot(raw_result: Dict[str, Any]) -> Dict[str, Any]:
    quote = raw_result.get("realtime_quote") or {}
    return {
        "price": _as_float(quote.get("price")),
        "change_pct": _as_float(quote.get("change_pct")),
        "turnover_rate": _as_float(quote.get("turnover_rate")),
        "volume_ratio": _as_float(quote.get("volume_ratio")),
        "pe_ratio": _as_float(quote.get("pe_ratio")),
        "pb_ratio": _as_float(quote.get("pb_ratio")),
    }


def _stock_entry(record: Any, history_service: HistoryService) -> Dict[str, Any]:
    raw_result = _safe_load_json(record.raw_result)
    dashboard = raw_result.get("dashboard") or {}
    core = dashboard.get("core_conclusion") or {}
    battle = dashboard.get("battle_plan") or {}
    intel = dashboard.get("intelligence") or {}

    full_markdown = ""
    try:
        full_markdown = history_service.get_markdown_report(record.id) or ""
    except Exception:
        full_markdown = ""

    return {
        "id": record.id,
        "query_id": record.query_id,
        "stock_code": record.code,
        "stock_name": record.name,
        "report_type": record.report_type,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "sentiment_score": record.sentiment_score,
        "operation_advice": record.operation_advice,
        "trend_prediction": record.trend_prediction,
        "analysis_summary": _truncate(record.analysis_summary, 320),
        "one_sentence": _truncate(core.get("one_sentence") or record.analysis_summary, 120),
        "time_sensitivity": core.get("time_sensitivity"),
        "has_position_advice": _truncate((core.get("position_advice") or {}).get("has_position"), 120),
        "no_position_advice": _truncate((core.get("position_advice") or {}).get("no_position"), 120),
        "ideal_buy": battle.get("entry_zone") or record.ideal_buy,
        "secondary_buy": battle.get("fallback_entry") or record.secondary_buy,
        "stop_loss": battle.get("stop_loss") or record.stop_loss,
        "take_profit": battle.get("take_profit") or record.take_profit,
        "latest_news": _truncate(intel.get("latest_news"), 180),
        "positive_catalysts": [_truncate(x, 90) for x in (intel.get("positive_catalysts") or [])[:3]],
        "risk_alerts": [_truncate(x, 90) for x in (intel.get("risk_alerts") or [])[:3]],
        "market_snapshot": _market_snapshot(raw_result),
        "full_markdown": full_markdown,
    }


def _decision_counts(stocks: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"buy": 0, "hold": 0, "sell": 0, "other": 0}
    for stock in stocks:
        advice = (stock.get("operation_advice") or "").strip()
        if advice == "买入":
            counts["buy"] += 1
        elif advice in {"观望", "持有", ""}:
            counts["hold"] += 1
        elif advice == "卖出":
            counts["sell"] += 1
        else:
            counts["other"] += 1
    return counts


def _elder_summary(stocks: List[Dict[str, Any]]) -> str:
    if not stocks:
        return "今天还没有可用日报。"

    top = sorted(stocks, key=lambda item: item.get("sentiment_score") or 0, reverse=True)
    counts = _decision_counts(stocks)
    head = f"今天共分析 {len(stocks)} 只股票，买入 {counts['buy']} 只，观望/持有 {counts['hold']} 只，卖出 {counts['sell']} 只。"
    focus = []
    for stock in top[:3]:
        focus.append(
            f"{stock['stock_name']}({stock['stock_code']}) 建议{stock.get('operation_advice') or '观望'}，核心原因是{stock.get('one_sentence') or stock.get('analysis_summary') or '暂无摘要'}"
        )
    return head + " " + "；".join(focus)


def _markdown_bundle(bundle: Dict[str, Any]) -> str:
    lines = [
        f"# {bundle['report_date']} 决策简报",
        "",
        f"> 批次: `{bundle['query_id']}` | 股票数: {bundle['stock_count']}",
        "",
        bundle["elder_friendly_summary"],
        "",
    ]
    for stock in bundle["stocks"]:
        lines.extend(
            [
                f"## {stock['stock_name']} ({stock['stock_code']})",
                "",
                f"- 建议: {stock.get('operation_advice') or '观望'}",
                f"- 趋势: {stock.get('trend_prediction') or '待观察'}",
                f"- 分数: {stock.get('sentiment_score') or 0}",
                f"- 一句话: {stock.get('one_sentence') or stock.get('analysis_summary') or '暂无摘要'}",
            ]
        )
        if stock.get("latest_news"):
            lines.append(f"- 最新消息: {stock['latest_news']}")
        if stock.get("risk_alerts"):
            lines.append(f"- 风险: {'；'.join(stock['risk_alerts'])}")
        if stock.get("positive_catalysts"):
            lines.append(f"- 利好: {'；'.join(stock['positive_catalysts'])}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_latest_bundle(days: int = 7, limit: int = 200) -> Dict[str, Any]:
    db = DatabaseManager()
    history_service = HistoryService(db)
    recent = db.get_analysis_history(days=days, limit=limit)
    if not recent:
        raise RuntimeError("No analysis history found in the configured database.")

    latest = recent[0]
    batch_query_id = latest.query_id
    batch_rows = db.get_analysis_history(query_id=batch_query_id, limit=limit)
    batch_rows = sorted(batch_rows, key=lambda row: (row.sentiment_score or 0), reverse=True)
    stocks = [_stock_entry(row, history_service) for row in batch_rows]
    counts = _decision_counts(stocks)

    bundle: Dict[str, Any] = {
        "version": 1,
        "generated_at": datetime.now().isoformat(),
        "query_id": batch_query_id,
        "report_date": latest.created_at.strftime("%Y-%m-%d") if latest.created_at else datetime.now().strftime("%Y-%m-%d"),
        "report_language": "zh",
        "report_type": latest.report_type or "brief",
        "stock_count": len(stocks),
        "decision_counts": counts,
        "elder_friendly_summary": _elder_summary(stocks),
        "stocks": stocks,
    }
    bundle["brief_markdown"] = _markdown_bundle(bundle)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Export latest DSA batch for Cloudflare Worker chat.")
    parser.add_argument("--out-json", default="reports/latest_report_bundle.json")
    parser.add_argument("--out-md", default="reports/latest_report_bundle.md")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    bundle = build_latest_bundle(days=args.days, limit=args.limit)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    out_json.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(bundle["brief_markdown"], encoding="utf-8")

    print(f"Exported latest report bundle to {out_json}")
    print(f"Exported latest markdown bundle to {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
