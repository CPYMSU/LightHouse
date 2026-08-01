#!/usr/bin/env python3
"""Fetch annual China–Russia HS2 merchandise trade from UN Comtrade.

The script uses China (reporter 156) and Russia (partner 643), annual data,
HS aggregate level 2 (AG2), and both export (X) and import (M) flows.
It writes one research-ready UTF-8-SIG CSV and a JSON retrieval summary.
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

START_YEAR = 1995
END_YEAR = 2025
REPORTER_CODE = 156
PARTNER_CODE = 643
BASE_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
RETRIEVED_ON = date.today().isoformat()
OUT_DIR = Path(os.environ.get("OUTPUT_DIR", "data"))
CSV_PATH = OUT_DIR / "china_russia_trade_hs2_1995_2025.csv"
SUMMARY_PATH = OUT_DIR / "china_russia_trade_hs2_1995_2025_summary.json"


def first(record: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return default


def as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def format_number(value: Any) -> str:
    number = as_number(value)
    if number is None:
        return ""
    if number.is_integer():
        return str(int(number))
    return format(number, ".12g")


def sector_for_hs2(code: str) -> tuple[str, str]:
    if code == "TOTAL":
        return "Total merchandise trade", "货物贸易总计"
    try:
        chapter = int(code)
    except ValueError:
        return "Unclassified / special", "未分类或特殊代码"
    ranges = [
        (1, 5, "Animal products", "动物产品"),
        (6, 14, "Vegetable products", "植物产品"),
        (15, 15, "Animal or vegetable fats and oils", "动植物油脂"),
        (16, 24, "Prepared food, beverages and tobacco", "食品、饮料与烟草"),
        (25, 26, "Minerals and ores", "矿物与矿石"),
        (27, 27, "Energy and mineral fuels", "能源与矿物燃料"),
        (28, 38, "Chemicals", "化学品"),
        (39, 40, "Plastics and rubber", "塑料与橡胶"),
        (41, 43, "Hides, skins and leather", "皮革与毛皮"),
        (44, 49, "Wood, pulp and paper", "木材、纸浆与纸制品"),
        (50, 63, "Textiles and apparel", "纺织品与服装"),
        (64, 67, "Footwear, headgear and related articles", "鞋帽及相关制品"),
        (68, 70, "Stone, ceramics and glass", "石材、陶瓷与玻璃"),
        (71, 71, "Precious metals and stones", "贵金属与宝石"),
        (72, 83, "Base metals and metal products", "贱金属及其制品"),
        (84, 84, "Machinery and mechanical appliances", "机械与机械设备"),
        (85, 85, "Electrical and electronic equipment", "电气与电子设备"),
        (86, 89, "Transport equipment", "运输设备"),
        (90, 92, "Precision, optical and medical instruments", "精密、光学与医疗仪器"),
        (93, 93, "Arms and ammunition", "武器与弹药"),
        (94, 96, "Miscellaneous manufactured articles", "其他制成品"),
        (97, 97, "Works of art and antiques", "艺术品与古董"),
        (98, 99, "Special classifications", "特殊分类"),
    ]
    for start, end, english, chinese in ranges:
        if start <= chapter <= end:
            return english, chinese
    return "Unclassified / special", "未分类或特殊代码"


def shock_metadata(year: int) -> dict[str, Any]:
    events: list[str] = []
    if year == 2008:
        events.append("Global financial crisis")
    if year == 2014:
        events.append("2014 sanctions shock")
    if year == 2020:
        events.append("COVID-19 pandemic")
    if year == 2022:
        events.append("2022 geopolitical and financial sanctions escalation")

    if year <= 2007:
        phase = "1995-2007 pre-global-financial-crisis baseline"
    elif year <= 2013:
        phase = "2008-2013 post-GFC, pre-2014"
    elif year <= 2019:
        phase = "2014-2019 post-2014 sanctions adjustment"
    elif year <= 2021:
        phase = "2020-2021 pandemic period"
    else:
        phase = "2022-2025 post-2022 escalation"

    return {
        "shock_event": "; ".join(events),
        "shock_phase": phase,
        "shock_2008": int(year == 2008),
        "shock_2014": int(year == 2014),
        "shock_2020": int(year == 2020),
        "shock_2022": int(year == 2022),
    }


def build_url(year: int, flow_code: str) -> str:
    params = {
        "reportercode": REPORTER_CODE,
        "flowCode": flow_code,
        "period": year,
        "cmdCode": "AG2",
        "partnerCode": PARTNER_CODE,
        "partner2Code": 0,
        "motCode": 0,
        "customsCode": "C00",
        "maxRecords": 500,
        "format": "JSON",
        "breakdownMode": "classic",
        "includeDesc": "true",
    }
    return BASE_URL + "?" + urllib.parse.urlencode(params)


def fetch_json(year: int, flow_code: str, attempts: int = 6) -> tuple[dict[str, Any] | None, str]:
    url = build_url(year, flow_code)
    last_error = ""
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "China-Russia-Trade-Research/1.0 (+academic data retrieval)",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                error = payload.get("error")
                if error:
                    raise RuntimeError(str(error))
                return payload, ""
            raise RuntimeError("API response was not a JSON object")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                delay = min(60.0, (2 ** (attempt - 1)) + random.random())
                print(f"retry {attempt}/{attempts - 1} for {year} {flow_code}: {last_error}; sleep {delay:.1f}s", flush=True)
                time.sleep(delay)
    return None, last_error


def normalize_code(record: dict[str, Any]) -> str:
    raw = str(first(record, "cmdCode", "cmd_code", default="")).strip()
    if raw.upper() in {"TOTAL", "00"}:
        return "TOTAL"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 1:
        return digits.zfill(2)
    if len(digits) >= 2:
        return digits[:2]
    return raw


def normalized_row(record: dict[str, Any], year: int, flow_code: str) -> dict[str, Any]:
    hs2_code = normalize_code(record)
    sector_en, sector_zh = sector_for_hs2(hs2_code)
    direction = "China_to_Russia" if flow_code == "X" else "Russia_to_China"
    exporter = "China" if flow_code == "X" else "Russia"
    importer = "Russia" if flow_code == "X" else "China"
    value = first(record, "primaryValue", "primary_value", "TradeValue", "tradeValue")
    row: dict[str, Any] = {
        "record_type": "trade",
        "year": year,
        "reporter_code": first(record, "reporterCode", default=REPORTER_CODE),
        "reporter_iso3": first(record, "reporterISO", "reporterIso", default="CHN"),
        "reporter": first(record, "reporterDesc", default="China"),
        "partner_code": first(record, "partnerCode", default=PARTNER_CODE),
        "partner_iso3": first(record, "partnerISO", "partnerIso", default="RUS"),
        "partner": first(record, "partnerDesc", default="Russian Federation"),
        "flow_code": flow_code,
        "flow": first(record, "flowDesc", default=("Export" if flow_code == "X" else "Import")),
        "trade_direction": direction,
        "exporter": exporter,
        "importer": importer,
        "classification_code": first(record, "classificationCode", "classificationSearchCode", default="HS"),
        "hs2_code": hs2_code,
        "hs2_description": first(record, "cmdDesc", "cmd_desc"),
        "sector_group_en": sector_en,
        "sector_group_zh": sector_zh,
        "trade_value_usd": format_number(value),
        "cif_value_usd": format_number(first(record, "cifvalue", "cifValue")),
        "fob_value_usd": format_number(first(record, "fobvalue", "fobValue")),
        "net_weight_kg": format_number(first(record, "netWgt", "netWeight")),
        "gross_weight_kg": format_number(first(record, "grossWgt", "grossWeight")),
        "quantity": format_number(first(record, "qty", "quantity")),
        "quantity_unit": first(record, "qtyUnitAbbr", "qtyUnitCode"),
        "is_quantity_estimated": first(record, "isQtyEstimated"),
        "is_net_weight_estimated": first(record, "isNetWgtEstimated"),
        "is_reported": first(record, "isReported"),
        "is_aggregate": first(record, "isAggregate"),
        "data_status": "available",
        "data_source": "UN Comtrade public API; China-reported annual merchandise trade",
        "source_query_scope": "Reporter CHN (156); partner RUS (643); annual; HS AG2",
        "retrieved_on": RETRIEVED_ON,
    }
    row.update(shock_metadata(year))
    return row


def availability_row(year: int, flow_code: str, status: str, error: str = "") -> dict[str, Any]:
    direction = "China_to_Russia" if flow_code == "X" else "Russia_to_China"
    exporter = "China" if flow_code == "X" else "Russia"
    importer = "Russia" if flow_code == "X" else "China"
    row: dict[str, Any] = {
        "record_type": "availability",
        "year": year,
        "reporter_code": REPORTER_CODE,
        "reporter_iso3": "CHN",
        "reporter": "China",
        "partner_code": PARTNER_CODE,
        "partner_iso3": "RUS",
        "partner": "Russian Federation",
        "flow_code": flow_code,
        "flow": "Export" if flow_code == "X" else "Import",
        "trade_direction": direction,
        "exporter": exporter,
        "importer": importer,
        "classification_code": "HS",
        "hs2_code": "",
        "hs2_description": "",
        "sector_group_en": "",
        "sector_group_zh": "",
        "trade_value_usd": "",
        "cif_value_usd": "",
        "fob_value_usd": "",
        "net_weight_kg": "",
        "gross_weight_kg": "",
        "quantity": "",
        "quantity_unit": "",
        "is_quantity_estimated": "",
        "is_net_weight_estimated": "",
        "is_reported": "",
        "is_aggregate": "",
        "data_status": status,
        "data_source": "UN Comtrade public API; China-reported annual merchandise trade",
        "source_query_scope": "Reporter CHN (156); partner RUS (643); annual; HS AG2",
        "retrieved_on": RETRIEVED_ON,
        "retrieval_error": error,
    }
    row.update(shock_metadata(year))
    return row


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    query_log: list[dict[str, Any]] = []
    success_queries = 0

    for year in range(START_YEAR, END_YEAR + 1):
        for flow_code in ("X", "M"):
            print(f"fetching {year} {flow_code}", flush=True)
            payload, error = fetch_json(year, flow_code)
            if payload is None:
                rows.append(availability_row(year, flow_code, "retrieval_failed", error))
                query_log.append({"year": year, "flow_code": flow_code, "status": "retrieval_failed", "error": error, "records": 0})
                continue

            data = payload.get("data", [])
            if not isinstance(data, list):
                data = []
            normalized: list[dict[str, Any]] = []
            for record in data:
                if not isinstance(record, dict):
                    continue
                code = normalize_code(record)
                # AG2 should return chapter-level rows. Keep two-digit chapters only;
                # skip TOTAL to avoid double counting in later aggregation.
                if len(code) == 2 and code.isdigit():
                    normalized.append(normalized_row(record, year, flow_code))

            if normalized:
                rows.extend(normalized)
                success_queries += 1
                status = "available"
            else:
                rows.append(availability_row(year, flow_code, "not_available_or_empty"))
                status = "not_available_or_empty"
            query_log.append({
                "year": year,
                "flow_code": flow_code,
                "status": status,
                "records": len(normalized),
                "api_count": payload.get("count"),
                "elapsed_time": payload.get("elapsedTime"),
            })
            # Stay comfortably below anonymous API request-rate limits.
            time.sleep(1.05)

    # Deduplicate exact year-flow-HS2 keys, retaining the first response row.
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    availability: list[dict[str, Any]] = []
    for row in rows:
        if row["record_type"] == "trade":
            key = (row["year"], row["flow_code"], row["hs2_code"])
            deduped.setdefault(key, row)
        else:
            availability.append(row)
    rows = list(deduped.values()) + availability

    # Compute annual flow totals and within-flow shares using HS2 rows only.
    totals: dict[tuple[int, str], float] = defaultdict(float)
    for row in rows:
        if row["record_type"] != "trade":
            continue
        value = as_number(row["trade_value_usd"])
        if value is not None:
            totals[(int(row["year"]), str(row["flow_code"]))] += value
    for row in rows:
        key = (int(row["year"]), str(row["flow_code"]))
        total = totals.get(key)
        if row["record_type"] == "trade" and total and total > 0:
            value = as_number(row["trade_value_usd"]) or 0.0
            row["annual_flow_total_usd"] = format_number(total)
            row["share_of_annual_flow"] = format(value / total, ".12g")
        else:
            row["annual_flow_total_usd"] = ""
            row["share_of_annual_flow"] = ""
        row.setdefault("retrieval_error", "")

    rows.sort(key=lambda row: (
        int(row["year"]),
        0 if row["flow_code"] == "X" else 1,
        0 if row["record_type"] == "trade" else 1,
        int(row["hs2_code"]) if str(row["hs2_code"]).isdigit() else 999,
    ))

    fieldnames = [
        "record_type", "year", "reporter_code", "reporter_iso3", "reporter",
        "partner_code", "partner_iso3", "partner", "flow_code", "flow",
        "trade_direction", "exporter", "importer", "classification_code",
        "hs2_code", "hs2_description", "sector_group_en", "sector_group_zh",
        "trade_value_usd", "annual_flow_total_usd", "share_of_annual_flow",
        "cif_value_usd", "fob_value_usd", "net_weight_kg", "gross_weight_kg",
        "quantity", "quantity_unit", "is_quantity_estimated",
        "is_net_weight_estimated", "is_reported", "is_aggregate",
        "shock_event", "shock_phase", "shock_2008", "shock_2014",
        "shock_2020", "shock_2022", "data_status", "data_source",
        "source_query_scope", "retrieved_on", "retrieval_error",
    ]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    trade_rows = [row for row in rows if row["record_type"] == "trade"]
    available_years = sorted({int(row["year"]) for row in trade_rows})
    missing_queries = [item for item in query_log if item["status"] != "available"]
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "csv_file": str(CSV_PATH),
        "source": "UN Comtrade public API",
        "scope": {
            "years_requested": [START_YEAR, END_YEAR],
            "reporter": {"code": REPORTER_CODE, "iso3": "CHN", "name": "China"},
            "partner": {"code": PARTNER_CODE, "iso3": "RUS", "name": "Russian Federation"},
            "flows": ["X", "M"],
            "classification": "HS AG2",
        },
        "row_count": len(rows),
        "trade_row_count": len(trade_rows),
        "availability_row_count": len(availability),
        "available_year_min": min(available_years) if available_years else None,
        "available_year_max": max(available_years) if available_years else None,
        "successful_queries": success_queries,
        "total_queries": (END_YEAR - START_YEAR + 1) * 2,
        "missing_or_failed_queries": missing_queries,
        "query_log": query_log,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "csv": str(CSV_PATH),
        "summary": str(SUMMARY_PATH),
        "rows": len(rows),
        "trade_rows": len(trade_rows),
        "successful_queries": success_queries,
        "missing_queries": len(missing_queries),
    }, ensure_ascii=False), flush=True)

    # Fail only when the API was broadly inaccessible, while still allowing
    # the workflow to upload diagnostics through an `if: always()` step.
    return 0 if success_queries >= 40 else 2


if __name__ == "__main__":
    raise SystemExit(main())
