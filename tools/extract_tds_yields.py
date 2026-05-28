#!/usr/bin/env python3
"""Extract yield/consumption candidates from Tover TDS PDFs.

Usage:
  python tools/extract_tds_yields.py "/path/to/ita tds.zip"

The script reads the app product list from index.html, scans the PDFs in the
provided zip, and writes review files under data/. It is intentionally
conservative: it suggests the highest consumption found in a range so material
estimates do not undercount.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
OUT_JSON = ROOT / "data" / "tds-yield-candidates.json"
OUT_MD = ROOT / "data" / "tds-yield-candidates.md"
OUT_CSV = ROOT / "data" / "tds-yield-candidates.csv"


@dataclass
class Product:
    category: str
    name: str
    format: str
    unit: str
    resa_mq: float
    dim_confezione: float


def normalize_name(value: str) -> str:
    value = value.lower()
    replacements = {
        "%": "",
        "+": " plus ",
        "&": " ",
        "'": "",
        ".": "",
        "/": "",
        "-": " ",
        "_": " ",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"\bit\b|\bita\b|\brev\d+\b|\brev\b", " ", value)
    value = re.sub(r"\bkit\b|\bkg\b|\bl\b|\blt\b|\blitro\b", " ", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def parse_products() -> list[Product]:
    html = INDEX.read_text(encoding="utf-8")
    pattern = re.compile(
        r'\{\s*category:\s*"(?P<category>[^"]+)",\s*'
        r'name:\s*"(?P<name>[^"]+)",\s*'
        r'format:\s*"(?P<format>[^"]+)",\s*'
        r'unit:\s*"(?P<unit>[^"]+)",\s*'
        r'resa_mq:\s*(?P<resa>[0-9.]+),\s*'
        r'dim_confezione:\s*(?P<dim>[0-9.]+)',
    )
    products: list[Product] = []
    for match in pattern.finditer(html):
        products.append(
            Product(
                category=match.group("category"),
                name=match.group("name"),
                format=match.group("format"),
                unit=match.group("unit"),
                resa_mq=float(match.group("resa")),
                dim_confezione=float(match.group("dim")),
            )
        )
    return products


def clean_text(text: str) -> str:
    replacements = {
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
        "/f_": "fi",
        "m/two.superior": "m2",
        "m/two": "m2",
        "m.sup.2": "m2",
        "m\u00b2": "m2",
        "lt": "l",
        "litro": "l",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"m\s+2", "m2", text, flags=re.I)
    return text


def pdf_text_from_zip(zip_file: zipfile.ZipFile, name: str) -> str:
    data = zip_file.read(name)
    reader = PdfReader(io.BytesIO(data))
    return clean_text("\n".join(page.extract_text() or "" for page in reader.pages))


def pdf_product_name(path: str) -> str:
    name = Path(path).name
    name = re.sub(r"\.pdf$", "", name, flags=re.I)
    name = re.sub(r"[_ -]*it[a]?[_ -]*rev\d+.*$", "", name, flags=re.I)
    name = re.sub(r"[_ -]*IT[_ -]*REV\d+.*$", "", name, flags=re.I)
    return name.replace("_", " ").strip()


def score_match(product: Product, pdf_name: str) -> int:
    product_key = normalize_name(product.name)
    pdf_key = normalize_name(pdf_product_name(pdf_name))
    if not product_key or not pdf_key:
        return 0
    if product_key == pdf_key:
        return 100
    if product_key in pdf_key or pdf_key in product_key:
        return 85
    product_tokens = set(re.findall(r"[a-z0-9]+", product.name.lower()))
    pdf_tokens = set(re.findall(r"[a-z0-9]+", pdf_product_name(pdf_name).lower()))
    return len(product_tokens & pdf_tokens) * 10


def find_pdf_for_product(product: Product, pdf_names: list[str]) -> tuple[str | None, int]:
    scored = [(score_match(product, pdf_name), pdf_name) for pdf_name in pdf_names]
    scored.sort(reverse=True)
    score, name = scored[0] if scored else (0, None)
    if score < 30:
        return None, score
    return name, score


def interesting_blocks(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if re.search(r"\bresa\b|\bconsumo\b|g/m2|kg/m2|m2/l|m2 /l|spatola|rullo|pennello", line, re.I):
            block = " ".join(lines[index : index + 5])
            if block not in blocks:
                blocks.append(block)
    return blocks


def parse_decimal(value: str) -> float:
    return float(value.replace(",", "."))


def application_label_from_context(text: str) -> str:
    lower = text.lower()
    labels = [
        ("consolid", "Consolidante"),
        ("spolver", "Consolidante"),
        ("impermeabil", "Impermeabilizzante"),
    ]
    positions = [(lower.find(key), label) for key, label in labels if key in lower]
    if positions:
        return sorted(positions)[0][1]
    if "spatola" in lower:
        return "Applicazione a spatola"
    if "rullo" in lower:
        return "Applicazione a rullo"
    if "pennello" in lower:
        return "Applicazione a pennello"
    return "Applicazione da scheda tecnica"


def coats_from_context(text: str) -> int | None:
    lower = text.lower()
    if re.search(r"\b(due|2)\s+mani?\b", lower):
        return 2
    if re.search(r"\b(una|1)\s+mani?\b", lower):
        return 1
    return None


def tool_from_context(text: str) -> str:
    lower = text.lower()
    if re.search(r"tkb\s*a1\s*/\s*a2", lower):
        return "Spatola TKB A1/A2"
    if re.search(r"tkb\s*/?\s*b1", lower):
        return "Spatola TKB/B1"
    if re.search(r"tkb\s*/?\s*b2", lower):
        return "Spatola TKB/B2"
    if re.search(r"tkb\s*/?\s*a2", lower):
        return "Spatola TKB/A2"
    match = re.search(r"(spatola[^.;,\n)]*(?:a2|b1|10 mm|8 mm|n\.?\s*\d+)?)", text, re.I)
    if match:
        tool = match.group(1)
        tool = re.split(r"\b(Carico|Temperatura|Min|Da|Conservazione)\b", tool, flags=re.I)[0]
        return tool.strip(" -")
    tools = []
    if re.search(r"\brullo\b", text, re.I):
        tools.append("rullo")
    if re.search(r"\bpennello\b", text, re.I):
        tools.append("pennello")
    return "/".join(tools)


def build_candidate(source: str, candidate_type: str, suggested: float, source_unit: str, product_unit: str) -> dict:
    return {
        "label": application_label_from_context(source),
        "coats": coats_from_context(source),
        "source": source[:350],
        "type": candidate_type,
        "suggested_resa_mq": round(suggested, 4),
        "source_unit": source_unit,
        "tool": tool_from_context(source),
        "needs_unit_review": product_unit != source_unit and not (source_unit == "g" and product_unit == "kg"),
        "confidence": "review",
    }


def candidates_from_block(block: str, product_unit: str) -> list[dict]:
    clean = re.sub(r"\s+", " ", block)
    lower = clean.lower()
    candidates: list[dict] = []

    # Examples: 250 - 300 g/m2, 1,0 - 1,5 kg/m2, 100 g/m2
    consumption_pattern = re.compile(
        r"(?P<a>\d+(?:[,.]\d+)?)\s*(?:-|a|/)\s*(?P<b>\d+(?:[,.]\d+)?)?\s*(?P<unit>g|kg|l)\s*/?\s*m\s*2"
    )
    for consumption_match in consumption_pattern.finditer(lower):
        a = parse_decimal(consumption_match.group("a"))
        b = parse_decimal(consumption_match.group("b") or consumption_match.group("a"))
        unit = consumption_match.group("unit")
        suggested = max(a, b)
        if unit == "g":
            suggested = suggested / 1000
            unit = "kg"
        prefix_start = max(consumption_match.start() - 45, 0)
        prefix = clean[prefix_start : consumption_match.start()]
        lower_prefix = prefix.lower()
        local_starts = [lower_prefix.rfind("spatola"), lower_prefix.rfind("resa"), lower_prefix.rfind("consumo")]
        local_start = max(local_starts)
        start = prefix_start + local_start if local_start >= 0 else consumption_match.start()
        end = min(consumption_match.end() + 160, len(clean))
        source = clean[start:end]
        candidates.append(build_candidate(source, "consumption", suggested, unit, product_unit))

    # Examples: 14 - 16 m2/l, 35 - 40 m2/l
    coverage_pattern = re.compile(
        r"(?P<a>\d+(?:[,.]\d+)?)\s*(?:-|a|/)\s*(?P<b>\d+(?:[,.]\d+)?)?\s*m2\s*/\s*l"
    )
    for coverage_match in coverage_pattern.finditer(lower):
        a = parse_decimal(coverage_match.group("a"))
        b = parse_decimal(coverage_match.group("b") or coverage_match.group("a"))
        lower_coverage = min(a, b)
        if lower_coverage <= 0:
            continue
        start = coverage_match.start()
        end = min(coverage_match.end() + 160, len(clean))
        source = clean[start:end]
        candidates.append(build_candidate(source, "coverage", 1 / lower_coverage, product_unit, product_unit))

    return candidates


def unique_candidates(candidates: Iterable[dict]) -> list[dict]:
    seen: set[tuple] = set()
    output: list[dict] = []
    for candidate in candidates:
        key = (
            candidate.get("suggested_resa_mq"),
            candidate.get("label"),
            candidate.get("source_unit"),
            candidate.get("tool"),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def build_report(zip_path: Path) -> dict:
    products = parse_products()
    report = {"zip": str(zip_path), "products": []}
    with zipfile.ZipFile(zip_path) as zip_file:
        pdf_names = [
            name
            for name in zip_file.namelist()
            if name.lower().endswith(".pdf") and not name.startswith("__MACOSX/")
        ]
        text_cache: dict[str, str] = {}

        for product in products:
            pdf_name, score = find_pdf_for_product(product, pdf_names)
            entry = {
                **asdict(product),
                "matched_pdf": pdf_name,
                "match_score": score,
                "candidates": [],
            }
            if pdf_name:
                if pdf_name not in text_cache:
                    text_cache[pdf_name] = pdf_text_from_zip(zip_file, pdf_name)
                blocks = interesting_blocks(text_cache[pdf_name])
                candidates = []
                for block in blocks:
                    candidates.extend(candidates_from_block(block, product.unit))
                entry["candidates"] = unique_candidates(candidates)
            report["products"].append(entry)
    return report


def write_markdown(report: dict) -> str:
    lines = [
        "# Tover TDS yield candidates",
        "",
        "Generated from the supplied TDS zip. Suggested `resa_mq` is conservative and must be reviewed against the PDF before importing in bulk.",
        "",
    ]
    matched = sum(1 for item in report["products"] if item["matched_pdf"])
    with_candidates = sum(1 for item in report["products"] if item["candidates"])
    lines.append(f"- Products in app: {len(report['products'])}")
    lines.append(f"- Products matched to a PDF: {matched}")
    lines.append(f"- Products with yield candidates: {with_candidates}")
    lines.append("")

    for item in report["products"]:
        lines.append(f"## {item['name']} - {item['format']}")
        lines.append(f"- Current app resa_mq: {item['resa_mq']} {item['unit']}/m2")
        lines.append(f"- Matched PDF: {item['matched_pdf'] or 'NOT FOUND'}")
        if not item["candidates"]:
            lines.append("- Candidates: none found")
            lines.append("")
            continue
        for index, candidate in enumerate(item["candidates"], start=1):
            lines.append(
                f"- Candidate {index}: `{candidate['suggested_resa_mq']}` {candidate['source_unit']}/m2"
                f" | label: {candidate.get('label') or '-'}"
                f" | type: {candidate['type']}"
                f" | tool: {candidate.get('tool') or '-'}"
                f" | unit review: {'yes' if candidate.get('needs_unit_review') else 'no'}"
            )
            lines.append(f"  Source: {candidate['source']}")
        lines.append("")
    return "\n".join(lines)


def write_csv(report: dict) -> None:
    fieldnames = [
        "category",
        "name",
        "format",
        "product_unit",
        "current_resa_mq",
        "matched_pdf",
        "match_score",
        "candidate_label",
        "coats",
        "tool",
        "suggested_resa_mq",
        "source_unit",
        "needs_unit_review",
        "candidate_type",
        "source",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in report["products"]:
            if not item["candidates"]:
                writer.writerow(
                    {
                        "category": item["category"],
                        "name": item["name"],
                        "format": item["format"],
                        "product_unit": item["unit"],
                        "current_resa_mq": item["resa_mq"],
                        "matched_pdf": item["matched_pdf"] or "",
                        "match_score": item["match_score"],
                    }
                )
                continue
            for candidate in item["candidates"]:
                writer.writerow(
                    {
                        "category": item["category"],
                        "name": item["name"],
                        "format": item["format"],
                        "product_unit": item["unit"],
                        "current_resa_mq": item["resa_mq"],
                        "matched_pdf": item["matched_pdf"] or "",
                        "match_score": item["match_score"],
                        "candidate_label": candidate.get("label") or "",
                        "coats": candidate.get("coats") or "",
                        "tool": candidate.get("tool") or "",
                        "suggested_resa_mq": candidate.get("suggested_resa_mq"),
                        "source_unit": candidate.get("source_unit"),
                        "needs_unit_review": candidate.get("needs_unit_review"),
                        "candidate_type": candidate.get("type"),
                        "source": candidate.get("source"),
                    }
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path", type=Path)
    args = parser.parse_args()

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(args.zip_path)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.write_text(write_markdown(report), encoding="utf-8")
    write_csv(report)

    matched = sum(1 for item in report["products"] if item["matched_pdf"])
    with_candidates = sum(1 for item in report["products"] if item["candidates"])
    print(f"Products: {len(report['products'])}")
    print(f"Matched PDFs: {matched}")
    print(f"With candidates: {with_candidates}")
    print(f"Wrote: {OUT_JSON}")
    print(f"Wrote: {OUT_MD}")
    print(f"Wrote: {OUT_CSV}")


if __name__ == "__main__":
    main()
