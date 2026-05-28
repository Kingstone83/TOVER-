#!/usr/bin/env python3
"""Import reviewed-compatible TDS yield candidates into the calculator.

This uses data/tds-yield-candidates.json and updates product rows in index.html
with `applications`. Candidates marked `needs_unit_review` are intentionally not
imported because the calculator cannot divide kg/m2 consumption by a litre pack
without a density/conversion value.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
REPORT = ROOT / "data" / "tds-yield-candidates.json"


PRODUCT_RE = re.compile(
    r'^(?P<indent>\s*)\{\s*category:\s*"(?P<category>[^"]+)",\s*'
    r'name:\s*"(?P<name>[^"]+)",\s*'
    r'format:\s*"(?P<format>[^"]+)",\s*'
    r'unit:\s*"(?P<unit>[^"]+)",\s*'
    r'resa_mq:\s*(?P<resa>[0-9.]+),\s*'
    r'dim_confezione:\s*(?P<dim>[0-9.]+)'
    r'(?:,\s*applications:\s*\[[^\]]*\])?\s*\}(?P<comma>,?)$'
)


def js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def normalize_label(candidate: dict) -> str:
    label = candidate.get("label") or "Applicazione da scheda tecnica"
    source = (candidate.get("source") or "").lower()
    if "dust-proof" in source or "hardener" in source:
        return "Consolidante"
    if "waterproof" in source:
        return "Impermeabilizzante"
    return label


def normalize_coats(label: str, candidate: dict) -> int | None:
    source = (candidate.get("source") or "").lower()
    coats = candidate.get("coats")
    if label == "Consolidante":
        return 1
    if label == "Impermeabilizzante":
        return 2
    if "una mano" in source or "one coat" in source:
        return 1
    if "due mani" in source or "two coats" in source:
        return 2
    return coats


def normalize_tool(candidate: dict) -> str:
    source = (candidate.get("source") or "").lower()
    raw_tool = (candidate.get("tool") or "").strip()
    resa = float(candidate.get("suggested_resa_mq") or 0)

    if re.search(r"\b200\s*-\s*300\b", source):
        return "Spatola TKB A1/A2"
    if re.search(r"\b300\s*-\s*350\b", source):
        return "Spatola TKB/B1"
    if re.search(r"\b350\s*-\s*450\b", source):
        return "Spatola TKB/B2"
    if "b2" in source and resa >= 0.35:
        return "Spatola TKB/B2"
    if "b1" in source:
        return "Spatola TKB/B1"
    if "a2" in source:
        return "Spatola TKB/A2"
    if "tbk a2" in source:
        return "Spatola TKB/A2"

    mm_match = re.search(r"spatola\s+(?:da\s+)?(?P<mm>\d+)\s*mm", raw_tool or source, re.I)
    if mm_match:
        return f"Spatola da {mm_match.group('mm')} mm"

    if raw_tool and raw_tool.lower().startswith("spatola"):
        return "Spatola"
    return raw_tool


def format_resa(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def candidate_to_application(candidate: dict, pdf_name: str) -> dict:
    label = normalize_label(candidate)
    tool = normalize_tool(candidate)
    if tool and label == "Applicazione da scheda tecnica":
        label = "Applicazione a spatola"
    application = {
        "label": label,
        "resa_mq": float(candidate["suggested_resa_mq"]),
        "note": f"Da scheda tecnica: {Path(pdf_name).name}",
    }
    coats = normalize_coats(label, candidate)
    if coats:
        application["coats"] = coats
    if tool:
        application["tool"] = tool
    return application


def applications_js(applications: list[dict]) -> str:
    parts = []
    for application in applications:
        props = [
            f"label: {js_string(application['label'])}",
            f"resa_mq: {format_resa(application['resa_mq'])}",
        ]
        if application.get("coats"):
            props.append(f"coats: {application['coats']}")
        if application.get("tool"):
            props.append(f"tool: {js_string(application['tool'])}")
        if application.get("note"):
            props.append(f"note: {js_string(application['note'])}")
        parts.append("{ " + ", ".join(props) + " }")
    return "[" + ", ".join(parts) + "]"


def build_application_map() -> dict[tuple[str, str], list[dict]]:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    output: dict[tuple[str, str], list[dict]] = {}
    for product in report["products"]:
        pdf_name = product.get("matched_pdf") or ""
        applications: list[dict] = []
        seen: set[tuple] = set()
        for candidate in product["candidates"]:
            if candidate.get("needs_unit_review"):
                continue
            if candidate.get("source_unit") != product["unit"]:
                continue
            application = candidate_to_application(candidate, pdf_name)
            key = (
                application["label"],
                application.get("tool", ""),
                application.get("coats", ""),
                application["resa_mq"],
            )
            if key in seen:
                continue
            seen.add(key)
            applications.append(application)
        if applications:
            output[(product["name"], product["format"])] = applications
    return output


def update_index() -> tuple[int, int]:
    application_map = build_application_map()
    lines = INDEX.read_text(encoding="utf-8").splitlines()
    updated_lines: list[str] = []
    updated = 0

    for line in lines:
        match = PRODUCT_RE.match(line)
        if not match:
            updated_lines.append(line)
            continue

        key = (match.group("name"), match.group("format"))
        applications = application_map.get(key)
        if not applications:
            updated_lines.append(line)
            continue

        base = (
            f'{match.group("indent")}{{ category: {js_string(match.group("category"))}, '
            f'name: {js_string(match.group("name"))}, '
            f'format: {js_string(match.group("format"))}, '
            f'unit: {js_string(match.group("unit"))}, '
            f'resa_mq: {match.group("resa")}, '
            f'dim_confezione: {match.group("dim")}, '
            f'applications: {applications_js(applications)} '
            f'}}{match.group("comma")}'
        )
        updated_lines.append(base)
        updated += 1

    INDEX.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    return updated, len(application_map)


def main() -> None:
    updated, available = update_index()
    print(f"Applications available for products: {available}")
    print(f"Product rows updated: {updated}")


if __name__ == "__main__":
    main()
