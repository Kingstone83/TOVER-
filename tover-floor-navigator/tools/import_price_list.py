#!/usr/bin/env python3
"""Import Tover price-list rows into the navigator product database."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
PRODUCTS_JSON = ROOT / "data" / "products.json"
OUT_JSON = ROOT / "data" / "products.json"
OUT_PRICES = ROOT / "data" / "price-list.json"


STOP_LINES = {
    "LISTINO PREZZI",
    "2026",
    "PRODOTTI",
    "CONFEZIONI",
    "PREZZO EURO",
    "LISTINO prezzi 2026",
    "PREZZI VALIDI A partire dal 1° MAGGIO 2026",
    "NEW",
}


def normalize(value: str) -> str:
    value = value.lower()
    value = value.replace("&", " e ")
    value = value.replace("+", " plus ")
    value = value.replace("’", "'")
    value = re.sub(r"\bh20\b", "h2o", value)
    value = re.sub(r"\bpu\s*100\b", "pu100", value)
    value = re.sub(r"\bpu-fix\b", "pu fix", value)
    value = re.sub(r"\bextra matt\b", "extra-matt", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def clean_line(line: str) -> str:
    line = line.replace("\u00a0", " ")
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def pdf_lines(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    lines: list[str] = []
    for page in reader.pages[4:22]:
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            line = clean_line(raw_line)
            if line:
                lines.append(line)
    return lines


def product_aliases(product_name: str) -> set[str]:
    aliases = {product_name}
    aliases.add(product_name.replace("-", " "))
    aliases.add(product_name.replace("/", ""))
    aliases.add(re.sub(r"RS(\d+)", r"RS/\1", product_name))
    aliases.add(product_name.replace("H20", "H2O"))
    aliases.add(product_name.replace("PU 100", "PU100"))
    aliases.add(product_name.replace("PU-FIX", "PU-Fix"))
    aliases.add(product_name.replace("Fullgap", "Full Gap"))
    aliases.add(product_name.replace("Nano-fix", "Nano-Fix"))
    aliases.add(product_name.replace("Tovcol LVT Fibre Plus", "Tovcol LVT Fibre+"))
    aliases.add(product_name.replace("P.EP.P.", "P. E P. P."))
    aliases.add(product_name.replace("SaniPro", "Sani Pro"))
    aliases.add(product_name.replace("PEPP", "P. E P. P."))
    aliases.add(product_name.replace("Clean&Go", "Clean&Go"))
    aliases.add(product_name.replace("Lega stucco", "Lega Stucco"))
    aliases.add(product_name.replace("Lux Matt", "Lux Matt"))
    aliases.add(product_name.replace("Maxi Oil Color", "Maxi Oil"))
    return {alias.strip() for alias in aliases if alias.strip()}


def build_name_map(products: list[dict]) -> dict[str, str]:
    name_map: dict[str, str] = {}
    for product in products:
        for alias in product_aliases(product["name"]):
            key = normalize(alias)
            if key:
                name_map.setdefault(key, product["name"])
    return name_map


def is_category(line: str) -> bool:
    return line.isupper() and not re.search(r"\d+[,.]?\d*\s*(?:kg|l|ml|pz)\b", line, re.I)


def is_product_line(line: str, name_map: dict[str, str]) -> str | None:
    key = normalize(line)
    if key in name_map:
        return name_map[key]
    for alias_key, canonical in name_map.items():
        if key.startswith(alias_key) and len(alias_key) >= 5:
            rest = key[len(alias_key) :]
            if not rest or re.search(r"^(\d|bianco|color|neutro|natur|extra|disponibile|ver|ral|nero|ml|kg|l|pz)", rest):
                return canonical
    return None


def split_inline_product(line: str, product_name: str) -> tuple[str, str]:
    pattern = re.escape(product_name)
    match = re.match(pattern + r"\s+(.*)$", line, re.I)
    if match:
        return product_name, match.group(1)
    for alias in sorted(product_aliases(product_name), key=len, reverse=True):
        match = re.match(re.escape(alias) + r"\s+(.*)$", line, re.I)
        if match:
            return alias, match.group(1)
    return product_name, ""


def extract_blocks(lines: list[str], products: list[dict]) -> dict[str, list[str]]:
    name_map = build_name_map(products)
    blocks: dict[str, list[str]] = {}
    current: str | None = None

    for line in lines:
        if line in STOP_LINES or is_category(line):
            continue
        product_name = is_product_line(line, name_map)
        if product_name:
            current = product_name
            blocks.setdefault(current, [])
            _, rest = split_inline_product(line, product_name)
            if rest:
                blocks[current].append(rest)
            continue
        if current:
            blocks[current].append(line)
    return blocks


def parse_number(value: str) -> float:
    return float(value.replace(".", "").replace(",", "."))


def package_size(format_text: str) -> float | None:
    text = format_text.replace(",", ".")
    kit_match = re.search(r"\((?P<a>\d+(?:\.\d+)?)\s*\+\s*(?P<b>\d+(?:\.\d+)?)\)\s*(?P<unit>kg|l)\b", text, re.I)
    if kit_match:
        return round(float(kit_match.group("a")) + float(kit_match.group("b")), 4)
    sum_match = re.search(r"\b(?P<a>\d+(?:\.\d+)?)\s*\+\s*(?P<b>\d+(?:\.\d+)?)\s*(?P<unit>kg|l)\b", text, re.I)
    if sum_match:
        return round(float(sum_match.group("a")) + float(sum_match.group("b")), 4)
    pack_match = re.search(r"(?:\d+\s*x\s*)?(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>kg|l|ml|pz)\b", text, re.I)
    if not pack_match:
        return None
    size = float(pack_match.group("size"))
    unit = pack_match.group("unit").lower()
    if unit == "ml":
        return round(size / 1000, 4)
    return size


def parse_price_entries(block: list[str]) -> list[dict]:
    text = " ".join(block)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\d),(?=\d)", lambda m: m.group(0), text)

    token_pattern = re.compile(
        r"\d+,\d{2}\s*(?:kg|L|l|pz)|"
        r"\d+,\d{2}|"
        r"\d+x\(\d+(?:,\d+)?\+\d+(?:,\d+)?\)\s*(?:kg|L|l)|"
        r"\d+(?:,\d+)?\+\d+(?:,\d+)?\s*(?:kg|L|l)|"
        r"\d+x\d+(?:,\d+)?\s*(?:kg|L|l|ml|pz)|"
        r"\d+(?:,\d+)?\s*(?:kg|L|l|ml|pz)|"
        r"kg|L|l|pz",
        re.I,
    )
    tokens = [match.group(0).strip() for match in token_pattern.finditer(text)]
    packages: list[str] = []
    prices: list[float] = []
    units: list[str] = []
    for token in tokens:
        price_with_unit = re.fullmatch(r"(\d+,\d{2})\s*(kg|L|l|pz)", token, re.I)
        if price_with_unit:
            prices.append(parse_number(price_with_unit.group(1)))
            units.append("L" if price_with_unit.group(2).lower() == "l" else price_with_unit.group(2).lower())
        elif re.fullmatch(r"\d+,\d{2}", token):
            prices.append(parse_number(token))
        elif re.fullmatch(r"kg|L|l|pz", token, re.I):
            units.append("L" if token.lower() == "l" else token.lower())
        elif re.search(r"(kg|L|l|ml|pz)\b", token, re.I):
            packages.append(token)

    entries = []
    count = min(len(packages), len(prices))
    for index in range(count):
        unit = units[index] if index < len(units) else infer_price_unit(packages[index])
        entries.append(
            {
                "format": packages[index],
                "amount": prices[index],
                "unit": unit,
                "packageSize": package_size(packages[index]),
            }
        )
    return entries


def infer_price_unit(format_text: str) -> str:
    if re.search(r"\bml\b|\bpz\b", format_text, re.I):
        return "pz"
    if re.search(r"\bL\b|\bl\b", format_text):
        return "L"
    return "kg"


def attach_prices(products: list[dict], prices: dict[str, list[dict]]) -> int:
    attached = 0
    for product in products:
        entries = prices.get(product["name"], [])
        product["prices"] = entries
        if entries:
            attached += 1
    return attached


def write_report(products: Iterable[dict], prices: dict[str, list[dict]], source: Path) -> dict:
    products = list(products)
    matched = sum(1 for product in products if product.get("prices"))
    report = {
        "source": str(source),
        "matchedProducts": matched,
        "products": [
            {
                "name": product["name"],
                "pdfName": product["pdfName"],
                "prices": product.get("prices", []),
            }
            for product in products
            if product.get("prices")
        ],
    }
    OUT_PRICES.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("price_pdf", type=Path)
    parser.add_argument("--products", type=Path, default=PRODUCTS_JSON)
    args = parser.parse_args()

    data = json.loads(args.products.read_text(encoding="utf-8"))
    products = data["products"]
    lines = pdf_lines(args.price_pdf)
    blocks = extract_blocks(lines, products)
    price_map = {name: parse_price_entries(block) for name, block in blocks.items()}
    price_map = {name: entries for name, entries in price_map.items() if entries}
    attached = attach_prices(products, price_map)
    data["priceList"] = {"source": str(args.price_pdf), "matchedProducts": attached}
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report = write_report(products, price_map, args.price_pdf)
    print(f"Price rows matched to products: {attached}")
    print(f"Products in report: {len(report['products'])}")
    print(f"Wrote: {OUT_JSON}")
    print(f"Wrote: {OUT_PRICES}")


if __name__ == "__main__":
    main()
