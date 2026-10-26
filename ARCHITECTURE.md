# 架构说明

本文记录**为什么这样设计**，而不是逐行解释代码。每条决定后面都跟着被否决的替代方案，因为没有否定项的架构文档等于广告。

---

## 1. 形态：单进程模块化单体

**选择**：一个 Python 进程，一个 SQLite 文件，一个调度线程。

**理由**：
- 单操作者、单账户、最多 8 个持仓。这是**一个进程**的工作量。
- 微服务带来的网络分区、重复消费、分布式事务，在 8 个持仓面前是纯负债。
- 崩溃恢复只需要重放一个本地文件，不需要协调多个组件的快照。

**已否决**：
- *微服务 + 消息队列*：把「订单不能重复」这个最难的约束变成分布式问题，收益为零。
- *Nautilus / Jesse / Hummingbot 作为内核*：框架的抽象（Actor、引擎、适配器矩阵）比问题本身还复杂；而且它们的风控模型无法表达「减仓永不被拦截」。
- *在进程内跑用户 Python 插件*：等于把任意代码执行权交给策略，回测与实盘边界消失。

**留下的缝**：`Broker`、`Journal`、`Clock` 都是 Protocol。将来要拆服务，替换实现即可，调用方不动。

---

## 2. 账本：事件溯源 + 同事务投影

**选择**：

```sql
events(id, ts_ms, cycle_id, type, symbol, payload, config_hash, code_version)
```

所有状态变化先写事件，再写投影（`orders` / `fills` / `positions` / `trades` / `equity`），**在同一个 `BEGIN IMMEDIATE` 事务里**。

**理由**：
- 「投影与事件不一致」是账本类系统最常见的腐坏方式。同事务写入让这种不一致在物理上不可能，除非有人绕过 `Store`。
- 事件带 `config_hash` 与 `code_version`，所以任何一笔交易都能回答「当时用的是哪份配置、哪个版本的代码」。
- `oq verify` 做了三层校验：SQLite 完整性、外键、以及**「持仓数量 = 该 (symbol, book) 下所有成交的签名和」**。最后一条能抓出任何在事务外写投影的代码。

**已否决**：
- *JSON 文件当状态*：原子性靠 `os.replace` 勉强能救，但并发读写与审计完全无解。
- *只写事件，投影按需重算*：查询要扫全表；面板每 15 秒刷新一次会烧穿 CPU。
- *ORM*：SQLAlchemy 的 session 生命周期与「一个事务一次提交」的心智模型冲突。

---

## 3. 策略：纯函数内核

**四道边界**（违反任何一条，回测就不再预测实盘）：

1. **无墙钟** —— 只读 `ctx.now_ms` 与 `view.ts_ms`；
2. **无随机** —— 不许 `random`、`uuid4`、`hash()`；
3. **无 IO** —— 不许读文件、发请求、查数据库；
4. **无可变配置** —— `ctx.params` 是只读映射。

**接口**：

```python
class Strategy(Protocol):
    id: str
    book: Book
    def generate(self, ctx: StrategyContext) -> list[Intent]: ...
```

策略返回 `Intent`，**不返回订单，也不知道自己有多少钱**。它只说「做多 BTC，止损 59,400，TP 阶梯 1.5R/3.0R」。

**指标因果缓存**：`SymbolView.feature(key, compute)` 在**每个 symbol 共享**的字典里记忆化。回测每根 K 线都调用策略，若每次都重算 200 周期 EMA，回测复杂度是 O(n²)——90 天 15m 数据要跑几十分钟。缓存后 O(n)。

**已否决**：
- *策略直接下单*：无法统一风控，也无法在回测与实盘之间复用同一段代码。
- *策略返回 pandas DataFrame*：类型不安全，且把指标计算与决策耦合。
- *给策略传 `now()`*：一旦策略自己取时间，回测就出现未来函数，且这种 bug 极难发现。

---

## 4. 意图 → 风险 → 执行

三段之间只有数据契约，没有控制反转：

```
Intent(strategy_id, book, action, legs, stop_px, tp_ladder, sizing, tags)
   │
   ▼  RiskEngine.evaluate(intent, RiskContext) -> RiskDecision(approved, legs: LegPlan[], gates[])
   │
   ▼  OrderManager.execute(decision, intent, broker, BrokerContext) -> ExecutionResult
```

**风险引擎是唯一裁决点**。它负责：数量（`risk_per_trade` / 止损距离）、所有组合级上限、所有入场闸门、以及**退出路径的结构校验**。

**闸门顺序**（入场）：`kill_switch → trading_halt → data_fresh → universe → spread → cooldown → stop_sanity → risk_reward → cost_edge → position_count → heat → drawdown → leverage`

**退出路径**（`_structural_gates`）只检查：标的存在、方向正确、数量不超过持仓、`MOVE_STOP` 必须收紧。**不检查**数据新鲜度、不检查熔断、不检查冷却。理由：行情断流时你最想做的事就是减仓，此时拦住它是灾难。

**已否决**：
- *风控写在下单函数里*：无法枚举，无法测试，无法解释「为什么这笔没成交」。
- *策略自己算仓位*：每个策略都会重复实现同一套上限，且总会漏掉一个。
- *风险引擎直接下单*：职责混杂，回测无法替换撮合。

---

## 5. 执行层：把交易所当作不可靠组件

**订单状态机**：

```
SUBMITTING → ACKED → PARTIAL → FILLED
     │          │        │
     └──────────┴────────┴──► UNKNOWN ──(reconcile)──► FILLED / CANCELED
```

`UNKNOWN` 是**一等状态**，不是错误。超时、网络中断、5xx 都落到这里。处理方式是**用确定性 `clOrdId` 重试**（`oq` + `sha1(intent_id, leg_index)[:24]`），而不是假设失败后重新下单——后者会造成双倍仓位。

**多腿原子性**：`open_pair` 两腿，若第二腿被拒，立即用 `_unwind` 平掉第一腿。宁可两腿都没开，也不要留一条裸腿——裸腿是无限风险。

**已否决**：
- *超时即失败*：会造成重复下单。
- *两腿串行且失败就重试*：在腿间留下敞口窗口。
- *在 OMS 里写重试循环*：重试属于调度层，OMS 只负责一次尝试的状态迁移。

---

## 6. 双账本：carry 与 directional

| | carry | directional |
| --- | --- | --- |
| 标的 | 现货多 + 永续空 | 永续 |
| 收益来源 | 资金费 + 基差收敛 | 趋势 |
| 持仓周期 | 数天~数周 | 数小时~数天 |
| 风险 | 基差、脱锚、强平、腿失败 | 方向 |
| 热度上限 | 1.2% | 0.8% |

两者共享账户权益与总杠杆上限，但热度独立核算——否则高频的方向性交易会挤占 carry 的预算。

`config/base.yaml` 的 `universe` 用 `books: [directional, carry]` 声明一个标的可被哪些账本使用。BTC/ETH 永续同时服务两个账本（carry 用它做对冲腿）。

**已否决**：
- *carry 用永续-永续对冲*：两腿同向爆仓风险，且资金费差通常不足以覆盖成本。
- *单一账本混跑*：风险预算无法分离，退出逻辑互相干扰。

---

## 7. 回测：与实盘共用同一套内核

回测**不是**另一套代码。它替换三个东西：

1. `Clock` → `SimClock`；
2. `Broker` → `PaperBroker`（下一根开盘价 ± 滑点 + taker 费率）；
3. 数据源 → parquet / 合成市场。

策略、风险、OMS、账本**完全复用**。所以回测里出现的 bug，就是实盘会出现的 bug（反之亦然）。

**成交语义**（必须显式定义，否则回测毫无意义）：

- 第 N 根收盘产生信号 → 第 N+1 根**开盘价**成交；
- 保护性止损/止盈在**当根 K 线内**用 high/low 判定，**止损优先于止盈**（同一根既触发止损又触发止盈时，按止损算——这是保守假设）；
- 入场当根不触发止盈（避免「一根大阳线既开仓又止盈」的作弊）；
- 每根 K 线收盘按收盘价标记净值。

**已否决**：
- *当根收盘价成交*：引入未来函数，回测虚高。
- *只按开盘/收盘判定止损*：低估止损频率，高估净值。

---

## 8. 时间、精度与货币

- **时间**：一律 UTC 毫秒整数。没有 `datetime` 参与比较，没有本地时区。
- **金额与数量**：一律 `Decimal`。浮点只出现在指标计算（EMA/ATR/σ）中，且进入决策前必须过 `D()`。
- **数量单位**：内部一律**基础币种**（BTC，而非张）。合约张数只在适配器里通过 `Instrument.contracts()` / `round_contracts()` 换算。
- **向下取整原则**：`round_qty` 永远向下取整到 `lot_size`。宁可少买一点，绝不多买。

**已否决**：
- *float 记账*：`0.1 + 0.2 != 0.3`，在 8 位小数的币圈会累积成真实资金误差。
- *以「张」为内部单位*：现货没有张，混用必然在某个边界漏换算。

---

## 9. Web 层

- FastAPI + 内联静态资源（无构建步骤，`pip install` 完就能跑）。
- 单文件 SVG 净值图（不引入 ECharts，省 1MB 与一个构建链）。
- 认证：Bearer token 或签名 cookie 或 loopback；写操作额外检查 `OQ_ALLOW_LOCAL_WRITES`。
- 面板每 15 秒轮询 `/api/status` + `/api/equity`；不做 WebSocket（8 个持仓不需要实时推送）。

**已否决**：
- *Vue/React SPA*：多一个 Node 构建链、多一个产物目录、多一处版本漂移。P2 再考虑。
- *Grafana / Prometheus*：对单操作者是运维负担，不是可观测性。

---

## 10. 部署

- 单容器，非 root，`cap_drop: ALL`，只读根文件系统 + 一个可写数据卷。
- `HEALTHCHECK` 打 `/api/health`。
- systemd unit 用 `Restart=on-failure` + `MemoryMax`，而不是 supervisor。
- 数据目录（SQLite + parquet）是**唯一**需要持久化的东西。

---

## 11. 明确不做的事

| 不做 | 原因 |
| --- | --- |
| 多交易所 | 每个所的合约规格、限频、错误码都不同，v0.1 只做透一个 |
| 组合保证金优化 | OKX 统一账户已做，重复实现只会更差 |
| LLM 在订单路径上 | 不可复现、不可审计、延迟不可控 |
| 高频 / 做市 | 需要 colocation 与微秒级时钟，与单进程 Python 矛盾 |
| 自动调参 | 在噪声上优化等于制造过拟合 |
| 「AI 预测价格」 | 没有可验证的样本外证据前，这是叙事不是工程 |
