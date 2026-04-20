# P10 战略输入：Propfirm v4 Engine 自主交付

**发布日期**: 2026-04-20
**发布层级**: P10 CTO → P9 Tech Lead
**约束模式**: L2 最优（够用即止，禁止炼丹）
**执行模式**: autoagent loop + per-phase pass gate

---

## 方向

把 propfirm v4 从 P0 骨架推到 P5 live money，用 autoagent 分相迭代的方式。不追求 Sharpe 最大化（L3），也不允许 pass gate 边缘过关（L1）——每 phase 达 gate 即转下一个，不回炉调参。

## 成功标准（可量化、不是过程指标）

| 指标 | 目标 | 不达标处置 |
|------|------|-----------|
| P1 Sharpe vs v3 baseline | ≥ 5/8 场景不回归 AND 交易数 ↓ ≥ 40% | 重做 ≤ 2 次；第 3 次失败上升到 P10 重定方向 |
| P2 Sharpe vs P1 | × 1.15，hard floor 0.90 | 同上 |
| P3 WR + 交易数 | WR ≥ 65%，交易数 ↓ ≥ 50% vs P2 | 同上 |
| P4 MFE leakage | < 0.5R over last 30 trades | 若 > 0.5R → 调 ATR mult 或退回 P3 |
| P5 最终 | N ≥ 30 trades, WR ≥ 65% 或 R:R ≥ 1.8, Manifold M$50→M$1000, Polymarket $100→$300 | 退回 P4 或宣告项目结项 |
| 时间盒 | 7 work sessions，≤ 2 calendar weeks | 超时上升 P10，砍 scope |

## 约束条件

- **技术约束**: Python 3.11+，stdlib + pandas-free（复用 `strategy_bake_off.py` 的 ema/adx 纯函数风格）；SQLite 作为唯一持久化层；yfinance + binance spot 为数据源。
- **资源约束**: 单会话 Claude Code，不 spawn > 2 P9 并行；token budget 不超 200k/session；autoagent loop 每 session 目标 1 phase 闭环。
- **合规约束**: P5 前不触碰真金；Dry-Run Gate 对所有外部 mutation（API 下单、push、部署）硬性启用；single-ticket cap M$10/$10 until journal 有 10 胜。
- **纪律约束** (L2): **过 gate 即转场**。禁止在 pass gate 之后继续 sweep 参数找更优解；禁止加 "只再调一个参数" 类增量修改；禁止用 multi-objective Pareto front 替代 single-metric gate。

## 风险预判

1. **`*.yaml` global gitignore 吞 confluence config** → 沿用 P0 的 negation-rule 模式，新增配置用同一套 allow-list。
2. **regime_v3_volume 未 tracked** → P9 启动前先决策：落 commit 进 propfirm_engine/ 子目录下的 snapshot，还是让它保持 untracked import？→ **决断: commit snapshot 到 propfirm_engine/vendor/ 保证跨分支可 import**。
3. **9 个月 BLS 日历缺失** → P1 backtest 限制在 2026-01 ~ 2026-05-13 窗口，或扩展 news_calendar.yaml 前不跑 Q3/Q4 回测。**决断: 允许 P1 用已知窗口，P4 前必须补齐**。
4. **TV paper 手动 CSV 回路在 autoagent loop 里是人工断点** → P3 引入前 P10 要决断：要么接受每周一次人工同步（时间盒延长），要么用 webhook 代理。**决断: 先人工 CSV，第 10 笔后评估是否值得写 bridge**。
5. **Manifold API rate limit / auth** → P5 前 P9-B 预研并写 dry-run mock，不要拖到 ship 当天才发现 auth 问题。
6. **L2 纪律失守（炼丹瘾）** → P10 每 phase 末尾审阅 `phase_N_report.md`，发现回炉迹象直接拍断。

## 不做什么（P10 做减法）

- 不做 Avellaneda-Stoikov 做市（独立 workstream）。
- 不做 Wolf Hour 02:30-04:00 UTC momentum directional trade（propfirm 研究已否）。
- 不做 I.q. China 4/4 模仿（二项噪声 per DSR）。
- 不做 breakeven stop 任何变体（proven net negative）。
- 不做 4+ confluence 信号堆叠（过拟合 per Colibri 研究）。
- 不做 phase pass gate 之后的参数扫描（L2 纪律硬禁止）。
- 不做 multi-asset portfolio 优化——每 phase 独立 8 场景矩阵评估。
- 不做 ML/RL 回路——纯规则引擎到 P5 结项。

## P9 编制

### P9-A：Backtest Pipeline（管 P1 + P2 + P3 + P4）

- **管辖**: session_filter, atr_utils, confluence_scorer, mfe_tracker, engine.py, reports.py
- **核心交付物**: 每 phase 一个 `phase_N_report.md` + git tag v4.p<N> + journal 里 trades 可追溯
- **不管**: 实盘 API、TV bridge、Manifold/Polymarket 下单
- **P8 拓扑**: 逻辑上可再拆 4 个 P8（每 phase 一个），但 L2 纪律下建议串行执行，节省上下文切换
- **验收节点**: 每 phase 结束 P10 亲自审 `phase_N_report.md` + git diff v4.p<N-1>..v4.p<N>

### P9-B：Live Carrier（管 P5 的实盘落地）

- **管辖**: manifold_live.py, polymarket_live.py, tv_paper_bridge.py, pn_sizing.py
- **核心交付物**: 3 个 live carrier 的 dry-run mock → live toggle，带 Dry-Run Gate 日志
- **不管**: backtest 逻辑、indicator 计算
- **P9-B 启动条件**: P4 pass gate 达成。P4 之前 P9-B 只做 API 预研（写 `docs/live-readiness.md` 列 Manifold/Polymarket auth/rate-limit/endpoint 清单）

### P9 间接口

- 通过 `propfirm_engine/engine.py` 的 flag 接口耦合：P9-A 交付带 flag off 的 engine，P9-B 负责翻 flag on + wrap 实盘 executor。
- 数据通过 `trade_journal.sqlite` schema 交换，不允许直接 Python object 传递。
- 冲突仲裁：若 P9-B 发现实盘 API 要求 schema 变更（如需要 `order_id` 列），不自己改 schema，提 issue 给 P10 仲裁是否引入 migration。

## 基础能力清单（造土壤）

- [x] Memory 结构: `propfirm-v4-trading-mandate.md`（授权边界）+ `.planning/propfirm_v4_state.json`（进度指针） — P0 已建
- [ ] 必要 Skill: `superpowers:executing-plans`（P9 默认 skill）+ `pua:p9`（角色注入）
- [ ] 质量门禁:
  - 每 phase 结束 P10 人工 review report + diff
  - pytest 覆盖率 ≥ 80% 在新模块上线前
  - Deflated Sharpe Ratio 在 phase 宣告 pass 前跑一次
- [ ] 方法论沉淀: 每 phase 结束写 `memory/propfirm-v4-phase-N-handoff.md`，P5 结项写 `memory/propfirm-v4-final-retrospective.md`
- [ ] L2 纪律守卫: P10 在每 phase pass gate 达成的那一刻发 `[P10-断事] Phase N pass, 转 Phase N+1，禁止回炉` 旁白，写入 commit trailer

---

## Autoagent Loop 定义（L2 参数）

```
while current_phase ≤ p5 and not interrupted:
  1. 读 .planning/propfirm_v4_state.json → current_phase
  2. P9-A (或 P9-B @ P5) 执行该 phase plan 里的 tasks
  3. 跑 pass gate 指标（spec §6 + 本文档成功标准表）
  4. if pass:
       - write phase_N_report.md
       - git tag v4.p<N>
       - update state.json → current_phase = N+1
       - write memory/propfirm-v4-phase-N-handoff.md
       - [P10-断事] 旁白 + 禁止回炉
       - continue
  5. if fail && retry_count < 2:
       - git reset --hard v4.p<N-1>
       - retry_count++
       - continue
  6. if fail && retry_count == 2:
       - ESCALATE to P10 human → redesign phase，不 spawn P9 盲跑第 3 次
       - break
```

**Score 函数**（每 phase 唯一 gate metric，单一目标不允许 Pareto）:
- P1: `min(sharpe_ratio_per_scenario_after_filter / sharpe_before_filter)` ≥ 1.0 over ≥ 5/8 scenarios
- P2: `sharpe_p2_avg / sharpe_p1_avg` ≥ 1.15
- P3: `winrate_p3 ≥ 0.65 AND trade_count_p3 / trade_count_p2 ≤ 0.50`
- P4: `leakage_last_30 < 0.5`
- P5: 复合 gate，见 spec §10 P5 pass gate

**禁止动作**（L2 discipline）:
- 禁止在 gate 达标后扫参数"看看能不能更好"
- 禁止 early-terminate phase 在 gate 未达标时宣布 "good enough"
- 禁止并行跑多个 phase（违反 clean attribution）
- 禁止改 spec §6 pass gate 阈值降低难度（若要改，必须 P10 人工批 + 写入 decisions log）

---

## P10→P9-A 第一道下发（即时启动 P1）

> [P10-编制] 本项目第一阶段需要 P9-A 启动 P1 session filter。P9-B 待命做 API 预研。战略输入已下发。

**P9-A 当前任务包**：
1. 从 `v4.p0` tag 切 `v4/p1-session-filter` 分支
2. 执行 `docs/superpowers/plans/2026-04-20-propfirm-v4-engine-plan.md` Phase 1 的 6 个 tasks
3. 用 `superpowers:executing-plans` skill 作为默认 runner
4. 遇到 regime_v3_volume import 问题 → 参照风险 #2 决断：vendor snapshot
5. Pass gate 达成 → 回 P10 审 + 转 P2（不等人问）
6. 两次 fail → 停，上报 P10 redesign

**P9-B 当前任务包**（并行但轻量）：
1. 写 `docs/live-readiness.md`
2. Manifold API key 在 `~/Documents/GitHub/FinceptTerminal/_Manifold API Key` 已有，但别 commit
3. 调查 Polymarket USDC via Binance 当前状态
4. 交付物：readiness 清单，不写任何实盘代码

---

## P10 验收 Checklist

每 phase 结束 P10 必看：
- [ ] `phase_N_report.md` 存在且含 8-scenario 矩阵
- [ ] `git tag v4.p<N>` 存在
- [ ] `.planning/propfirm_v4_state.json` 指针推进
- [ ] `memory/propfirm-v4-phase-N-handoff.md` 存在
- [ ] Pass gate metric 在 report 中量化呈现，不是定性描述
- [ ] 没有 `--no-verify` / `git push --force` / skip-test 痕迹
- [ ] Commit log 没有 "WIP"/"fix typo" 类低信息 commit（L2 discipline）

---

## 不 spawn 实际 subagent 的理由（P10 断事）

当前 Claude Code 环境下 `Agent` 工具可用，我可以用 `pua:tech-lead-p9` 或 `superpowers:executing-plans` spawn 真的 P9 子代理。但 L2 纪律下：

- **Phase 1 先 in-line 执行**（当前会话继续，我扮演 P9-A 角色）——先验证 plan 质量，再投入并行编制。
- 若 P1 顺利，P2 起考虑 spawn 真 subagent 并行（如 P9-A 跑 backtest 期间 P9-B 做 API 预研）。
- 不在 P1 盲目 spawn 双 P9，避免基础设施未验证就放大开销。

这也是做减法：**验证前不铺 topology**。

---

**下一步**: 等用户 go，P9-A 立即启动 P1。
