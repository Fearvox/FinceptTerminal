"""
BMI Bulk Ingester — 处理 127 geos × 2397 items × 38 years 大规模导出
========================================================================
原 bmi_ingester.py 把整个 CSV 变一个 .md，对 GB 级数据会炸。
此版本按 (geography, category) 切分，每组合一个精简 .md（含 frontmatter + 关键统计）。

BMI 导出 schema（"wide" 格式，根据 UI 截图）:
  Geography | Data Item | Source | 2024 | 2025 | 2026 | 2027 | 2028 | ...

切分策略:
  - 粗粒度：按 geography 切（每国一个 .md）
  - 细粒度：按 (country, category) 切（每个 category 一个 .md）
  - 智能：只保留关键年份摘要（最近 + 未来 3 年）+ 数据链接指向原 CSV

产物位置:
  research-vault/data-sources/bmi/<country>/<category>.md
  原 CSV 保留在 /Users/0xvox/Documents/BMI-raw/<YYYY-MM-DD>/

用法:
  python bmi_bulk_ingester.py ingest --input ~/Downloads/BMI_full_export.csv
  python bmi_bulk_ingester.py ingest --input ~/Downloads/BMI_export.xlsx --keep-raw
  python bmi_bulk_ingester.py status       # 看 vault 里有哪些国家/category
"""
import argparse
import csv
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

VAULT = Path("/Users/0xvox/Documents/Evensong/research-vault/knowledge")
BMI_VAULT = VAULT / "data-sources" / "bmi"
RAW_ARCHIVE = Path("/Users/0xvox/Documents/BMI-raw")


CATEGORY_MAP = {
    # 优先识别关键词（先匹配放前面）
    ("gdp", "nominal gdp", "real gdp", "gdp growth", "gdp deflator"): "macro-gdp",
    ("inflation", "cpi", "ppi", "deflator", "price"): "macro-inflation",
    ("unemployment", "employment", "labour", "labor", "wages"): "macro-labor",
    ("exchange rate", "currency", "fx", "rer", "forex"): "macro-fx",
    ("trade balance", "imports", "exports", "current account"): "macro-trade",
    ("fiscal", "debt", "deficit", "budget", "revenue"): "macro-fiscal",
    ("population", "demographic", "age"): "macro-population",
    ("biri", "banking industry risk", "financial score", "regulatory"): "risk-banking",
    ("economic volatility", "volatility score"): "risk-volatility",
    ("government finance score", "living standard"): "risk-political",
    ("oil", "crude", "wti", "brent", "petroleum"): "commodity-oil",
    ("gas", "lng", "natural gas"): "commodity-gas",
    ("gold", "silver", "copper", "metal"): "commodity-metals",
    ("interest rate", "policy rate", "fed", "central bank"): "macro-rates",
    ("autos sales", "autos production", "auto industry", "ev", "electric vehicle"): "industry-autos",
    ("rri", "risk/reward", "risk reward index"): "industry-rri",
    ("retail", "consumer", "household"): "industry-retail",
    ("infrastructure", "construction", "housing"): "industry-construction",
    ("telecom", "broadband", "mobile", "internet"): "industry-telecom",
    ("pharma", "healthcare", "hospital"): "industry-healthcare",
    ("tourism", "hospitality", "hotel"): "industry-tourism",
    ("mining", "iron ore", "coal", "lithium"): "industry-mining",
    ("agriculture", "crop", "livestock"): "industry-agri",
}


def categorize(data_item: str) -> str:
    d = (data_item or "").lower()
    for keywords, cat in CATEGORY_MAP.items():
        if any(kw in d for kw in keywords):
            return cat
    return "misc"


def country_slug(geography: str) -> str:
    """Normalize geography 为 slug"""
    g = (geography or "").lower().strip()
    # 剥去 prefecture / state / province 后缀
    for suffix in [" (prefecture)", " (state)", " (province)", " (region)"]:
        if g.endswith(suffix):
            g = g[:-len(suffix)].strip()
    g = g.replace(" ", "-").replace(",", "").replace("(", "").replace(")", "")
    return g[:40]


def detect_year_columns(header: list) -> list:
    """找出 header 里哪些列是年份/季度/月份（1990-2035）"""
    import re
    year_cols = []
    for i, col in enumerate(header):
        s = (col or "").strip()
        # Pure year: "2026"
        if s.isdigit() and 1990 <= int(s) <= 2035:
            year_cols.append((i, int(s)))
            continue
        # Quarter: "Q1 2026", "2026 Q1", "2026Q1", "2026-Q1"
        m = re.search(r'(?:Q([1-4]))?\s*[-_]?\s*(\d{4})\s*[-_]?\s*(?:Q([1-4]))?', s)
        if m and m.group(2) and 1990 <= int(m.group(2)) <= 2035:
            q = m.group(1) or m.group(3)
            if q:
                year_cols.append((i, f"{m.group(2)}-Q{q}"))
                continue
        # Month: "Jan 2026", "2026-01", "2026M01"
        m = re.search(r'(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+)?(\d{4})(?:[-_]?M?(\d{1,2}))?', s, re.IGNORECASE)
        if m and m.group(2) and 1990 <= int(m.group(2)) <= 2035:
            mo = m.group(1) or (f"M{m.group(3)}" if m.group(3) else None)
            if mo:
                year_cols.append((i, f"{m.group(2)}-{mo}"))
                continue
    return year_cols


def ingest_csv(csv_path: Path, keep_raw: bool = False):
    BMI_VAULT.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        print(f"❌ {csv_path} 不存在")
        sys.exit(1)

    # 归档 raw CSV
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_dir = RAW_ARCHIVE / today
    raw_dir.mkdir(parents=True, exist_ok=True)
    if keep_raw:
        shutil.copy(csv_path, raw_dir / csv_path.name)
        print(f"  📦 原 CSV 归档到 {raw_dir / csv_path.name}")

    print(f"📖 读取 {csv_path.name}...")
    # 第一遍：拿 header + 统计规模
    with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            print(f"❌ 空文件")
            return

        year_cols = detect_year_columns(header)
        print(f"  Columns: {len(header)}  |  Year cols: {len(year_cols)}")

        # 找 geography / data_item / source 列索引
        def find_col(names):
            for n in names:
                for i, c in enumerate(header):
                    if c and c.lower().strip() == n.lower():
                        return i
            return -1

        geo_idx = find_col(["geography", "country", "region"])
        di_idx = find_col(["data item", "indicator", "metric"])
        src_idx = find_col(["source"])

        if geo_idx < 0 or di_idx < 0:
            print(f"❌ 找不到 geography/data_item 列")
            print(f"   Header: {header[:10]}")
            return

        # 第二遍：按 (country, category) 分组累积数据
        groups = defaultdict(list)
        row_count = 0
        for row in reader:
            if len(row) < max(geo_idx, di_idx) + 1:
                continue
            geo = row[geo_idx]
            di = row[di_idx]
            src = row[src_idx] if src_idx >= 0 and src_idx < len(row) else ""
            if not geo or not di:
                continue
            country = country_slug(geo)
            category = categorize(di)
            # 提取数据点 (year, value)
            data_points = []
            for col_idx, year_label in year_cols:
                if col_idx < len(row) and row[col_idx]:
                    val = row[col_idx].strip()
                    if val and val not in ("-", "N/A", ""):
                        data_points.append((year_label, val))
            groups[(country, category)].append({
                "geography": geo,
                "data_item": di,
                "source": src,
                "points": data_points,
            })
            row_count += 1
            if row_count % 5000 == 0:
                print(f"  ... {row_count} 行已读")

        print(f"  总 {row_count} 行，{len(groups)} 组 (country × category)")

    # 第三遍：为每组写 markdown
    # 用 suffix 防止 Annual/Quarterly/Monthly 覆盖
    freq_suffix = ""
    csv_name_lower = csv_path.name.lower()
    for freq in ("annual", "monthly", "quarterly"):
        if freq in csv_name_lower:
            freq_suffix = f"-{freq}"
            break

    written = 0
    for (country, category), records in groups.items():
        out_dir = BMI_VAULT / country
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{category}{freq_suffix}.md"
        _write_group_md(out_path, country, category, records, csv_path)
        written += 1

    print(f"\n✓ 写入 {written} 个 .md 文件到 {BMI_VAULT}")
    return written


def _write_group_md(path: Path, country: str, category: str, records: list, source_csv: Path):
    """为 (country, category) 组合写一个精简 markdown"""
    ts = datetime.now(timezone.utc).isoformat()
    fid = f"bmi-{country}-{category}-{ts[:10].replace('-','')}"

    # 提取关键年份：近 3 年 + 未来 3 年
    key_years = [2023, 2024, 2025, 2026, 2027, 2028]

    tags = ["bmi", "fitch-solutions", f"country-{country}", category]

    md_lines = [
        "---",
        f"id: {fid}",
        f"source: BMI / Fitch Solutions (bulk export)",
        f"ingested: {ts}",
        f"category: data-sources/bmi/{country}",
        f"tags: {json.dumps(tags)}",
        f"country: {country}",
        f"domain: {category}",
        f"data_items: {len(records)}",
        f"raw_csv: {source_csv.name}",
        "---",
        "",
        f"# BMI {country.upper()} — {category}",
        "",
        f"**Source**: BMI (Fitch Solutions) Bulk Export",
        f"**Indicators**: {len(records)}",
        "",
        "## Key Metrics (2024-2028 snapshot)",
        "",
        "| Indicator | 2024 | 2025 | 2026 | 2027 | 2028 | Source |",
        "|-----------|------|------|------|------|------|--------|",
    ]

    # 只显示 top 30 indicators 避免文件太大
    for r in records[:30]:
        row_vals = {}
        for year, val in r["points"]:
            if isinstance(year, int) and year in key_years:
                row_vals[year] = val
        row = f"| {r['data_item'][:45]} "
        for y in key_years[-5:]:  # 2024-2028
            row += f"| {row_vals.get(y,'-')[:15]} "
        row += f"| {r['source'][:20]} |"
        md_lines.append(row)

    if len(records) > 30:
        md_lines.append(f"\n*({len(records) - 30} more indicators in raw CSV)*\n")

    md_lines.append(f"\n## Raw Data\n\nFull time series in: `{source_csv}`\n")

    path.write_text("\n".join(md_lines), encoding="utf-8")


def status():
    if not BMI_VAULT.exists():
        print("Vault BMI dir 不存在")
        return
    countries = [d for d in BMI_VAULT.iterdir() if d.is_dir()]
    total_md = 0
    total_size = 0
    for c in countries:
        files = list(c.rglob("*.md"))
        size = sum(f.stat().st_size for f in files)
        total_md += len(files)
        total_size += size
        if len(files) > 0:
            print(f"  {c.name:20} {len(files):>3} files, {size/1024:>6.1f} KB")

    print(f"\n  总计: {len(countries)} 国家, {total_md} 个 .md, {total_size/1024/1024:.1f} MB")


def ingest_xlsx(xlsx_path: Path, keep_raw: bool = False):
    """BMI 格式: 前 2-3 行是 disclaimer/空行，header 通常在第 4 行"""
    import openpyxl
    print(f"📂 Loading {xlsx_path.name} (47MB 可能需要 30-60s)...")
    wb = openpyxl.load_workbook(xlsx_path, read_only=False)
    skip_sheets = {"Disclaimer"}

    for sheet in wb.sheetnames:
        if sheet in skip_sheets:
            continue
        ws = wb[sheet]
        print(f"\n📄 Sheet: {sheet} ({ws.max_row} rows × {ws.max_column} cols)")

        # 找 header 行（搜索 "Geography" 作为 anchor）
        header_row_idx = None
        for i, row in enumerate(ws.iter_rows(max_row=10, values_only=True), start=1):
            if row and row[0] and "geography" in str(row[0]).lower():
                header_row_idx = i
                break
        if not header_row_idx:
            print(f"  ⚠️ 找不到 Geography header，跳过")
            continue
        print(f"  Header 在第 {header_row_idx} 行")

        tmp_csv = xlsx_path.parent / f"{xlsx_path.stem}_{sheet}.tmp.csv"
        with open(tmp_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if i < header_row_idx:
                    continue
                w.writerow([str(c) if c is not None else "" for c in row])
        print(f"  写 CSV: {tmp_csv.name} ({tmp_csv.stat().st_size/1024/1024:.1f} MB)")
        ingest_csv(tmp_csv, keep_raw=keep_raw)
        if not keep_raw:
            tmp_csv.unlink()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest")
    i.add_argument("--input", required=True)
    i.add_argument("--keep-raw", action="store_true", help="保留原 CSV 到 /Users/0xvox/Documents/BMI-raw/")
    sub.add_parser("status")

    args = p.parse_args()
    if args.cmd == "ingest":
        path = Path(args.input).expanduser()
        if path.suffix.lower() in (".xlsx", ".xls"):
            ingest_xlsx(path, args.keep_raw)
        else:
            ingest_csv(path, args.keep_raw)
    elif args.cmd == "status":
        status()


if __name__ == "__main__":
    main()
