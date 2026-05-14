"""
Strategy Auditor — 第一性原理交易决策审计系统
================================================
在每次 dryRun 后、实盘前运行。

不是检查表。是对抗性审讯：
  1. 把你的论点拆解成最小原子假设
  2. 对每个假设找最强反驳
  3. 量化推理惯性风险（anchoring / narrative / availability）
  4. 从 research vault 交叉验证
  5. 给出校准后的概率区间，而不是点估计
  6. 最终：GO / REDUCE / NO-GO + 置信度评分

比 Wolf Hour 文章更细腻的地方：
  - 强迫分离「我知道的」vs「我假设的」vs「市场知道的」
  - 计算 adverse selection 成本占价差的比例
  - 检测「叙事谬误」：你的论点是否因为连贯而显得正确？
  - 对抗性定价：如果你是做市商，你会把价格定在哪里？

用法:
  python strategy_auditor.py audit \
    --market-id R96CQ5gEcn \
    --outcome NO \
    --my-prob 0.40 \
    --market-prob 0.11 \
    --edge-source "polls_52_47_within_margin" \
    --amount 50

  python strategy_auditor.py review   # 查看历史审计记录
"""

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import urllib.request

VAULT_PATH = Path("/Users/0xvox/Documents/Evensong/research-vault/knowledge")
DB_PATH = Path(__file__).parent / "audit_log.db"
MANIFOLD_API = "https://api.manifold.markets/v0"


# ─── 数据结构 ──────────────────────────────────────────────────────────────

@dataclass
class Assumption:
    claim: str            # "Virginia 民调准确反映选民意图"
    source: str           # "Ballotpedia poll, N=800"
    falsifiable: bool     # 是否可证伪
    confidence: float     # 0-1
    kill_switch: str      # "什么情况下这个假设崩溃？"

@dataclass
class BiasCheck:
    name: str
    triggered: bool
    evidence: str
    severity: str         # low / medium / high
    mitigation: str

@dataclass
class AuditReport:
    market_id: str
    market_question: str
    outcome: str
    my_prob: float
    market_prob: float
    my_implied_edge: float     # my_prob - market_prob (if NO: 1-market_prob)
    amount: float
    timestamp: str

    assumptions: list = field(default_factory=list)
    bias_checks: list = field(default_factory=list)
    counter_arguments: list = field(default_factory=list)
    vault_references: list = field(default_factory=list)

    adversarial_market_price: float = 0.0  # "如果你是做市商，你定多少？"
    calibrated_prob_low: float = 0.0
    calibrated_prob_high: float = 0.0
    adverse_selection_pct: float = 0.0    # AS 成本占价差的估计比例

    reasoning_inertia_score: float = 0.0  # 0=无惯性, 1=全靠直觉
    verdict: str = "PENDING"              # GO / REDUCE / NO-GO
    verdict_confidence: float = 0.0
    reduction_factor: float = 1.0         # 如果 REDUCE: 建议投多少比例
    notes: str = ""


# ─── 数据库 ────────────────────────────────────────────────────────────────

def init_db(db: sqlite3.Connection):
    db.execute("""
        CREATE TABLE IF NOT EXISTS audits (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id  TEXT NOT NULL,
            outcome    TEXT NOT NULL,
            verdict    TEXT NOT NULL,
            score      REAL NOT NULL,
            amount     REAL NOT NULL,
            my_prob    REAL NOT NULL,
            mkt_prob   REAL NOT NULL,
            timestamp  TEXT NOT NULL,
            report_json TEXT NOT NULL
        )
    """)
    db.commit()

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    init_db(db)
    return db


# ─── Manifold API ──────────────────────────────────────────────────────────

def get_market(market_id: str) -> dict:
    req = urllib.request.Request(
        f"{MANIFOLD_API}/market/{market_id}",
        headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# ─── Vault 交叉验证 ────────────────────────────────────────────────────────

def search_vault(keywords: list[str]) -> list[dict]:
    """
    在 research vault 中搜索相关知识文件（权重化）：
    - finance/ 目录下文件 3x 权重（交易决策）
    - frontmatter (id/tags/title) 匹配 5x 权重
    - 忽略 ai-agents/llm-architecture 等非交易主题（除非关键词特别相关）
    - 过滤掉 score < 4 的噪声结果
    """
    hits = []
    if not VAULT_PATH.exists():
        return hits

    finance_keywords = {"manifold", "polymarket", "kelly", "microstructure", "commodity",
                        "oil", "gold", "wti", "brent", "prediction", "market", "bet",
                        "edge", "liquidity", "base_rate", "referendum", "election",
                        "earnings", "stock", "price", "fed", "bmi", "imf", "macro",
                        "fair_value", "volatility", "hedge", "arbitrage"}
    trading_path_prefixes = ("finance/", "macro/", "data-sources/")
    noisy_path_prefixes = ("ai-agents/", "llm-architecture/", "software-engineering/")

    kws_lower = [k.lower() for k in keywords]

    for md_file in VAULT_PATH.rglob("*.md"):
        rel_path = str(md_file.relative_to(VAULT_PATH))
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        content_lower = content.lower()

        # 分离 frontmatter (--- ... ---)
        frontmatter = ""
        body = content_lower
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1].lower()
                body = parts[2].lower()

        # 三级评分
        score = 0
        for kw in kws_lower:
            if kw in frontmatter:   # id/tags/title 精确匹配: 5 分
                score += 5
            elif kw in body:         # body 文本匹配: 1 分
                score += 1

        # 路径加权
        is_finance = any(rel_path.startswith(p) for p in trading_path_prefixes)
        is_noise = any(rel_path.startswith(p) for p in noisy_path_prefixes)

        if is_finance:
            score *= 3
        elif is_noise:
            # 非交易主题：必须有 finance 关键词强匹配才纳入
            if not any(fk in frontmatter or fk in body[:500] for fk in finance_keywords):
                continue
            score = max(1, score // 2)

        if score >= 4:
            hits.append({
                "file": rel_path,
                "score": score,
                "preview": frontmatter[:120].replace("\n", " ") if frontmatter else body[:120].replace("\n", " "),
            })

    return sorted(hits, key=lambda x: x["score"], reverse=True)[:5]


# ─── 偏差检测器 ────────────────────────────────────────────────────────────

def run_bias_checks(
    my_prob: float,
    market_prob: float,
    edge_source: str,
    amount: float,
    outcome: str,
) -> list[BiasCheck]:
    checks = []

    # 1. 锚定偏差：是否因为先看到市场价格才形成自己的判断？
    checks.append(BiasCheck(
        name="Anchoring",
        triggered=(abs(my_prob - market_prob) < 0.05),
        evidence=f"my_prob={my_prob:.1%} vs market={market_prob:.1%}，差距{'小' if abs(my_prob-market_prob)<0.05 else '大'}",
        severity="high" if abs(my_prob - market_prob) < 0.05 else "low",
        mitigation="在看市场价格前先独立估计概率，记录在 vault 中"
    ))

    # 2. 可用性偏差：信息来源是否太单一？
    source_count = len(edge_source.split("+"))
    checks.append(BiasCheck(
        name="Availability Bias",
        triggered=(source_count == 1),
        evidence=f"信息来源数量: {source_count} | 来源: {edge_source}",
        severity="medium" if source_count == 1 else "low",
        mitigation="至少使用 3 个独立信息源：民调 + 赔率历史 + 区域投票记录"
    ))

    # 3. 叙事谬误：论点是否因为"连贯"而显得正确？
    # 代理指标：如果 edge_source 包含多个词但都指向同一结论
    narrative_keywords = ["therefore", "because", "since", "thus", "clearly", "obviously"]
    narrative_risk = any(kw in edge_source.lower() for kw in narrative_keywords)
    checks.append(BiasCheck(
        name="Narrative Fallacy",
        triggered=narrative_risk,
        evidence="边际来源描述包含因果连接词，注意叙事连贯 ≠ 正确",
        severity="medium" if narrative_risk else "low",
        mitigation="列出你的假设可能错误的 3 个具体场景"
    ))

    # 4. 过度自信：我的概率区间是否够宽？
    # 如果只给单点估计，自动触发
    checks.append(BiasCheck(
        name="Overconfidence",
        triggered=True,  # 总是需要提醒
        evidence="单点概率估计没有置信区间",
        severity="medium",
        mitigation="为 my_prob 设定 90% 置信区间，而不是单点值"
    ))

    # 5. 基率忽视：是否参考了历史相似事件的基率？
    base_rate_terms = ["historically", "base rate", "past", "similar", "historical"]
    has_base_rate = any(t in edge_source.lower() for t in base_rate_terms)
    checks.append(BiasCheck(
        name="Base Rate Neglect",
        triggered=not has_base_rate,
        evidence=f"edge_source 中{'未' if not has_base_rate else '已'}提及历史基率",
        severity="high" if not has_base_rate else "low",
        mitigation="查询同类型公投/选举的历史基率：预测市场 vs 实际结果误差分布"
    ))

    # 6. 对抗性思维缺失：是否考虑过为什么市场价格不同？
    checks.append(BiasCheck(
        name="Market Respect Deficit",
        triggered=(abs(my_prob - market_prob) > 0.30),
        evidence=f"我的概率 vs 市场概率差距 {abs(my_prob-market_prob):.1%}——差距越大，越需要解释市场为何错",
        severity="high" if abs(my_prob - market_prob) > 0.40 else "medium",
        mitigation="必须明确回答：市场中持对立观点的人知道什么你不知道的？"
    ))

    # 7. 仓位规模惯性：是否因为之前赢了就下更大？或因为帐户小就全押？
    checks.append(BiasCheck(
        name="Position Sizing Inertia",
        triggered=(amount >= 50),  # 全押所有余额
        evidence=f"下注 M${amount}（全部余额），建议使用 Kelly 公式控制仓位",
        severity="medium",
        mitigation="使用 Quarter-Kelly 或保守比例，而不是全押"
    ))

    return checks


# ─── 对抗性定价 ────────────────────────────────────────────────────────────

def adversarial_pricing(
    my_prob: float,
    market_prob: float,
    outcome: str,
    market_data: dict,
) -> float:
    """
    如果你是做市商，你会把价格定在哪里？

    做市商的定价逻辑：
      price = E[true_value | order_flow] + adverse_selection_premium

    在预测市场中，做市商不知道你是否是知情交易者。
    如果你下 NO，做市商观察到 NO 压力，会更新 YES 概率估计。

    对抗性定价 = 市场当前价格 + 信息修正 + 流动性溢价
    """
    liquidity = market_data.get("totalLiquidity", 100)

    # 流动性修正：流动性越低，做市商定价折扣越大
    liquidity_factor = min(1.0, liquidity / 5000)

    # 做市商的对立估计：假设他们也看到了同样的民调
    # 但他们更相信市场，所以给 market_prob 更大权重
    adversarial = 0.4 * my_prob + 0.6 * market_prob

    # 流动性调整：低流动性时做市商风险溢价更高，对我们更不利
    adversarial += (1 - liquidity_factor) * 0.05

    return min(max(adversarial, 0.01), 0.99)


# ─── 推理惯性评分 ──────────────────────────────────────────────────────────

def calc_inertia_score(bias_checks: list[BiasCheck]) -> float:
    """
    0 = 完全理性，无推理惯性
    1 = 完全依赖直觉，高惯性风险

    基于触发的偏差数量和严重程度加权。
    自学习：从 decision_scoring 读取历史偏差权重（如果 DB 存在），
    历史上导致亏损的偏差权重 > 1，被听取更重。
    """
    weights = {"high": 0.25, "medium": 0.15, "low": 0.05}
    learned = _load_bias_weights()
    total = 0.0
    for b in bias_checks:
        if not b.triggered:
            continue
        base = weights.get(b.severity, 0)
        mult = learned.get(b.name, 1.0)   # 历史权重，默认 1.0
        total += base * mult
    return min(1.0, total)


def _load_bias_weights() -> dict:
    """从 audit_log.db 读 bias_weights 表（decision_scoring 维护）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name, weight FROM bias_weights")
        rows = dict(c.fetchall())
        conn.close()
        return rows
    except Exception:
        return {}


# ─── 校准概率区间 ──────────────────────────────────────────────────────────

def calibrate_prob(my_prob: float, bias_checks: list[BiasCheck]) -> tuple[float, float]:
    """
    把单点概率估计扩展为 90% 置信区间。

    惯性越高 → 区间越宽（因为你对自己的判断应该越不确定）。
    """
    triggered_high = sum(1 for b in bias_checks if b.triggered and b.severity == "high")

    # 基础不确定性：±10-15%
    base_uncertainty = 0.12 + triggered_high * 0.05

    low  = max(0.01, my_prob - base_uncertainty)
    high = min(0.99, my_prob + base_uncertainty)
    return low, high


# ─── 最终裁决 ──────────────────────────────────────────────────────────────

def make_verdict(
    my_prob: float,
    market_prob: float,
    outcome: str,
    calibrated_low: float,
    calibrated_high: float,
    inertia_score: float,
    adversarial_price: float,
) -> tuple[str, float, float]:
    """
    GO / REDUCE / NO-GO

    Returns: (verdict, confidence, reduction_factor)
    """
    # 在校准区间的最保守端，EV 是否仍然为正？
    conservative_prob = calibrated_low if outcome == "YES" else (1 - calibrated_high)

    # 对抗性价格是否仍然支持边际？
    adversarial_edge = conservative_prob - adversarial_price if outcome == "YES" \
        else (1 - adversarial_price) - conservative_prob

    # EV 在最保守估计下
    net_odds = (1 / market_prob) - 1 if outcome == "NO" else (1 / market_prob) - 1
    conservative_ev = conservative_prob * net_odds - (1 - conservative_prob)

    # 裁决逻辑
    if adversarial_edge < 0:
        verdict = "NO-GO"
        confidence = 0.85
        reduction = 0.0
    elif inertia_score > 0.6:
        verdict = "REDUCE"
        confidence = 0.70
        reduction = 0.3  # 建议只下 30%
    elif conservative_ev < 0.05:
        verdict = "REDUCE"
        confidence = 0.60
        reduction = 0.5
    else:
        # GO 但根据惯性评分缩减
        verdict = "GO"
        confidence = max(0.50, 1.0 - inertia_score)
        reduction = max(0.5, 1.0 - inertia_score * 0.5)

    return verdict, confidence, reduction


# ─── 主审计流程 ────────────────────────────────────────────────────────────

def run_audit(
    market_id: str,
    outcome: str,
    my_prob: float,
    market_prob: float,
    edge_source: str,
    amount: float,
    assumptions_raw: Optional[list] = None,
    counter_args_raw: Optional[list] = None,
) -> AuditReport:

    # 获取市场数据
    try:
        market_data = get_market(market_id)
        market_question = market_data.get("question", market_id)
    except Exception:
        market_data = {}
        market_question = market_id

    implied_edge = my_prob - market_prob if outcome == "YES" else (1 - market_prob) - my_prob

    report = AuditReport(
        market_id=market_id,
        market_question=market_question,
        outcome=outcome,
        my_prob=my_prob,
        market_prob=market_prob,
        my_implied_edge=implied_edge,
        amount=amount,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # 1. 偏差检测
    report.bias_checks = [asdict(b) for b in run_bias_checks(
        my_prob, market_prob, edge_source, amount, outcome
    )]

    # 2. 推理惯性评分
    bc_objs = [BiasCheck(**b) for b in report.bias_checks]
    report.reasoning_inertia_score = calc_inertia_score(bc_objs)

    # 3. 校准概率区间
    report.calibrated_prob_low, report.calibrated_prob_high = calibrate_prob(my_prob, bc_objs)

    # 4. 对抗性定价
    report.adversarial_market_price = adversarial_pricing(
        my_prob, market_prob, outcome, market_data
    )

    # 5. Vault 交叉验证
    keywords = edge_source.lower().split("_") + outcome.lower().split()
    market_words = market_question.lower().split()[:5]
    report.vault_references = search_vault(keywords + market_words)

    # 6. 预设假设（如果用户提供）
    report.assumptions = assumptions_raw or []
    report.counter_arguments = counter_args_raw or []

    # 7. Adverse Selection 估算
    # 在低流动性市场，AS 约占价差的 50-70%（来自 arXiv:2604.10005）
    liquidity = market_data.get("totalLiquidity", 100)
    as_base = 0.60 if liquidity < 1000 else 0.45 if liquidity < 5000 else 0.30
    report.adverse_selection_pct = as_base

    # 8. 最终裁决
    report.verdict, report.verdict_confidence, report.reduction_factor = make_verdict(
        my_prob, market_prob, outcome,
        report.calibrated_prob_low, report.calibrated_prob_high,
        report.reasoning_inertia_score,
        report.adversarial_market_price,
    )

    return report


# ─── 输出格式化 ────────────────────────────────────────────────────────────

def print_report(report: AuditReport):
    bc = [BiasCheck(**b) for b in report.bias_checks]
    triggered = [b for b in bc if b.triggered]
    high_risk  = [b for b in triggered if b.severity == "high"]

    VERDICT_COLOR = {
        "GO":     "✅",
        "REDUCE": "⚠️",
        "NO-GO":  "🛑",
    }

    print(f"\n{'='*72}")
    print(f"  策略审计报告 — {report.timestamp[:16]} UTC")
    print(f"{'='*72}")
    print(f"  市场: {report.market_question[:65]}")
    print(f"  方向: {report.outcome}  |  金额: M${report.amount}")
    print(f"{'─'*72}")

    print(f"\n【概率分析】")
    print(f"  我的估计:    {report.my_prob:.1%}")
    print(f"  市场价格:    {report.market_prob:.1%}  (方向隐含价格)")
    print(f"  名义边际:    {report.my_implied_edge:+.1%}")
    print(f"  校准区间:    [{report.calibrated_prob_low:.1%}, {report.calibrated_prob_high:.1%}]  (90% CI)")
    print(f"  对抗性定价:  {report.adversarial_market_price:.1%}  (如果我是做市商)")
    print(f"  逆向选择成本: 约 {report.adverse_selection_pct:.0%} × 价差")

    print(f"\n【偏差检测】({len(triggered)}/{len(bc)} 触发)")
    for b in bc:
        icon = "🔴" if b.triggered and b.severity == "high" \
               else "🟡" if b.triggered \
               else "🟢"
        print(f"  {icon} {b.name:<28} {b.evidence[:45]}")
        if b.triggered:
            print(f"       → 修正: {b.mitigation[:60]}")

    print(f"\n【推理惯性评分】{report.reasoning_inertia_score:.1%}")
    bar_len = int(report.reasoning_inertia_score * 30)
    print(f"  [{'█'*bar_len}{'░'*(30-bar_len)}]  {'高风险' if report.reasoning_inertia_score>0.6 else '中等' if report.reasoning_inertia_score>0.3 else '低风险'}")

    if report.vault_references:
        print(f"\n【Vault 交叉验证】({len(report.vault_references)} 条相关知识)")
        for ref in report.vault_references:
            print(f"  📚 {ref['file']} (score={ref['score']})")
            preview = ref.get("preview", "").strip()
            if preview:
                import re
                # 优先 description，其次 source，最后 tags
                for field in ["description", "source", "tags"]:
                    m = re.search(rf"{field}:\s*(.+?)(?:\s+[a-z_]+:|\s*\|\s*|$)", preview)
                    if m:
                        val = m.group(1).strip().rstrip("|").strip()[:90]
                        print(f"      → {field}: {val}")
                        break

    print(f"\n{'─'*72}")
    verdict_icon = VERDICT_COLOR.get(report.verdict, "❓")
    print(f"  {verdict_icon} 最终裁决: {report.verdict}  |  置信度: {report.verdict_confidence:.0%}")
    if report.verdict == "REDUCE":
        print(f"     建议仓位比例: {report.reduction_factor:.0%} × 原计划金额 = M${report.amount * report.reduction_factor:.1f}")
    if high_risk:
        print(f"\n  ⚠️  高风险偏差 ({len(high_risk)} 个):")
        for b in high_risk:
            print(f"     • {b.name}: {b.mitigation}")
    print(f"{'='*72}\n")


# ─── CLI ───────────────────────────────────────────────────────────────────

def cmd_audit(args):
    report = run_audit(
        market_id=args.market_id,
        outcome=args.outcome.upper(),
        my_prob=args.my_prob,
        market_prob=args.market_prob,
        edge_source=args.edge_source,
        amount=args.amount,
    )
    print_report(report)

    # 保存到数据库
    with get_db() as db:
        db.execute("""
            INSERT INTO audits
            (market_id, outcome, verdict, score, amount, my_prob, mkt_prob, timestamp, report_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            report.market_id, report.outcome, report.verdict,
            report.reasoning_inertia_score, report.amount,
            report.my_prob, report.market_prob, report.timestamp,
            json.dumps(asdict(report)),
        ))
        db.commit()

    print(f"审计已保存到 {DB_PATH}")


def cmd_review(args):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM audits ORDER BY timestamp DESC LIMIT 20"
        ).fetchall()

    if not rows:
        print("暂无审计记录。")
        return

    print(f"\n{'timestamp':>20} {'verdict':>8} {'score':>7} {'M$':>5}  市场")
    print("─"*80)
    for r in rows:
        icon = "✅" if r["verdict"]=="GO" else "⚠️" if r["verdict"]=="REDUCE" else "🛑"
        print(f"{r['timestamp'][:19]:>20} {icon} {r['verdict']:>6} {r['score']:>6.1%} {r['amount']:>5.0f}  {r['market_id']}")


def main():
    parser = argparse.ArgumentParser(
        description="第一性原理策略审计系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit", help="审计一个交易决策")
    p_audit.add_argument("--market-id",   required=True, help="Manifold 市场 ID")
    p_audit.add_argument("--outcome",     required=True, help="YES 或 NO")
    p_audit.add_argument("--my-prob",     type=float, required=True,
                         help="你对 outcome 的主观概率 (0-1)")
    p_audit.add_argument("--market-prob", type=float, required=True,
                         help="当前市场对 outcome 的隐含概率")
    p_audit.add_argument("--edge-source", required=True,
                         help="你的边际来源（简短描述，用下划线连接）")
    p_audit.add_argument("--amount",      type=float, default=50,
                         help="计划下注金额")

    sub.add_parser("review", help="查看历史审计记录")

    args = parser.parse_args()
    if args.cmd == "audit":
        cmd_audit(args)
    elif args.cmd == "review":
        cmd_review(args)


if __name__ == "__main__":
    main()
