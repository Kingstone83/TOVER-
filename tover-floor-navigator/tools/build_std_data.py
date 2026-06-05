#!/usr/bin/env python3
"""Build product data for the standalone Tover floor navigator.

The script scans a folder of Tover technical PDF sheets and extracts enough
structured data for a guided estimator: product name, inferred category,
package candidates, yield/consumption candidates, and short source snippets.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "products.json"


def clean_text(text: str) -> str:
    replacements = {
        "\u00a0": " ",
        "\u2013": "-",
        "\u2014": "-",
        "m\u00b2": "m2",
        "m.sup.2": "m2",
        "m/two": "m2",
        "lt": "l",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"m\s+2", "m2", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def product_name_from_file(path: Path) -> str:
    name = path.stem.replace("_", " ").strip()
    name = re.sub(r"\s*-\s*REV\s*\d+.*$", "", name, flags=re.I)
    name = re.sub(r"\s*it[a]?\s*rev\s*\d+.*$", "", name, flags=re.I)
    name = re.sub(r"\s*IT\s*-\s*REV\s*\d+.*$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" -")


def infer_category(name: str, text: str) -> str:
    product_name = name.lower()
    name_rules = [
        (r"sportfloor|line maker", "Pavimenti sportivi"),
        (r"deck|wpc|giardino|oil4sun|pro-deck|fuori.*esterno", "Legno in esterno"),
        (r"cleaner|pulito|deter|lavaggio|remover|sanipro|sanifloor|saniparquet|sani|grey free|tovclean|nettardue|make-up|resinal wax", "Pulizia e manutenzione"),
        (r"tovcol|adesol|gekol|sigil|tovstik|solver|stripcoll", "Adesivi e sigillanti"),
        (r"primer|idroblok|adeblok|nano-fix|pepp|rockfloor|lvt leveller|fondo isolante|rallenty", "Primer e sottofondi"),
        (r"stucco|fullgap|epofill|fast&full|fondo|base gold|super gold", "Stucchi e fondi"),
        (r"olio|oil|wax|tintoretto|color|tingo|antique|belle epoque|art deco|alchimia|contrasto|xilocolor|pasta color", "Oli, cere e coloranti"),
        (r"lux|lak|maxima|bella|uniqua|home maxi|smart|idro|durolak|monolux|protect|cristal|resinal|myfloor|epoxy|firestop", "Vernici e finiture"),
    ]
    for pattern, category in name_rules:
        if re.search(pattern, product_name):
            return category

    value = f"{name} {text[:900]}".lower()
    rules = [
        (r"\badesiv[oi]\b|incollaggio|collante", "Adesivi e sigillanti"),
        (r"\bprimer\b|consolidante|impermeabilizzante", "Primer e sottofondi"),
        (r"\bvernice\b|verniciatura|sovraverniciatura", "Vernici e finiture"),
        (r"\bolio\b|\bcera\b|colorante", "Oli, cere e coloranti"),
    ]
    for pattern, category in rules:
        if re.search(pattern, value):
            return category
    return "Prodotti Tover"


def infer_tags(name: str, category: str) -> dict[str, list[str]]:
    value = f"{name} {category}".lower()
    subfloors = ["cemento", "assorbente"]
    coverings = ["sottofondo"]
    goals = ["standard"]

    if "adesivi" in category.lower():
        coverings = ["parquet"]
        if re.search(r"lvt|resilient|pvc|linoleum|wet", value):
            coverings = ["lvt"]
        subfloors = ["cemento", "legno", "non_assorbente"]
    elif "primer" in category.lower():
        coverings = ["sottofondo", "parquet", "lvt"]
        if re.search(r"idroblok|adeblok|pu|ms", value):
            goals.append("umidita")
            subfloors.append("umido")
        if re.search(r"nano|pepp|toverfix", value):
            subfloors.append("non_assorbente")
    elif "vernici" in category.lower():
        coverings = ["vernice"]
        subfloors = ["legno"]
        if re.search(r"antislip|grip", value):
            goals.append("antiscivolo")
        if re.search(r"matt|natur|essenza|lympha|natural", value):
            goals.append("naturale")
    elif "oli" in category.lower():
        coverings = ["olio"]
        subfloors = ["legno"]
        goals.append("naturale")
    elif "esterno" in category.lower():
        coverings = ["esterno"]
        subfloors = ["legno"]
    elif "sportivi" in category.lower():
        coverings = ["vernice"]
        goals.append("antiscivolo")
    elif "pulizia" in category.lower():
        coverings = ["manutenzione"]

    if re.search(r"fast|smart|mono|start|rapid|quick", value):
        goals.append("rapido")

    return {
        "subfloors": sorted(set(subfloors)),
        "coverings": sorted(set(coverings)),
        "goals": sorted(set(goals)),
    }


def parse_decimal(value: str) -> float:
    return float(value.replace(",", "."))


def source_slice(text: str, start: int, end: int) -> str:
    left = max(0, start - 70)
    right = min(len(text), end + 160)
    return text[left:right].strip()


def parse_yields(text: str) -> list[dict]:
    candidates: list[dict] = []

    consumption = re.compile(
        r"(?P<a>\d+(?:[,.]\d+)?)\s*(?:-|a|/)\s*(?P<b>\d+(?:[,.]\d+)?)?\s*(?P<unit>g|kg|l)\s*/?\s*m2",
        re.I,
    )
    for match in consumption.finditer(text):
        a = parse_decimal(match.group("a"))
        b = parse_decimal(match.group("b") or match.group("a"))
        unit = match.group("unit").lower()
        amount = max(a, b)
        if unit == "g":
            amount = amount / 1000
            unit = "kg"
        candidates.append(
            {
                "label": label_from_context(text, match.start()),
                "type": "consumo",
                "amount": round(amount, 4),
                "unit": unit.upper() if unit == "l" else unit,
                "source": source_slice(text, match.start(), match.end()),
            }
        )

    coverage = re.compile(r"(?P<a>\d+(?:[,.]\d+)?)\s*(?:-|a|/)\s*(?P<b>\d+(?:[,.]\d+)?)?\s*m2\s*/\s*(?P<unit>l|kg)", re.I)
    for match in coverage.finditer(text):
        a = parse_decimal(match.group("a"))
        b = parse_decimal(match.group("b") or match.group("a"))
        lower_coverage = min(a, b)
        if lower_coverage <= 0:
            continue
        unit = match.group("unit").lower()
        candidates.append(
            {
                "label": label_from_context(text, match.start()),
                "type": "resa",
                "amount": round(1 / lower_coverage, 4),
                "unit": unit.upper() if unit == "l" else unit,
                "source": source_slice(text, match.start(), match.end()),
            }
        )

    unique: list[dict] = []
    seen = set()
    for item in candidates:
        key = (item["amount"], item["unit"], item["label"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:6]


def label_from_context(text: str, position: int) -> str:
    context = text[max(0, position - 120) : position + 120].lower()
    if "impermeabil" in context or "umid" in context:
        return "Barriera umidita"
    if "consolid" in context or "spolver" in context:
        return "Consolidamento"
    if "spatola" in context:
        return "Applicazione a spatola"
    if "rullo" in context:
        return "Applicazione a rullo"
    if "pennello" in context:
        return "Applicazione a pennello"
    return "Applicazione da scheda tecnica"


def parse_packages(text: str) -> list[dict]:
    window_match = re.search(r"(confezioni?|formati?).{0,280}", text, re.I)
    window = window_match.group(0) if window_match else text[:1800]
    packages = []
    seen = set()

    kit_pattern = re.compile(
        r"(?P<a>\d+(?:[,.]\d+)?)\s*\+\s*(?P<b>\d+(?:[,.]\d+)?)\s*(?P<unit>kg|l)\b",
        re.I,
    )
    for match in kit_pattern.finditer(window):
        amount = parse_decimal(match.group("a")) + parse_decimal(match.group("b"))
        unit = match.group("unit").lower()
        key = (round(amount, 4), unit)
        if key in seen:
            continue
        seen.add(key)
        packages.append({"amount": round(amount, 4), "unit": unit.upper() if unit == "l" else unit})

    pattern = re.compile(r"(?P<amount>\d+(?:[,.]\d+)?)\s*(?P<unit>kg|l)\b", re.I)
    for match in pattern.finditer(window):
        amount = parse_decimal(match.group("amount"))
        unit = match.group("unit").lower()
        if amount <= 0 or amount > 100:
            continue
        key = (amount, unit)
        if key in seen:
            continue
        seen.add(key)
        packages.append({"amount": amount, "unit": unit.upper() if unit == "l" else unit})
    return packages[:5]


def read_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return clean_text(" ".join(page.extract_text() or "" for page in reader.pages))


def build(pdf_dir: Path) -> dict:
    products = []
    errors = []
    for path in sorted(pdf_dir.rglob("*.pdf")):
        if path.name.startswith("."):
            continue
        try:
            text = read_pdf_text(path)
            name = product_name_from_file(path)
            category = infer_category(name, text)
            packages = parse_packages(text)
            yields = parse_yields(text)
            tags = infer_tags(name, category)
            products.append(
                {
                    "id": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
                    "name": name,
                    "category": category,
                    "pdfName": path.name,
                    "pdfPath": str(path),
                    "packages": packages,
                    "yields": yields,
                    "tags": tags,
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"file": str(path), "error": str(exc)})
    return {"sourceDir": str(pdf_dir), "productCount": len(products), "errors": errors, "products": products}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_dir", type=Path, nargs="?", default=Path("/Users/michele/Desktop/STD"))
    args = parser.parse_args()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = build(args.pdf_dir)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Products: {data['productCount']}")
    print(f"Errors: {len(data['errors'])}")
    print(f"Wrote: {OUT}")


if __name__ == "__main__":
    main()
