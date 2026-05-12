# _attic — 已被取代的旧版本（lessons learned，非垃圾桶）

这里放**经过严肃测试后被证伪、但保留作为参考的代码**。直接复用前先查 EverMem
确认它为什么被取代。

## 当前内容

### `regime_dual_engine.py` (归档于 2026-05-12)
- **是什么**: ADX + BBW 的 dual-regime 引擎（trend / range 双引擎切换）
- **为什么归档**: 8-scenario 跨市场实测仅 2/8 胜，S6 独用 4/8 最 universal
- **EverMem 出处**: `regime-dual-engine-findings.md` 完整结论
- **唯一仍在调用的位置**: `regime_v3_volume.py:18` 通过 `from _attic.regime_dual_engine import ...` 复用基础 indicator (`adx`, `bb_width`, `classify_regime`, `fetch_any`, `run_dual_engine`)；v3 在此基础上加了 volume 维度

## 复用规则

1. 路径前缀必须保持 `_attic.` —— production 代码 import attic 是**有意为之的味道**，提醒
   "你正在依赖一个被证伪的实现的一部分"。
2. 想从 attic 里"救出"某个函数：先把它单独抽到 production 路径（如新建
   `data_algo/regime_indicators.py`），再调整调用方。**不要在 production 与 attic
   之间双向依赖。**
3. attic 内容不要再扩展功能；只接受 bug fix 和 docstring 补充。
