# Origin Quant

一个面向**单一操作者**的加密货币量化交易引擎：事件溯源账本、纯函数策略内核、由风险引擎统一裁决的意图流，以及一套把「交易所不可靠」当作常态来设计的执行层。

> **这不是一个能直接赚钱的系统。** 它是一套**诚实的**交易基础设施：先把记账、风控、执行、审计做对，再谈策略。见 [诚实结论](#诚实结论)。

- 版本：`0.1.0`
- 运行模式：`paper`（默认）/ `live`（三重开关才会开启）
- 依赖：Python 3.11、SQLite（WAL）、FastAPI、pandas / pyarrow
- 交易所：OKX（现货 + USDT 永续，统一账户）

---

## 1. 它解决什么问题

散户量化项目通常死在同一个地方：**账算不清**。策略看起来赚钱，实盘一上就亏，因为：

1. 每根 K 线都重新计算指标 → 回测跑得比实盘慢一个数量级，且容易引入未来函数；
2. 状态写在 JSON 文件里，进程被 kill 后账本与交易所对不上；
3. 止损、止盈、移动止损分散在策略里，改一处漏一处；
4. 风控写成 `if` 散落在下单路径上，**减仓**反而被「数据过期就禁止交易」拦住；
5. 手续费、资金费、滑点在回测里被忽略，净值曲线是幻觉。

Origin Quant 用四条硬约束回应它们：

| 约束 | 实现 |
| --- | --- |
| 策略必须是纯函数 | 四道边界：无墙钟、无随机、无 IO、无可变配置 |
| 账本必须可重放 | `events` 表 append-only，投影与事件**同一事务**写入 |
| 风控必须唯一裁决 | 策略只产出 `Intent`（含风险条款），风险引擎决定数量，执行层只负责下单 |
| 交易所必须被怀疑 | `UNKNOWN` 是订单的一等状态；多腿订单任何一腿失败立即反手平掉已成交腿 |

---

## 2. 六十秒上手

```bash
# 1. 装依赖（项目内虚拟环境）
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"      # Windows
# source .venv/bin/activate && pip install -e ".[dev]"   # macOS / Linux

# 2. 建库
oq init-db

# 3. 生成 90 天合成数据（无需 API Key，可离线复现）
oq seed --source synthetic --days 90

# 4. 回测
oq backtest --source okx --days 90 --cash 10000 --monte-carlo

# 5. 起面板
oq serve --scheduler
# http://127.0.0.1:8000
```

真实数据（需要 OKX 只读 Key）：

```bash
export OQ_MODE=paper
export OKX_API_KEY=...
export OKX_SECRET=...
export OKX_PASSPHRASE=...
oq seed --source okx --days 365
```

---

## 3. 架构

```
                       ┌──────────────────────────────────────────┐
   墙钟 / 边界 ────────►│  Scheduler（单线程，对齐 K 线收盘）        │
                       └───────────────┬──────────────────────────┘
                                       ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │  Ingest  │──►│  Views   │──►│ Strategy │──►│   Risk   │──►│   OMS    │
   │ (REST)   │   │ (causal) │   │  (pure)  │   │ (gates)  │   │ (FSM)    │
   └──────────┘   └──────────┘   └──────────┘   └──────────┘   └────┬─────┘
         │                                                          │
         ▼                                                          ▼
   ┌──────────┐                                            ┌────────────────┐
   │  parquet │                                            │ Broker / Venue │
   └──────────┘                                            └───────┬────────┘
                                                                   ▼
                       ┌──────────────────────────────────────────┐
                       │ Journal：events + projections（同一事务）  │
                       │           SQLite WAL，单写者              │
                       └──────────────────────────────────────────┘
```

关键设计决定：

- **单进程、单写者、单时钟。** 没有 cron 子进程、没有文件锁、没有 JSON 状态文件。所有周期由进程内 `Scheduler` 驱动，回测用 `SimClock`，实盘用 `SystemClock`。
- **意图 / 数量 / 下单三段分离。** 策略说「我想在 BTC 上做多，止损 59,400」，风险引擎算「那你只能买 0.0583 张」，执行层只负责把 0.0583 变成一张订单。
- **双账本。** `carry`（现货多 + 永续空的资金费收割）与 `directional`（趋势跟踪）共享风控预算但独立核算热度。
- **指标因果缓存。** `SymbolView.feature(key, compute)` 在每个 symbol 的共享字典里记忆化，策略用 `view.i` 索引，回测复杂度从 O(n²) 降到 O(n)。

更细的设计与权衡见 [ARCHITECTURE.md](ARCHITECTURE.md)，事故模式见 [FAILURE_MODES.md](FAILURE_MODES.md)，路线图见 [ROADMAP.md](ROADMAP.md)。

---

## 4. 风控模型

入场公式（唯一）：

```
qty = equity × risk_per_trade ÷ |entry − stop|
```

然后逐级削减，**任何一级削减都会把所有腿重新对齐到最小变动单位，若某腿因此低于最小下单量则整笔拒绝**——绝不留下一条裸腿。

| 闸门 | 阈值（`config/risk.yaml`） |
| --- | --- |
| 单笔风险 | 0.25% 净值 |
| 组合总热度 | 2.0%（carry 1.2% / directional 0.8%） |
| 单币种敞口 | 25% 净值 |
| 集群敞口 | 40% 净值（majors / l1 / defi / meme） |
| 保证金占用 | ≤ 50% |
| 总杠杆 | 2.0× |
| 日内回撤 | −1.5% 停止开仓；−2.5% 全部平仓 |
| 周回撤 | −4% 风险减半 |
| 历史最大回撤 | −10% 停机 |
| 重复入场冷却 | 平仓后 60 分钟；止损后 30 分钟 |
| 成本闸门 | 预期收益 ≥ 3× 往返成本 |

**减仓永不被拦截**：止损、止盈、时间止损、`flatten`、`kill` 走的是 `_structural_gates` 分支，只检查结构合法性（方向、数量、标的存在），不检查数据新鲜度、不检查日内熔断——因为阻止减仓才是真正的风险。

---

## 5. 退出阶梯

以 R（初始风险）为单位，这是**唯一**能让「赢与亏可比」的记账方式：

| 触发 | 动作 |
| --- | --- |
| −1R（硬止损） | 全平 |
| +1R | 止损移到保本 |
| +1.5R | 减 33%（TP1） |
| +2R | 止损跟随 2×ATR |
| +3R | 再减 33%（TP2） |
| 剩余 33% | 移动止损奔跑 |
| 32 根 K 线未触发 TP1 | 时间止损 |

理论值：胜率 42% 即可盈亏平衡。实测（见下）达不到，原因是成本。

---

## 6. 诚实结论

在 **合成数据**（90 天、15m、10 个标的、regime-switching 因子 + OU 基差）上：

```
start / end     10,000.00 -> 9,558.83        total return   -4.41%
max drawdown    7.92%                        Sharpe        -1.50
trades          655     win rate 50.8%       profit factor  0.94
avg win/loss R  +0.99R / -1.09R              expectancy    -0.030R
fees / funding  1,517.80 / 65.89
```

**解读：**

- 毛利 ≈ +0.07R/笔，成本 ≈ 0.10R/笔 → 净 −0.03R/笔。**成本吃掉了全部毛利**。
- 手续费 1,517.80 USD 对 10,000 USD 起始净值＝**15%**。这是 15m 周期、taker 双边 5bp 的必然结果。
- 合成数据没有可利用结构（这就是「合成」的定义），所以这个结果**不是调参问题，是数学问题**。任何在合成数据上「调出」正收益的尝试，都只是把噪声拟合成了参数。

**这不是失败，这是仪器读数正确。** 一个不能诚实报告亏损的回测器，没有资格报告盈利。

要让净值转正，只有三个方向（按有效性排序）：

1. **降低换手**：15m → 1h/4h，交易次数降 4~16 倍，成本同比例下降；
2. **降低执行成本**：maker 挂单（2bp → 可能返佣），每年可省 2/3 的手续费；
3. **提高每笔毛利**：需要真正的 edge（资金费套利、跨所价差、波动率事件），而不是在 OHLCV 上找形态。

第 1 条是**参数**，第 2 条是**执行**，第 3 条是**研究**。本项目已把前两条的接口留好（`timeframe` 配置、`OrderType.POST_ONLY`、maker 费率），第三条不在 v0.1 范围内。

---

## 7. 安全边界

实盘需要**三重开关**同时成立，缺一不可：

```python
config.app.live_trading is True
and os.getenv("OQ_LIVE") == "1"
and config.app.mode == "live"
```

默认 `config/base.yaml` 是 `mode: paper` / `live_trading: false`，`.env.example` 里 `OQ_LIVE=0`。任何一次误改都只会在纸面成交。

API 层：

- `/api/health` 公开；其余读取需 `OQ_API_TOKEN`（Bearer）；
- 写操作（`flatten` / `kill` / `pause` / `cycle`）在配置了 token 时一律要求 token；
- 未配置 token 时仅允许 loopback，可用 `OQ_ALLOW_LOCAL_WRITES=0` 关闭本地写权限；
- 密码用 Argon2 hash（`OQ_ADMIN_PASSWORD_HASH`），不存明文；
- 会话 cookie 用 itsdangerous 签名，`OQ_SECRET_KEY` 必须显式设置。

---

## 8. 常用命令

```bash
oq init-db                      # 建库 / 迁移
oq verify                       # 账本完整性（含「持仓 = 成交签名和」校验）
oq status                       # 账本统计
oq config-show                  # 生效配置（密钥脱敏）
oq seed   --source okx --days 365
oq backtest --source okx --days 90 --monte-carlo --json out.json
oq serve  --scheduler           # 面板 + 调度器
oq cycle                        # 只跑一个周期（调试）
oq reconcile                    # 本地持仓 vs 交易所持仓
```

测试与检查：

```bash
.venv/Scripts/python.exe -m pytest -q          # 85 passed
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m mypy src           # Success: no issues in 47 files
```

---

## 9. 目录结构

```
config/                 base.yaml / risk.yaml / strategies/*.yaml
src/oq/
  types.py              Decimal 货币原语、Instrument、订单状态机
  clock.py              SystemClock / SimClock，K 线对齐
  config.py             强类型配置 + 归一化哈希
  domain/               events / intent / order / position / portfolio
  data/                 OKX REST、parquet 存储、指标、合成市场、采集
  strategy/             base（SymbolView）、trend_donchian、carry_basis、position_manager
  risk/                 context / sizing / engine（唯一裁决点）
  execution/            broker / paper / oms / journal / okx_adapter / reconcile
  backtest/             engine / data / metrics / report
  storage/              schema.sql / db / storage（事件 + 投影）
  app/                  api / auth / orchestrator / main / static
tests/                  85 个测试：类型、组合、风控、执行、策略、回测不变量、存储、API
deploy/                 systemd unit
```

---

## 10. 已知限制

- **仅 OKX**，且只支持单向持仓模式（`posSide: net`）。
- 合成数据用于**管道验证**，不可用于策略评估。
- 无跨所套利、无期权、无组合保证金优化。
- Web 面板是 vanilla HTML/CSS/JS（无构建步骤）；Vue 重写排在 P2。
- 单进程，无法横向扩展；这是刻意选择，不是缺陷。
- 回测与实盘的撮合模型不同（回测下一根开盘价 ± 滑点；实盘为市价单 + 真实盘口），二者会有偏差。

## 11. 许可

私有项目，未授权分发。
