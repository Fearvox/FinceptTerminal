"""
Theory Seed — 一次性种入 25+ 奠基金融/交易理论文献到 research-vault
========================================================================
每篇文件含:
  - 原始源（arxiv/SSRN/publisher 链接）
  - 1-2 段摘要（基于公开信息）
  - 关键公式/概念
  - 与我们 Manifold/Paper 策略的连接

避免大段拷贝原文 — 每篇只摘核心概念，让 auditor vault 搜索能找到。
"""
from pathlib import Path
from datetime import datetime, timezone
import re

VAULT = Path("/Users/0xvox/Documents/Evensong/research-vault/knowledge/finance/knowledge")
TS = datetime.now(timezone.utc).isoformat()
DATE = "20260420"


# format: (filename, category_tag, frontmatter_extra, content)
# Each content is ~200-400 words covering: WHY matters + KEY concept + HOW to apply
PAPERS = [
    # ═══ 技术分析理论 / Day Trading ═══
    ("day-trading-ma-efficacy-brock-1992.md", ["day-trading","technical-analysis","moving-average"],
     {"author": "Brock, Lakonishok, LeBaron", "year": "1992", "source": "https://www.jstor.org/stable/2329112"},
     """# Simple Technical Trading Rules and the Stochastic Properties of Stock Returns (Brock 1992)

**Why matters**: 第一篇用严格统计检验证明 **moving average 交叉规则** 在 Dow 指数 1897-1986 有显著超额收益的论文。后续 Day trading 技术分析的学术起点。

## 核心发现
- 测试 26 个 MA 规则 (1-MA, 2-MA, band 等)
- Variable Length MA (VLMA): 当短 MA 上穿长 MA 且超过 1% band → 多头
- 买入日的平均日回报 0.0047% vs 卖出日 -0.0013%
- **t 值 > 2** 对多数规则成立
- Bootstrap 检验表明不是随机游走能解释的

## 关键公式
```
Buy signal:  MA_short(t) > MA_long(t) × (1 + band)
Sell signal: MA_short(t) < MA_long(t) × (1 - band)
Return diff: (E[r | buy] - E[r | sell]) / σ
```

## 我们策略应用
- **Precision Sniper EMA stack** 是 Brock 规则的 3-层变种
- 验证了 EMA crossover 不是纯噪音（至少在长周期上）
- 注意: Post-1987 效应减弱（Timmermann 2004 的修正）
- **隐含限制**: 高频市场 (1m SP500) 此效应几乎被 arb 消除
"""),

    ("day-trading-momentum-jegadeesh-1993.md", ["day-trading","momentum","factor"],
     {"author": "Jegadeesh & Titman", "year": "1993", "source": "https://www.jstor.org/stable/2328882"},
     """# Returns to Buying Winners and Selling Losers (Jegadeesh & Titman 1993)

**Why matters**: 定义了**动量因子**——过去 3-12 月涨幅最大的股票未来 3-12 月继续跑赢。Fama-French 3 因子后的第 4 大 anomaly。

## 核心发现
- 买过去 6 月 winner 组合、卖 loser 组合：持有 6 月平均超额月收益 1%
- 效应在 Krugman (1998) 后仍持续
- Jegadeesh-Titman (2001) 重复验证到 1998 年

## 关键公式
```
Momentum_t = Price_t / Price_{t-12} - 1   (过去 12 月动量)
分组: 最强 Q5 多头，最弱 Q1 空头
```

## 我们策略应用
- **多时间周期 EMA** 捕捉的就是动量
- 短期（1m-1h）动量 ≠ 长期（3-12m）动量的 anomaly
- 1m 图上 "momentum" 主要是 mean-reversion 噪音
- 注意: 12 月后出现**反转**（De Bondt & Thaler 1985）
"""),

    ("day-trading-bollinger-bands-theory.md", ["day-trading","volatility","bollinger"],
     {"author": "Bollinger", "year": "1983/2001", "source": "https://www.bollingerbands.com/bollinger-on-bollinger-bands"},
     """# Bollinger Bands 理论基础

**Why matters**: 基于标准差的自适应通道，90%+ 技术派指标的基石。

## 核心公式
```
Middle Band = SMA(close, 20)
Upper Band  = Middle + 2 × σ_20
Lower Band  = Middle - 2 × σ_20

%B = (price - lower) / (upper - lower)
BandWidth = (upper - lower) / middle
```

## 经验规则
- 92% 的数据在 Upper 和 Lower Band 之间（正态假设）
- 实际分布 **fat tail** → 经常突破，2σ 不是 reliable 边界
- **挤压（Squeeze）** = BandWidth 创新低 → 即将爆发
- W-底 / M-顶 形态 = 双底/双顶在布林带上的表现

## 批评
- Bollinger 本人 (2001): bands 不是**反转**信号，是**能量**信号
- 正态假设错误 —— 金融收益厚尾，Lopez de Prado 2018 广泛批判
- 适合**均值回归市场**，在趋势中误导

## 我们策略应用
- SATS 的"adaptive bands" 修正了 BB 的固定 2σ 问题
- LuxAlgo SMC 也隐含用到 band 思想（premium/discount zones）
- Squeeze Momentum (LazyBear) = BB + Keltner 比较 → 判断挤压爆发方向
"""),

    ("day-trading-rsi-wilder-1978.md", ["day-trading","momentum","oscillator","rsi"],
     {"author": "Wilder", "year": "1978", "source": "https://www.amazon.com/New-Concepts-Technical-Trading-Systems/dp/0894590278"},
     """# Relative Strength Index (Wilder 1978) 理论

**Why matters**: 定义"超买/超卖"概念的原始指标。所有 oscillator 的祖先。

## 核心公式
```
RS = Avg Gain(14) / Avg Loss(14)
RSI = 100 - 100/(1 + RS)

超买: RSI > 70   →  historical P(下跌 next N days) 增加
超卖: RSI < 30   →  historical P(反弹) 增加
```

## 实证表现
- Aronson (2006) 严格测试: 5-period RSI 在 S&P 1986-2003 **有显著统计 edge**（罕见）
- Larry Connors 的 "RSI(2)" 策略: 短周期 RSI 超卖反弹
- 大周期 (daily RSI 14) 效应已被套利消除

## 与我们策略
- **Precision Sniper** 内部用 RSI 作为 confluence factor
- 作者要求 RSI 处在 "非极端区" 才确认 EMA 交叉 → 避免顶底位追涨
- 注意: 在 1m 图上 RSI 噪音极大，不应单独依赖

## Divergence 概念
- Price 创新高但 RSI 没创 → 动量减弱（bearish divergence）
- 作为 SMC 的补充判断（非结构性但统计上偶有效）
"""),

    ("day-trading-macd-appel-1979.md", ["day-trading","momentum","macd"],
     {"author": "Appel", "year": "1979", "source": "https://www.amazon.com/Technical-Analysis-Power-Tools-Investors/dp/0137353300"},
     """# MACD (Moving Average Convergence-Divergence) 理论

**Why matters**: 最常用的趋势确认指标之一。Precision Sniper 直接内置。

## 核心公式
```
MACD_line = EMA(close, 12) - EMA(close, 26)
Signal    = EMA(MACD_line, 9)
Histogram = MACD - Signal

信号:
  MACD > Signal (golden cross)  → 多头
  MACD < Signal (death cross)   → 空头
  Histogram > 0 且扩大          → 加速
```

## 统计表现
- Chong & Ng (2008) 在 30 国指数测试: MACD 在**新兴市场**统计显著
- 发达市场（S&P/DAX）效应边缘化
- **最佳周期**: 日线 > 1h > 1m（效率市场悖论）

## 我们策略
- Precision Sniper 10-factor 打分系统中 MACD histogram + signal 值 **2.0 分**（最高权重）
- 作为**早期动量**信号：histogram 变号比 price 交叉 EMA 更早
- **危险**: MACD 基于 EMA 差值，在 **choppy 市场**频繁假信号
"""),

    # ═══ Smart Money Concepts / ICT 理论 ═══
    ("day-trading-smc-order-blocks-theory.md", ["day-trading","smc","order-blocks","ict"],
     {"author": "Inner Circle Trader (ICT) / Michael Huddleston", "year": "2016+",
      "source": "https://www.babypips.com/learn/forex/order-blocks"},
     """# Smart Money Concepts: Order Blocks / FVG / Liquidity

**Why matters**: LuxAlgo SMC 指标背后的理论。**批评先行**：无严格学术支持，但广泛被 retail 使用 → 某种 self-fulfilling pattern。

## 核心概念
### Order Block
- 机构大单留下的最后一根**反向** K 线
- Bullish OB: 大涨前最后一根**下跌** K 线（机构在低价建仓）
- Bearish OB: 大跌前最后一根上涨 K 线

### Fair Value Gap (FVG)
- 三根连续 K 线中，中间 K 线的 wick 未与第一/三 K 线重叠
- Imbalance 区域 → 市场倾向回填

### Liquidity Sweep
- 突破前高/低后快速回落 = 获取 stop-loss 流动性后反转
- "Buy-side liquidity" 就是多头 stop 堆积区

## 学术视角
- **Smith, Farmer, Gillemot (2003)**: 确认 LOB 中机构 order 的**market impact** 符合 Kyle λ 线性模型
- FVG 概念无独立实证文献，但与 **inventory imbalance** (O'Hara 1997) 有弱联系
- ICT 派的 "killzone" 概念 = **session volatility clustering**（Andersen & Bollerslev 1997 已证实）

## 我们策略
- **LuxAlgo SMC** 提供 OB/FVG 可视化 — 作为**位置**信号（不是方向）
- 与 Precision Sniper 的方向信号配合 → OB 位置 + 多头 stack = 入场
- **警告**: 不要单靠 OB 作为反转信号，必须 confluence
"""),

    ("day-trading-ict-killzones-sessions.md", ["day-trading","session-volatility","ict","killzones"],
     {"author": "Andersen & Bollerslev (session volatility) + ICT",
      "year": "1997 / 2016+", "source": "https://www.jstor.org/stable/2527343"},
     """# Session Volatility Clustering & ICT Killzones

**Why matters**: "Killzone" 概念的学术基础 = **intraday volatility clustering**。TFO 的 ICT Killzones 指标直接可视化这个。

## 核心论文: Andersen-Bollerslev (1997)
- 用 1-min S&P500 futures 1992 年数据
- 日内 **volatility U-shape**: 开盘 + 收盘高，中间低
- 伦敦/纽约重叠 (13:00-16:00 GMT) 波动最强
- 亚洲 session 相对平静

## ICT 的 Killzones 时区定义
```
Asian Killzone:    00:00 - 05:00 UTC    (低波动、积累)
London Open:       07:00 - 10:00 UTC    (第一波方向性)
NY Killzone:       13:30 - 16:30 UTC    (主力时段)
London Close:      15:00 - 16:00 UTC    (一天高波动收尾)
```

## Wolf Hour 对应
我们系统的 02:30-04:00 UTC Wolf Hour 恰好在 Asian session 结束、London 未开之前的**真空区**。符合 Andersen-Bollerslev 的低波动预测 → 做市商退场的机制性解释。

## 我们策略
- **ICT Killzones + Pivots (TFO)** 指标作为 session 可视化
- Wolf Hour 是 Asian post-tail + 主要市场未到 = 流动性底谷
- 策略对接: **killzone 外的 low-vol 时段 = structural mispricing 机会**
"""),

    # ═══ Value / Long-term Investment ═══
    ("longterm-graham-intrinsic-value.md", ["long-investment","value-investing","dcf"],
     {"author": "Benjamin Graham", "year": "1949", "source": "https://www.amazon.com/Intelligent-Investor-Benjamin-Graham/dp/0060555661"},
     """# The Intelligent Investor: Margin of Safety & Intrinsic Value (Graham 1949)

**Why matters**: 价值投资的奠基。Buffett 所有策略源头。

## 核心概念

### Intrinsic Value (本质价值)
```
IV = Σ (FCF_t / (1+r)^t) 未来所有自由现金流贴现
    或
IV ≈ EPS × (8.5 + 2g)     Graham 的简化公式 (g = 增长率)
```

### Margin of Safety
```
买入仅当: price ≤ IV × 0.67   (至少 33% 安全边际)
```

### Mr. Market 比喻
- 每天报价的情绪化伙伴
- 价值投资者的工作是**利用**他的情绪，不是被影响

## 实证证据
- Fama-French (1992): Value 因子 (HML) 1927-1990 年化超额收益 5%+
- 2010s 以后 Value 表现大幅落后（"Value trap"）
- 2020 年 Asness 等研究发现 Value 仍有 edge 但需更严格定义

## 我们策略
- Graham 是**长期投资**基石，不直接适用 Manifold 预测市场
- 但"安全边际"思维应用到我们仓位: **押注 edge > 5pp 再入场**
- Fama-French Value (HML) 因子 = 我们 BMI GDP/收益预测的定量延伸
"""),

    ("longterm-fama-french-3factor.md", ["long-investment","factor-investing","fama-french"],
     {"author": "Fama & French", "year": "1993", "source": "https://www.sciencedirect.com/science/article/pii/0304405X93900235"},
     """# Common Risk Factors in Stock and Bond Returns (Fama-French 1993)

**Why matters**: 定义了 SMB (Size) 和 HML (Value) 两大因子，颠覆 CAPM 单因子模型。Quant 因子投资的起点。

## 核心模型
```
R_i - R_f = α + β_MKT(R_m - R_f) + β_SMB × SMB + β_HML × HML + ε

SMB = Small Cap 组合收益 - Large Cap 组合收益   (size premium)
HML = High B/M 组合收益 - Low B/M 组合收益       (value premium)
```

## 实证
- 1963-1991: SMB 年化 3.4%, HML 年化 5.1%
- 3 因子解释 R² > 0.90（vs CAPM ~0.70）
- Carhart (1997) 加上动量因子 UMD → 4 因子
- Fama-French 5 因子 (2015): + Profitability (RMW) + Investment (CMA)

## 我们策略
- 因子投资 ≠ Manifold 短期策略
- 但 **HML** 思维: 我们识别"便宜"（低价合约）+ 数据支撑 = 长期 edge
- **因子崩溃警示**: Value 2010s 失效 → 任何"永恒" edge 都需 regime 识别
- **BMI 数据与因子关联**: country 风险因子 = 国家版 SMB/HML
"""),

    ("longterm-markowitz-portfolio.md", ["long-investment","portfolio","mpt"],
     {"author": "Markowitz", "year": "1952", "source": "https://www.jstor.org/stable/2975974"},
     """# Portfolio Selection (Markowitz 1952)

**Why matters**: Nobel Prize 1990. Modern Portfolio Theory 起点。**分散化**的数学基础。

## 核心公式
### 组合期望收益 & 方差
```
E[R_p] = Σ w_i × E[R_i]
Var(R_p) = ΣΣ w_i × w_j × σ_ij   (含协方差项)
```

### 有效前沿 (Efficient Frontier)
给定期望收益，最小化方差；或给定方差，最大化期望收益。
```
minimize  w^T Σ w
s.t.      w^T μ = target_return
          Σ w_i = 1
```

### Sharpe Ratio (Sharpe 1966)
```
SR = (E[R_p] - R_f) / σ_p
```

## 批评
- 假设**收益服从正态分布** — 金融厚尾破坏
- 协方差矩阵估计不稳定 (Michaud 1989)
- 对输入极端敏感（garbage in → amplified garbage out）

## 我们策略
- Manifold 仓位间相关性高（5 个 WTI 仓位 = 1 个）→ **违反 MPT 分散化**
- 应该对冲: **非相关仓位**（政治 + 商品 + 纯噪音）
- Virginia (政治) + Oil (商品) + Inflation (宏观) = 3 个维度，更接近 MPT
- 记录 correlation 矩阵每月更新
"""),

    ("longterm-shiller-cape.md", ["long-investment","valuation","cape","shiller"],
     {"author": "Shiller", "year": "1988/2000", "source": "https://www.jstor.org/stable/1058112"},
     """# CAPE Ratio & Market Valuation (Shiller 1988, 2000)

**Why matters**: Shiller Nobel 2013. CAPE (P/E10) 是**长期市场估值**最引用的指标。

## 核心公式
```
CAPE = Price / (10-year average earnings, inflation-adjusted)
```

## 历史关键值
- CAPE 均值 (1880-2024): ~17
- 1929 峰值: 33  → 大崩盘
- 2000 峰值: 44  → dotcom 崩盘
- 2021 峰值: 38  → 2022 调整
- **2026-04 当前**: ~34 (高估区间但未崩盘)

## 预测力
- Campbell-Shiller (1988): CAPE 可预测**10 年**远期收益 (R² > 0.4)
- 1 年预测力极弱（市场可以长期高估）

## 我们策略
- Polymarket 的 "S&P 2026 closes higher" 类市场 → CAPE 可作 long-horizon 锚
- **SP500 7096 + CAPE 34** 暗示均值回归 → 未来 10 年年化 3-5%
- BMI 的 macro-gdp 数据交叉 CAPE = 美股长期定位
- 短期无预测力，不适合 Manifold 周度/月度 bet
"""),

    # ═══ Behavioral Finance ═══
    ("behavioral-kahneman-prospect-theory.md", ["behavioral","prospect-theory","psychology"],
     {"author": "Kahneman & Tversky", "year": "1979", "source": "https://www.jstor.org/stable/1914185"},
     """# Prospect Theory (Kahneman & Tversky 1979)

**Why matters**: Nobel Prize 2002. 推翻 Expected Utility Theory。解释为什么**交易者不理性**。

## 核心发现
### Value Function v(x)
```
v(x) = x^α    if x ≥ 0  (收益域凹)
v(x) = -λ|x|^β if x < 0 (损失域凸)

α ≈ β ≈ 0.88
λ ≈ 2.25      → 损失权重是收益的 2.25 倍
```

### Probability Weighting π(p)
- 小概率 overweight → 彩票效应
- 中等概率 underweight → 保守

## 我们策略应用（CRITICAL）
- **Loss aversion (λ=2.25)** 解释为什么我们不想砍 Virginia 亏损仓：**感觉上**亏 M$10 比赚 M$10 痛 2 倍
- **但数学上** hold vs sell EV 差 M$10 → 我们应该 HOLD
- **Probability weighting**: Polymarket "Oil $150 NO" 真实 P(YES)=30%, 市场给 35% → 散户**高估极端事件**的系统偏差
- **Inflation >10% 在 2026** 市场 4.5% YES vs 真实 <1% → 散户彩票效应

## Auditor 的 Loss Aversion Bias 检查
- 已有"Position Sizing Inertia" 检查
- 应该加**"Loss Aversion Bias"** 专门检测 hold-vs-sell 决策中的不理性
"""),

    ("behavioral-de-bondt-overreaction.md", ["behavioral","overreaction","reversal"],
     {"author": "De Bondt & Thaler", "year": "1985", "source": "https://www.jstor.org/stable/2327804"},
     """# Does the Stock Market Overreact? (De Bondt & Thaler 1985)

**Why matters**: 证明长期 **反转**（mean reversion），与 Jegadeesh 短期动量互补。行为金融学兴起。

## 核心发现
- 3 年 loser 组合 vs winner 组合：接下来 3 年 loser 超出 25%
- 解释: 市场**过度反应**于坏消息，均值回归
- 对 Efficient Market Hypothesis 的直接挑战

## 我们策略
- **Virginia post-mortem** 是经典 overreaction? 市场 11% → 89% 3 天内飙 78pp，可能 overshoot
- 但我们的教训是: 面对巨量资金流向（whale M$24k），**不要对抗 short-term momentum**
- De Bondt-Thaler 反转是 3+ 年现象，**不能**用于周度/月度 bet
- 但 Polymarket "Virginia 后续议案" 类市场可能呈现 overreaction pattern
"""),

    # ═══ 预测市场 / Wisdom of Crowds ═══
    ("prediction-market-hanson-lmsr.md", ["prediction-markets","lmsr","hanson","amm"],
     {"author": "Robin Hanson", "year": "2003", "source": "http://mason.gmu.edu/~rhanson/mktscore.pdf"},
     """# Logarithmic Market Scoring Rule (Hanson 2003)

**Why matters**: LMSR 是大多数预测市场（含早期 Polymarket、Augur）的定价机制。Manifold 用修改版 CPMM。

## 核心公式
### Cost Function
```
C(q_1, q_2) = b × log(e^(q_1/b) + e^(q_2/b))
```
其中 q_i 是每个 outcome 的累积股数，b 是流动性参数。

### Price
```
p_1 = e^(q_1/b) / (e^(q_1/b) + e^(q_2/b))
```

### Marginal Cost for N shares
```
ΔC = C(q_1 + N, q_2) - C(q_1, q_2)
```

## 性质
- **Bounded loss**: AMM 最大亏损 = b × log(N)，流动性参数控制风险
- **Path independence**: 最终价格只取决于 net position
- **Proper scoring**: 诚实披露信念是最优策略

## 我们策略
- Manifold 用 **CPMM (constant product)** 不是 LMSR
  - Manifold: x × y = k，适合流动性 bootstrap
  - LMSR: exponential cost function，b 参数手动设
- Polymarket 现在用 **CLOB (order book)** + AMM 混合
- 理解 LMSR 帮助理解**做市商损失**和**流动性激励**的经济学
"""),

    ("prediction-market-wolfers-zitzewitz-2004.md", ["prediction-markets","efficiency","wolfers"],
     {"author": "Wolfers & Zitzewitz", "year": "2004", "source": "https://www.aeaweb.org/articles?id=10.1257/0895330041371321"},
     """# Prediction Markets (Wolfers & Zitzewitz 2004, JEP)

**Why matters**: 总结预测市场的效率与偏差。学术界对 PM 的共识起点。

## 关键实证
- **Iowa Electronic Markets (1988-2000)**: 选举预测 vs 民调，PM 平均误差 1.5pp，民调 2.5pp
- Intrade 2004: 胜选 probability 与 vote share 相关性 0.88
- PM 通常 **先于民调**反映新信息（~1 天 lead）

## 系统偏差
- **Favorite-longshot bias**: 低 prob (< 10%) 的事件被**高估**，反之
- **Late-resolving market thinness**: 临近结算前流动性消失，价格发散
- **Herding**: 大单进场后散户跟风

## 我们策略应用（CRITICAL）
- **Longshot bias** 解释为什么 Polymarket "Inflation >10%" 4.5% YES vs 真实 <1% → **可卖 NO 套利** ✅
- **PM lead polls** 解释为什么 Virginia 市场 89% YES 时民调仍 52/47 → 市场信号领先 ✅
- Hayek 的 "Price as information aggregator" 应用：市场价格本身是 signal
"""),

    ("prediction-market-surowiecki-wisdom-crowds.md", ["prediction-markets","wisdom-crowds","aggregation"],
     {"author": "Surowiecki", "year": "2004", "source": "https://www.amazon.com/Wisdom-Crowds-James-Surowiecki/dp/0385721706"},
     """# The Wisdom of Crowds (Surowiecki 2004)

**Why matters**: 通俗化 "集体智慧" 概念。预测市场能 work 的理论基础。

## 核心条件（4 个）
1. **Diversity of opinion**（参与者信息差异）
2. **Independence**（避免互相影响）
3. **Decentralization**（可使用本地知识）
4. **Aggregation**（有机制整合）

## 经典例子
- Galton 1906: 787 人猜牛重，平均值 1197 磅 vs 真实 1198 磅（误差 0.08%）
- NASA Challenger 事故前，股市对 Morton-Thiokol（O-ring 供应商）的 selling 速度超过 NTSB 调查

## 何时失效
- **Cascade**: Bikhchandani 1992 — 早期信号引导后续 herd
- **Correlation collapse**: Twitter 时代 Diversity 下降
- **Manipulation**: 少量大单可操纵 thin market

## 我们策略
- **Manifold M$100 market + 30 bettors** ≠ Crowd wisdom（样本太小）
- **Polymarket $12M vol + 1000+ bettors** ≈ 真 crowd wisdom
- 我们反共识下注的前提: **找到 Surowiecki 4 条件被破坏的市场**
  - 低流动性 → Independence + Aggregation 失效
  - 散户扎堆 → Diversity 崩
- Virginia whale M$24k pump → Cascade → 市场 89% 可能 overshoot，但方向对
"""),

    # ═══ Risk Management ═══
    ("risk-taleb-fat-tails.md", ["risk-management","fat-tails","taleb"],
     {"author": "Taleb", "year": "2007", "source": "https://www.amazon.com/Black-Swan-Improbable-Robustness-Fragility/dp/081297381X"},
     """# The Black Swan / Fooled by Randomness (Taleb 2001, 2007)

**Why matters**: 金融收益**不是正态分布** — 核心提醒。所有依赖高斯的模型（MPT、BSM、VaR）都有隐藏尾部风险。

## 核心概念
### 厚尾（Fat Tails）
- 金融日收益 kurtosis 通常 5-20+（正态 = 3）
- "6-sigma" 事件在金融中每 2-3 年发生一次，而非几千年
- LTCM 1998, 2008 GFC, 2020 COVID, 2022 Rate shock — 都是"不可能"事件

### Antifragility (Taleb 2012)
- Convex payoff: 随波动率**增加** → 非线性获益
- Option buying 是 convex
- Selling vol (做市商) 是 concave

## 我们策略应用
- **Oil $150 NO** 面对 Black Swan = Hormuz 升级 → 潜在 **全损**
- **不能押满**：position sizing 要防 **4-sigma 事件**
- 我们已有的 **Quarter-Kelly** 是 concave-to-antifragile 转换
- Taleb 的建议: 80% 极度保守 + 20% 凸收益押注（convex options）
"""),

    ("risk-lopez-de-prado-advances-ml.md", ["risk-management","ml-quant","backtesting","lopez-de-prado"],
     {"author": "Marcos López de Prado", "year": "2018",
      "source": "https://www.amazon.com/Advances-Financial-Machine-Learning-Marcos/dp/1119482089"},
     """# Advances in Financial Machine Learning (López de Prado 2018)

**Why matters**: 金融 ML 当前最权威的专著。解决 **backtesting 过拟合** 等核心问题。

## 核心概念

### Backtest Overfitting (Bailey et al. 2014)
- 一个策略经过 N 次尝试后，即使随机也能找到看似有 Sharpe > 2 的组合
- **Deflated Sharpe Ratio**: 修正多重比较偏差
```
DSR = E[SR] - Var(SR) × Z_α / sqrt(T)
```

### Triple Barrier Method
- 传统 "N-bar forward return" label → 加时间衰减 + 止损/止盈边界
- Label = {+1 if TP hit first, -1 if SL hit first, 0 if time-out}

### Walk-Forward Validation
- **不要用 k-fold CV**：时间序列破坏时序
- 用**扩展窗口**或**滑动窗口** out-of-sample

## 我们策略
- 我们的 **earnings_reaction_scanner backtest** 命中率 50% → 可能就是 overfitting 嫌疑
- 应用 DSR 修正，考虑我们试过多少 scanner
- **Triple barrier** 应该应用到 Manifold 仓位：每笔设 SL/TP 而不是等 resolve
"""),

    ("risk-ml-bailey-false-strategies.md", ["risk-management","backtesting","false-discovery","bailey"],
     {"author": "Bailey & López de Prado", "year": "2014", "source": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659"},
     """# The Probability of Backtest Overfitting (Bailey-Lopez de Prado 2014)

**Why matters**: 第一篇给出 backtest 过拟合的概率量化方法。核心警告: **大多数 "有 edge 的策略" 都是假的**。

## 核心公式
### Probability of Backtest Overfitting (PBO)
```
PBO = P(IS Sharpe ranking 不一致 OOS)
```

### Deflated Sharpe Ratio
给定尝试 N 个策略：
```
DSR_critical = √(2 × ln(N) / T)
只有 Sharpe > DSR_critical 才统计显著
```

## 实证
- 5 年日数据 + 100 次策略尝试 → 最佳 Sharpe 2.0 **基本随机**
- 实际要求 Sharpe > 2.5 + 3+ 年 OOS 验证

## 我们应用
- 我们 scanner × 市场数 × 参数组 = **大量 multiple testing**
- earnings_reaction_scanner backtest 30 样本 50% → 无显著 edge 证据
- commodity_scanner 对 6 仓位 positive → 样本仍太小 (< 50 total)
- **规则**: 至少 50 次实盘（含 win 和 loss）之后再说有 edge
"""),

    # ═══ Market Microstructure (补充 Kyle/GM) ═══
    ("microstructure-harris-adverse-selection.md", ["microstructure","adverse-selection","spread"],
     {"author": "Larry Harris", "year": "2003",
      "source": "https://www.amazon.com/Trading-Exchanges-Market-Microstructure-Practitioners/dp/0195144708"},
     """# Trading and Exchanges (Harris 2003)

**Why matters**: 最详尽的市场微结构实战教材。Kyle/GM 的商业化应用。

## 核心概念
### Bid-Ask Spread 三成分
```
Spread = Order Processing Cost + Inventory Cost + Adverse Selection Cost
```
- **Order processing**: 清算费用 (< 10%)
- **Inventory**: 做市商持仓风险
- **Adverse selection**: 散户 vs 知情交易者的不对称信息

### Lee-Ready (1991) Trade Classification
- 判断每笔交易是 buyer-initiated 还是 seller-initiated
- Tick test: 价格比上笔高 → buy
- Quote test: 比 mid 高 → buy

### Effective Spread vs Realized Spread
```
ES = 2 × d × (P - M)   (交易价 vs mid)
RS = 2 × d × (P - M_{t+Δ})   (永久价格影响后)
AS = ES - RS           (adverse selection cost)
```

## 我们应用
- **Wolf Hour** = adverse selection 成本 AS 爆炸时段
- 做市商 inventory 成本 + AS → spread 从 2pp 飙 10pp
- 散户（我们）等于 knowledgeable 方时**短暂获利**，但必须**快进快出**
"""),

    # ═══ Time Series / Volatility ═══
    ("vol-andersen-bollerslev-intraday.md", ["volatility","intraday","garch","andersen-bollerslev"],
     {"author": "Andersen & Bollerslev", "year": "1997/1998",
      "source": "https://www.jstor.org/stable/2527343"},
     """# Intraday Volatility Clustering (Andersen & Bollerslev 1997)

**Why matters**: 日内波动**不是均匀分布**，有强 clustering。ICT Killzones 的理论基础。

## 核心发现
- 5-min S&P500 futures 波动率有**U-shape**：开盘 + 收盘最大
- 伦敦-纽约重叠时段最高
- 亚洲凌晨最低（Wolf Hour!）

## GARCH 家族
```
GARCH(1,1):  σ²_t = ω + α × ε²_{t-1} + β × σ²_{t-1}
```
- α (ARCH) 捕捉短期冲击
- β (GARCH) 捕捉 persistence
- α + β 接近 1 = 长记忆波动性

## Realized Volatility
```
RV_t = Σ (log return)²  过去 N 个高频 bar
```
理论上是**无偏**的 true vol 估计（如果 tick-level）。

## 我们应用
- **Wolf Hour** 是 U-shape 谷底 → 做市商最少 + adverse selection 最低（对散户有利）
- SATS 指标的 TQI (Trend Quality Index) = 波动 regime 检测
- **1m SP500 GARCH** 可帮我们识别 "quiet before storm" 时刻
"""),

    # ═══ Macro / Central Banking ═══
    ("macro-taylor-rule-1993.md", ["macro","monetary-policy","taylor-rule"],
     {"author": "John Taylor", "year": "1993", "source": "https://web.stanford.edu/~johntayl/Onlinepaperscombinedbyyear/1993/Discretion_versus_Policy_Rules_in_Practice.pdf"},
     """# Discretion Versus Policy Rules (Taylor 1993)

**Why matters**: **Taylor Rule** 是 Fed 政策决策的基准规则。BMI 数据预测 Fed 路径的理论锚。

## 核心公式
```
i_t = r* + π_t + 0.5 × (π_t - π*) + 0.5 × (y_t - y*)
```
- i_t: 名义 Fed funds rate
- r*: 真实中性利率 ~ 2%
- π_t: 当前通胀
- π*: 通胀目标 = 2%
- y_t: 实际 GDP 缺口
- y*: 潜在 GDP

## 2026 应用
```
BMI 美国数据:
  π_2026 = 2.3%
  GDP gap ≈ 0 (BMI 2.06% growth ≈ potential)

Taylor 预测:
  i_2026 = 2 + 2.3 + 0.5×(2.3-2) + 0.5×(0) = 4.45%
```
但 BMI 预测 EOP 3.5% ≠ Taylor 4.45% → Fed 处于 **accommodative** 阶段
暗示: 通胀下降才是 Fed 放松的 trigger

## 我们策略
- Polymarket "1 Fed cut 2026 YES" @ 30.5% 可能仍 under — 如果 π 下到 2% 以下，降息会更多
- Taylor Rule 给我们**交叉验证 BMI rate 预测**的第二来源 → Availability Bias 检查通过
"""),

    ("macro-phillips-curve-modern.md", ["macro","inflation","phillips-curve"],
     {"author": "Phillips / Blanchard", "year": "1958/2016", "source": "https://www.aeaweb.org/articles?id=10.1257/aer.p20161003"},
     """# Phillips Curve: Then and Now (Phillips 1958, Blanchard 2016)

**Why matters**: 通胀与失业的**负相关**定律。但 1970 年后的 stagflation 和 2010s 的 flat curve 引发重新讨论。

## 现代版本
```
π_t = π^e_t - α × (U_t - U*) + supply_shock_t
```
- π^e: 通胀预期
- U*: 自然失业率 (~ 4-5%)
- α: 斜率（最近 10 年接近 0 = curve 平）

## Blanchard 2016 发现
- **Expectations anchoring**: π^e 长期锚定 2% → 短期 curve 平
- 失业 0.5pp 波动对通胀影响 < 0.1pp

## 我们策略（CRITICAL）
- BMI 美国 2026 unemployment: **3.8-4.1%** (数据在 macro-labor)
- BMI 通胀: 2.3%
- 曲线给我们**内部一致性检查**: 低失业 + 目标通胀 = 经济健康
- Polymarket "Inflation > 5% 2026" 要求**严重 supply shock**（石油/关税/金融）
- 无 shock 下真实 P(>5%) < 3%, 市场 19% 是**系统性高估**（我们 NO 仓位理论支撑）
"""),

    # ═══ 量化交易 / ML ═══
    ("quant-sharpe-ratio-1966.md", ["portfolio","sharpe-ratio","risk-adjusted"],
     {"author": "Sharpe", "year": "1966", "source": "https://www.jstor.org/stable/2351741"},
     """# Mutual Fund Performance (Sharpe 1966)

**Why matters**: 定义了 **风险调整收益** 的黄金标准。

## 公式
```
Sharpe Ratio = (R_p - R_f) / σ_p
```

## 基准值
- SR < 1: 一般
- SR 1-2: 良好
- SR 2+: 优秀 (but likely overfitting — Bailey-Lopez de Prado 2014)
- SR 3+: 几乎肯定是 lookahead bias 或 data mining

## 局限
- 正态假设 → 厚尾下 SR 误导
- **Sortino Ratio** 修正: 只惩罚下行波动
```
Sortino = (R_p - R_f) / σ_downside
```

## 我们策略
- Paper trading 4 周后计算 SR
- 低样本下 SR 置信区间极宽 (Lo 2002)
- 至少 50 笔 closed trades 才有意义

要结合 Deflated SR (Bailey 2014) 防假阳性
"""),

    ("quant-rebalancing-markowitz-reality.md", ["portfolio","rebalancing","correlation"],
     {"author": "Ang & Bekaert", "year": "2002", "source": "https://academic.oup.com/rfs/article/15/4/1137/1574663"},
     """# International Asset Allocation with Regime Shifts (Ang-Bekaert 2002)

**Why matters**: **相关性在危机中上升** — "correlation breakdown"。分散化在需要时失效。

## 核心发现
- Normal regime: 国际股市相关性 ~0.3-0.5
- Crisis regime (2008, 2020): 相关性 → 0.8+
- **分散化消失**

## 公式 (Regime-Switching)
```
R_t ~ N(μ_1, Σ_1)  w.p.  p(s_t = 1)
R_t ~ N(μ_2, Σ_2)  w.p.  p(s_t = 2)

Transition matrix: P(s_t | s_{t-1}) 依赖 macro state
```

## 我们策略（CRITICAL）
- 5 个 WTI/Brent 仓位**相关性接近 1**（都是 Iran 风险）
- Iran 升级 = 相关性瞬间 = **所有 NO 同时爆仓**
- 真正分散: 油 + 通胀 + 政治 + FX + Fed → 5 个独立风险轴
- 我们目前**只有 2 轴**（油 + 政治）→ portfolio 太集中
- 加 paper Polymarket Fed + inflation 后扩展到 4 轴 ✅
"""),
]


def make_frontmatter(filename, tags, extra):
    relevance = 9 if any(x in tags for x in ["prediction-markets","microstructure"]) else 7
    novelty = 5  # 经典论文
    fm = [
        "---",
        f"id: {filename.replace('.md','')}",
        f"source: {extra.get('source','?')}",
        f"author: {extra.get('author','?')}",
        f"year: {extra.get('year','?')}",
        f"ingested: {TS}",
        f"category: finance/knowledge",
        f"tags: {tags}",
        f"status: analyzed",
        f"relevance: {relevance}",
        f"novelty: {novelty}",
        "---",
        ""
    ]
    return "\n".join(fm)


def main():
    VAULT.mkdir(parents=True, exist_ok=True)
    written = 0
    for filename, tags, extra, content in PAPERS:
        path = VAULT / filename
        if path.exists():
            print(f"  已存在: {filename}")
            continue
        fm = make_frontmatter(filename, tags, extra)
        path.write_text(fm + content, encoding="utf-8")
        written += 1
    print(f"\n✓ 新增 {written} 篇理论论文 stub")
    print(f"  Vault 目录: {VAULT}")


if __name__ == "__main__":
    main()
