"""
BMI Bulk Data Ingester — 把 BMI (Fitch Solutions) 导出数据 → research-vault
================================================================================
BMI 常见导出格式:
  - CSV/XLSX: Country Risk scores, Macro forecasts, Industry outlooks
  - PDF: Full reports (需要 pdf-to-text)

产物: 每个 dataset 一个 markdown 文件，放到:
  research-vault/knowledge/finance/macro/       (宏观预测类)
  research-vault/knowledge/finance/knowledge/   (分析/理论类)
  research-vault/knowledge/data-sources/bmi/    (raw 数据快照，便于追溯)

每个 .md 文件带 frontmatter:
  - id: bmi-<topic>-<date>
  - source: BMI / Fitch Solutions
  - tags: [country, sector, metric_type]
  - ingested: ISO8601

用法:
  python bmi_ingester.py csv --input ~/Downloads/bmi_country_risk.csv --country USA
  python bmi_ingester.py xlsx --input ~/Downloads/bmi_forecasts.xlsx
  python bmi_ingester.py scan-dir ~/Downloads/bmi-exports/
  python bmi_ingester.py review         # 看已摄入的所有 BMI 文件
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import subprocess

VAULT = Path("/Users/0xvox/Documents/Evensong/research-vault/knowledge")
DATA_SOURCES = VAULT / "data-sources" / "bmi"
MACRO = VAULT / "finance" / "macro"
KNOWLEDGE = VAULT / "finance" / "knowledge"


def ensure_dirs():
    for d in (DATA_SOURCES, MACRO, KNOWLEDGE):
        d.mkdir(parents=True, exist_ok=True)


def infer_category(filename: str) -> tuple[Path, list[str]]:
    """基于文件名猜测分类 + tags"""
    name = filename.lower()
    tags = ["bmi", "fitch-solutions"]

    if any(k in name for k in ["risk", "political", "stability", "governance"]):
        cat = MACRO
        tags += ["country-risk", "political-risk"]
    elif any(k in name for k in ["gdp", "inflation", "fiscal", "macro", "forecast", "currency", "fx"]):
        cat = MACRO
        tags += ["macro-forecast", "economic-indicator"]
    elif any(k in name for k in ["oil", "gas", "commodity", "metal", "agri", "mining"]):
        cat = KNOWLEDGE
        tags += ["commodity", "market-forecast"]
    elif any(k in name for k in ["industry", "sector", "business", "trade"]):
        cat = KNOWLEDGE
        tags += ["industry-analysis", "sector"]
    elif any(k in name for k in ["company", "earnings", "corp", "equity"]):
        cat = KNOWLEDGE
        tags += ["company-analysis", "equity"]
    else:
        cat = DATA_SOURCES
        tags += ["raw-export"]

    # 国家标签提取
    country_codes = {"usa":"US","us":"US","china":"CN","cn":"CN","uk":"UK",
                     "japan":"JP","germany":"DE","france":"FR","india":"IN",
                     "brazil":"BR","russia":"RU","mexico":"MX","canada":"CA"}
    for kw, code in country_codes.items():
        if kw in name:
            tags.append(f"country-{code}")
            break

    return cat, tags


def csv_to_markdown(csv_path: Path, country: str | None = None) -> str:
    """CSV → 结构化 markdown (保留 data + 加 frontmatter)"""
    cat, tags = infer_category(csv_path.name)
    if country:
        tags.append(f"country-{country.upper()}")

    # 读 CSV
    rows = []
    with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
            if len(rows) > 500:
                break  # 限制大文件

    if not rows:
        return ""

    header = rows[0]
    data_rows = rows[1:]

    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    stem = csv_path.stem.lower().replace(" ","-").replace("_","-")
    fid = f"bmi-{stem[:50]}-{ts}"

    md = f"""---
id: {fid}
source: BMI / Fitch Solutions
ingested: {datetime.now(timezone.utc).isoformat()}
category: {cat.relative_to(VAULT)}
tags: {json.dumps(tags)}
format: csv
rows: {len(data_rows)}
---

# BMI Data: {csv_path.stem}

**Source**: BMI (Fitch Solutions) 内部导出
**Columns**: {len(header)}  |  **Rows**: {len(data_rows)}

## Schema

| Column | Sample |
|--------|--------|
"""
    for i, col in enumerate(header[:20]):
        sample = data_rows[0][i] if data_rows and i < len(data_rows[0]) else ""
        md += f"| {col} | {sample[:50]} |\n"

    # 数据样本
    md += f"\n## Data Sample (first 10 rows)\n\n"
    md += "| " + " | ".join(header[:8]) + " |\n"
    md += "|" + "---|" * min(8, len(header)) + "\n"
    for row in data_rows[:10]:
        md += "| " + " | ".join(str(c)[:30] for c in row[:8]) + " |\n"

    if len(data_rows) > 10:
        md += f"\n*({len(data_rows) - 10} more rows omitted, full CSV at {csv_path})*\n"

    # 自动提取关键数值（如果有数值列）
    numeric_cols = []
    for i, col in enumerate(header):
        try:
            vals = [float(r[i]) for r in data_rows[:20] if i < len(r) and r[i].replace('.','').replace('-','').isdigit()]
            if len(vals) >= 5:
                numeric_cols.append((col, vals))
        except Exception:
            pass

    if numeric_cols:
        md += f"\n## Key Metrics (auto-extracted)\n\n"
        for col, vals in numeric_cols[:5]:
            md += f"- **{col}**: min={min(vals):.2f}, max={max(vals):.2f}, avg={sum(vals)/len(vals):.2f}\n"

    md += f"\n## Raw Source\n\nOriginal file: `{csv_path}`\n"
    return md


def ingest_csv(csv_path: Path, country: str | None = None):
    ensure_dirs()
    if not csv_path.exists():
        print(f"❌ {csv_path} 不存在")
        sys.exit(1)

    cat, tags = infer_category(csv_path.name)
    md_content = csv_to_markdown(csv_path, country)
    if not md_content:
        print(f"❌ 空内容")
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    stem = csv_path.stem.lower().replace(" ","-").replace("_","-")[:50]
    out_path = cat / f"{ts}-bmi-{stem}.md"
    out_path.write_text(md_content, encoding="utf-8")
    print(f"✓ {csv_path.name} → {out_path.relative_to(VAULT.parent)}")
    print(f"  分类: {cat.relative_to(VAULT)}")
    print(f"  tags: {tags}")
    return out_path


def ingest_xlsx(xlsx_path: Path):
    """XLSX 转 CSV 再 ingest (用 openpyxl 或 csv export)"""
    try:
        import openpyxl
    except ImportError:
        # 用 Python 标准库 + subprocess
        print("⚠️ openpyxl 不可用，请先: pip install openpyxl")
        print(f"或手动转 CSV: ssconvert '{xlsx_path}' '{xlsx_path.with_suffix('.csv')}'")
        return

    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    for sheet_name in wb.sheetnames[:3]:   # 最多 3 sheets
        ws = wb[sheet_name]
        tmp_csv = xlsx_path.parent / f"{xlsx_path.stem}_{sheet_name}.tmp.csv"
        with open(tmp_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            for row in ws.iter_rows(values_only=True):
                writer.writerow([str(c) if c is not None else "" for c in row])
        ingest_csv(tmp_csv)
        tmp_csv.unlink()


def scan_dir(dir_path: Path):
    if not dir_path.exists():
        print(f"❌ {dir_path} 不存在")
        return
    ensure_dirs()
    count = 0
    for f in dir_path.rglob("*"):
        if f.suffix.lower() == ".csv":
            ingest_csv(f)
            count += 1
        elif f.suffix.lower() in (".xlsx", ".xls"):
            ingest_xlsx(f)
            count += 1
    print(f"\n✓ 总共处理 {count} 个文件")


def review():
    ensure_dirs()
    print(f"\n📚 Research Vault 中的 BMI 数据\n")
    total = 0
    for d in (DATA_SOURCES, MACRO, KNOWLEDGE):
        files = [f for f in d.rglob("*.md") if "bmi" in f.name.lower()]
        if files:
            print(f"  {d.relative_to(VAULT)}:")
            for f in files:
                size_kb = f.stat().st_size / 1024
                print(f"    {f.name}  ({size_kb:.1f}KB)")
                total += 1
    print(f"\n  总计: {total} 个 BMI 文件")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("csv"); c.add_argument("--input", required=True); c.add_argument("--country")
    x = sub.add_parser("xlsx"); x.add_argument("--input", required=True)
    s = sub.add_parser("scan-dir"); s.add_argument("path")
    sub.add_parser("review")

    args = p.parse_args()
    if args.cmd == "csv":
        ingest_csv(Path(args.input).expanduser(), args.country)
    elif args.cmd == "xlsx":
        ingest_xlsx(Path(args.input).expanduser())
    elif args.cmd == "scan-dir":
        scan_dir(Path(args.path).expanduser())
    elif args.cmd == "review":
        review()


if __name__ == "__main__":
    main()
