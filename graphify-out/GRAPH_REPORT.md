# Graph Report - .  (2026-08-03)

## Corpus Check
- 126 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1153 nodes · 2455 edges · 79 communities detected
- Extraction: 45% EXTRACTED · 55% INFERRED · 0% AMBIGUOUS · INFERRED: 1358 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `DatabaseManager` - 123 edges
2. `PortfolioStrategy` - 114 edges
3. `RebalanceConfig` - 102 edges
4. `DataFetcher` - 53 edges
5. `UniverseManager` - 36 edges
6. `PortfolioBacktestEngine` - 35 edges
7. `EvaluateRequest` - 35 edges
8. `LeaderboardRequest` - 35 edges
9. `ModelsRequest` - 35 edges
10. `GenerateRequest` - 35 edges

## Surprising Connections (you probably didn't know these)
- `多维因子计算引擎      整合日线衍生因子（高频）和财报衍生因子（低频）的统一入口。` --uses--> `DatabaseManager`  [INFERRED]
  factors/engine.py → data/database.py
- `批量计算质量因子（F-Score / Accruals / R&D）          注意: 需要从新浪拉取财报，有网络请求。               m` --uses--> `DatabaseManager`  [INFERRED]
  factors/engine.py → data/database.py
- `批量计算 PEG 估值因子          Args:             stock_dfs: {symbol: DataFrame} 含 pe 列的日` --uses--> `DatabaseManager`  [INFERRED]
  factors/engine.py → data/database.py
- `一站式计算全部因子          Args:             symbols: 股票代码列表             days: 日线数据天数` --uses--> `DatabaseManager`  [INFERRED]
  factors/engine.py → data/database.py
- `判断是否涨停（A股规则：主板10%，创业板/科创板20%，ST 5%）` --uses--> `DatabaseManager`  [INFERRED]
  paper_trading/broker.py → data/database.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.02
Nodes (86): 哑铃型配置策略 (Barbell 2026)  实施《2026年A股中小盘策略》顶层逻辑： 1. 退市预警底线：利用市盈率(PE)、市净率(PB)构建第一道防线, 北漂青年 1500 万微盘股量化策略  核心逻辑： 1. 目标股票池：小盘股、微盘股。 2. 财务过滤：通过 PB/PE 近似估算 ROE，排除非盈利垃圾股。, 第三层：完整复刻版     规则：V2(微盘+财务)基础上叠加技术和交易面因子     1. 20日均线乖离率 (Bias)：喜欢超跌反弹或某种特定形态, 第四层：散户行为猎杀版      在 V3 基础上，新增以下散户行为因子与风控规则：      新增因子：     1. 处置效应因子 (disposition, 第一层：纯微盘基线     规则：只过滤 ST 和退市，按流通市值升序排列，全A选前 N 只。, 第二层：微盘 + 财务安全垫     规则：V1 基础上，加入基本面门槛（盈利股）。     利用数据中的 PE 和 PB 估算 ROE。     ROE ≈, 围猎散户专属策略  核心理念： 散户有三大系统性弱点，可以被量化并反向利用：  1. 处置效应 (Disposition Effect)    - 散户倾向于", 围猎散户策略      选股逻辑：     1. 基础过滤：非ST、股价>1元、上市>150天、流通市值<50亿     2. 基本面：ROE(PB/PE) > (+78 more)

### Community 1 - "Community 1"
Cohesion: 0.03
Nodes (64): breakout_worker_entry(), get_limit_pct(), _get_signal_funcs(), 动能突破策略 — 统一 Worker 基座  将 V1/V2/V3 共享的全部逻辑（~85%）抽取至此：     - 持仓恢复（_restore_holding, 动能突破策略的统一实时盘中扫描 Worker。      由 server.py 在独立线程中启动。     version: 'v1' | 'v2' | 'v, 根据股票代码判定涨停板幅度。      - 主板（60xxxx / 00xxxx）：10%     - 创业板（300xxx）：20%     - 科创板（68, 惰性加载信号过滤函数，避免启动时 import 所有版本, _update_task_db_safely() (+56 more)

### Community 2 - "Community 2"
Cohesion: 0.03
Nodes (39): ABC, BaostockFetcher, _ensure_login(), Baostock 基本面历史数据获取器  用于突破免费 API 快照限制的“Point-In-Time”真实基本面溯源模块。 1. 提供历史上每日的 peTTM, 获取过去指定天数的历史基本面切片（支持全缓存提速）         返回 DataFrame, columns = ['日期', 'peTTM', 'pbMRQ, 底层的 baostock 爬取与 SQLite 入库操作（全局长连接，只加锁不 login/logout）, 一次性从 baostock 拉取全市场的股票基础信息（包含上市日期）并入库, 转换 6 位数字代码为 baostock 的带市场前缀代码 (+31 more)

### Community 3 - "Community 3"
Cohesion: 0.16
Nodes (52): Barbell2026Strategy, BeipiaoMicrocapV1, BeipiaoMicrocapV2, BeipiaoMicrocapV3, BeipiaoMicrocapV4, BeipiaoRetailHunter, BacktestEngine, 回测引擎      接收策略产生的信号，模拟历史交易过程。 (+44 more)

### Community 4 - "Community 4"
Cohesion: 0.04
Nodes (68): applyDeployStrategyDefaults(), closeSubPage(), closeTaskDetail(), confirmStrategy(), deleteAutopilotTask(), deleteBacktestRecord(), _drawBarChart(), _drawFlowChart() (+60 more)

### Community 5 - "Community 5"
Cohesion: 0.05
Nodes (28): BacktestResult, FactorEngine, PaperTradingEngine, 模拟交易引擎  接入实时行情，运行策略，模拟交易过程。, 持续运行策略监控          Args:             interval: 检查间隔（秒）, 批量计算 PEG 估值因子          Args:             stock_dfs: {symbol: DataFrame} 含 pe 列的日, 一站式计算全部因子          Args:             symbols: 股票代码列表             days: 日线数据天数, 运行回测          Args:             df: 日线数据             signals: 策略生成的信号列表 (+20 more)

### Community 6 - "Community 6"
Cohesion: 0.05
Nodes (26): Exception, _delayed_restore(), evaluate_strategy(), get_backtest_task(), get_backtest_task_full(), _get_portfolio_strategies(), get_realtime_breadth(), get_stock_kline() (+18 more)

### Community 7 - "Community 7"
Cohesion: 0.06
Nodes (37): atr(), ATR — 平均真实波动范围  由 J. Welles Wilder 发明。衡量市场波动性/焦虑程度。  - ATR 大：市场波动剧烈 - ATR 小：市场平静, 计算 True Range      TR = max(         当日最高 - 当日最低,         |当日最高 - 昨日收盘|,, 计算 ATR（Wilder 平滑）      Args:         high: 最高价序列         low: 最低价序列         clos, true_range(), BaseModel, donchian_channel(), DonchianResult (+29 more)

### Community 8 - "Community 8"
Cohesion: 0.1
Nodes (13): is_stock_downloaded(), 检查数据库中是否已存在该股票的充足数据（超过1000条视为已下载）, robust_download_pool(), 北漂策略规模极限探测器 (Scale Matrix) 测试 1, 3, 5 年 x 10万, 30万, 2000万 的滑点与本金反噬矩阵, 动态股票池管理器  负责获取指数成分股、过滤垃圾标的，为组合策略提供干净的候选池。, 通过 AKShare 获取全市场 A 股列表, 获取股票池的截面数据（一次性获取所有因子）          Args:             symbols: 股票代码列表          Return, 灾备管线：用 Sina 拿实盘行情，用 SQLite fundamental_cache 拿 PE/PB/市值 (+5 more)

### Community 9 - "Community 9"
Cohesion: 0.2
Nodes (16): count_a_share_trading_days(), filter_a_share_trading_dates(), get_a_share_order_state(), get_a_share_trading_state(), is_a_share_precompute_time(), is_a_share_trading_day(), _normalize_datetime(), now_shanghai() (+8 more)

### Community 10 - "Community 10"
Cohesion: 0.21
Nodes (13): check_limit_up(), close_position(), filter_kcbj_stock(), filter_limitdown_stock(), filter_limitup_stock(), filter_new_stock(), filter_paused_stock(), filter_st_stock() (+5 more)

### Community 11 - "Community 11"
Cohesion: 0.21
Nodes (13): check_limit_up(), close_position(), filter_kcbj_stock(), filter_limitdown_stock(), filter_limitup_stock(), filter_new_stock(), filter_paused_stock(), filter_st_stock() (+5 more)

### Community 12 - "Community 12"
Cohesion: 0.18
Nodes (15): add_friction_factors(), build_market_returns(), compute_amihud_illiquidity(), compute_amihud_log(), compute_idio_vol(), compute_total_vol(), compute_turnover_volatility(), 摩擦与风险因子计算模组 (Friction & Risk Factors)  基于价格容量波动的资金异动/庄家博弈分析。  修订记录:   - v1: 基础版本 (+7 more)

### Community 13 - "Community 13"
Cohesion: 0.16
Nodes (9): _append_live_metrics(), _coerce_numeric(), filter_volume_shock_signals(), 生成结构化信号指标，写入 trade_signal_ext。, 对 Sina 快照补充 VWAP、实时量比、换手率。      不同行情源的成交量可能是股或手。这里用 amount / volume 与现价的数量级关系做推断, _safe_metric_float(), _update_task_db_safely(), volume_shock_signal_metrics() (+1 more)

### Community 14 - "Community 14"
Cohesion: 0.19
Nodes (13): compute_accruals(), compute_fscore(), compute_quality_factors(), compute_rd_intensity(), fetch_financial_data(), 质量、估值与成长因子计算模组 (Quality, Valuation & Growth Factors)  数据来源: 新浪财经三大报表 (via AKShar, 计算 Piotroski F-Score (9分制)      9项评分标准:       盈利能力 (4分):         1. ROA > 0, 安全地将财报字段转为 float，NaN/None 返回 0 (+5 more)

### Community 15 - "Community 15"
Cohesion: 0.23
Nodes (13): fetch_quarter_data(), get_target_quarters(), main(), quarter_to_date_str(), quarter_to_stat_date(), 季度转 yyyymmdd 格式（AKShare 参数格式）, 季度转 yyyy-mm-dd 格式（数据库存储格式）, 通过 AKShare 的 stock_yjbb_em 接口批量获取某季度全市场业绩报表     返回新增入库的条数 (+5 more)

### Community 16 - "Community 16"
Cohesion: 0.24
Nodes (13): apply_stage_to_daily_kline(), close_baostock_socket(), get_bs_code(), login_baostock_with_retry(), main(), parse_args(), prepare_stage_table(), query_st_dates_with_retry() (+5 more)

### Community 17 - "Community 17"
Cohesion: 0.22
Nodes (13): fetch_qfq_data(), get_all_symbols(), load_checkpoint(), main(), 前复权价格批量更新脚本  功能：使用 AKShare 的 stock_zh_a_hist(adjust='qfq') 接口，       仅更新 MySQL d, 获取前复权 OHLC 数据      优先使用 stock_zh_a_daily（新浪源，更稳定）     失败时回退到 stock_zh_a_hist（东财源, 将前复权 OHLC 写入 MySQL，只更新 open/close/high/low 四列。      Returns: 更新的行数, 从 MySQL 获取所有 distinct symbol (+5 more)

### Community 18 - "Community 18"
Cohesion: 0.27
Nodes (11): atomic_swap(), create_new_table(), format_date(), insert_batch(), main(), parse_float(), process_csv(), 安全解析浮点数，nan/空值返回 None (+3 more)

### Community 19 - "Community 19"
Cohesion: 0.29
Nodes (10): _extract_date_from_zip(), format_date(), main(), parse_float(), parse_row(), process_zip(), 从 zip 文件名中提取日期字符串，如 '2026-04-13, 把 '20250627' 转成 '2025-06-27 (+2 more)

### Community 20 - "Community 20"
Cohesion: 0.44
Nodes (8): fetch_quarter(), get_target_quarters(), main(), quarter_to_date_str(), quarter_to_stat_date(), 通过 AKShare 批量获取某季度的 ROE 和 ROA，写入 eastmoney_roa, safe_decimal(), safe_float()

### Community 21 - "Community 21"
Cohesion: 0.32
Nodes (7): batch_compute_peg(), compute_peg(), fetch_earnings_forecast(), 估值因子计算模组 (Valuation Factors)  PEG = PE / 预期EPS增长率 数据来源:   - PE: 本地 daily_kline 表, 获取全市场盈利预测数据      Args:         date: 报告期，如 '20241231'，默认取最近年报期      Returns:, 计算 PEG 比率      Args:         pe: 市盈率 (PE-TTM)         earnings_growth: 预期盈利增长率（百, 批量计算 PEG      Args:         stock_pe_map: {股票代码: PE-TTM}         forecast_date:

### Community 22 - "Community 22"
Cohesion: 0.32
Nodes (3): backtest(), _get_strategy(), paper()

### Community 23 - "Community 23"
Cohesion: 0.33
Nodes (6): calculate_position_size(), calculate_r_multiple(), PositionSize, 头寸管理 — Position Sizing  融合 Elder 2% 规则与 Van Tharp R 倍数理论。  核心原则： - 单笔交易的最大亏损 = 总, 基于风险计算仓位大小（Elder 2% 规则）      仓位 = 可承受亏损金额 / 每股风险     每股风险 = |入场价 - 止损价|      Arg, 计算 R 倍数（Van Tharp）      R = 实际盈亏 / 初始风险     - R > 0：盈利交易     - R < 0：亏损交易     -

### Community 24 - "Community 24"
Cohesion: 0.6
Nodes (5): batch_insert(), fetch_dupont_for_stock(), get_all_stock_codes(), main(), to_bs_code()

### Community 25 - "Community 25"
Cohesion: 0.4
Nodes (3): 动能突破 V1 — 信号过滤条件  六条件联合碰撞：     1. 突破 20 日新高     2. 爆量 3-5 倍     3. 涨幅 > min_pct_, V1 六条件向量化碰撞。      参数:         merged: 快照与阈值合并后的 DataFrame         pre_close_col:, v1_1_signal_filter()

### Community 26 - "Community 26"
Cohesion: 0.4
Nodes (3): 动能突破 V1 — 信号过滤条件  六条件联合碰撞：     1. 突破 20 日新高     2. 爆量 3-5 倍     3. 涨幅 > min_pct_, V1 六条件向量化碰撞。      参数:         merged: 快照与阈值合并后的 DataFrame         pre_close_col:, v1_signal_filter()

### Community 27 - "Community 27"
Cohesion: 0.4
Nodes (3): 动能突破 V2 — 信号过滤条件（VWAP + 换手率增强）  八条件联合碰撞：     1. 突破 20 日新高     2. 爆量 3-5 倍     3., V2 八条件向量化碰撞（在 V1 基础上增加 VWAP 偏离 + 换手率）。      参数:         merged: 快照与阈值合并后的 DataFr, v2_1_signal_filter()

### Community 28 - "Community 28"
Cohesion: 0.4
Nodes (3): 动能突破 V2 — 信号过滤条件（VWAP + 换手率增强）  八条件联合碰撞：     1. 突破 20 日新高     2. 爆量 3-5 倍     3., V2 八条件向量化碰撞（在 V1 基础上增加 VWAP 偏离 + 换手率）。      参数:         merged: 快照与阈值合并后的 DataFr, v2_signal_filter()

### Community 29 - "Community 29"
Cohesion: 0.4
Nodes (3): 动能突破 V3 — 信号过滤条件（ATR 自适应突破）  七条件联合碰撞：     1. 突破 20 日新高     2. 爆量 3-5 倍     3. AT, V3 七条件向量化碰撞（ATR 自适应替代固定涨幅门槛）。      参数:         merged: 快照与阈值合并后的 DataFrame, v3_1_signal_filter()

### Community 30 - "Community 30"
Cohesion: 0.4
Nodes (3): 动能突破 V3 — 信号过滤条件（ATR 自适应突破）  七条件联合碰撞：     1. 突破 20 日新高     2. 爆量 3-5 倍     3. AT, V3 七条件向量化碰撞（ATR 自适应替代固定涨幅门槛）。      参数:         merged: 快照与阈值合并后的 DataFrame, v3_signal_filter()

### Community 31 - "Community 31"
Cohesion: 0.7
Nodes (4): fetch_all_stock_codes(), fetch_profit_data(), main(), safe_float()

### Community 32 - "Community 32"
Cohesion: 0.7
Nodes (4): get_target_quarters(), main(), quarter_to_stat_date(), safe_float()

### Community 33 - "Community 33"
Cohesion: 0.4
Nodes (0):

### Community 34 - "Community 34"
Cohesion: 0.4
Nodes (0):

### Community 35 - "Community 35"
Cohesion: 0.4
Nodes (0):

### Community 36 - "Community 36"
Cohesion: 0.4
Nodes (0):

### Community 37 - "Community 37"
Cohesion: 0.4
Nodes (0):

### Community 38 - "Community 38"
Cohesion: 0.4
Nodes (0):

### Community 39 - "Community 39"
Cohesion: 0.4
Nodes (0):

### Community 40 - "Community 40"
Cohesion: 0.4
Nodes (0):

### Community 41 - "Community 41"
Cohesion: 0.4
Nodes (0):

### Community 42 - "Community 42"
Cohesion: 0.4
Nodes (0):

### Community 43 - "Community 43"
Cohesion: 0.4
Nodes (0):

### Community 44 - "Community 44"
Cohesion: 0.67
Nodes (3): enrich_industry_name(), main(), 给证监会分类名称打上东财防守板块的标签补丁

### Community 45 - "Community 45"
Cohesion: 0.5
Nodes (3): get_fundamentals(), 基本面数据获取模块  从行情接口中提取股票的基本财务指标： - PE (市盈率) - PB (市净率) - 总市值, 批量获取股票基本面数据      Args:         symbols: 股票代码列表，如 ['000001', '600000']      Retur

### Community 46 - "Community 46"
Cohesion: 0.83
Nodes (3): main(), submit_task(), wait_for_result()

### Community 47 - "Community 47"
Cohesion: 0.67
Nodes (0):

### Community 48 - "Community 48"
Cohesion: 0.67
Nodes (0):

### Community 49 - "Community 49"
Cohesion: 0.67
Nodes (0):

### Community 50 - "Community 50"
Cohesion: 0.67
Nodes (1): 融资余额增速 — 逃顶信号胜率回测 ============================================ 信号定义:   1. 获取上交所融

### Community 51 - "Community 51"
Cohesion: 0.67
Nodes (1): 新高新低比 — 顶部背离逃顶信号胜率回测 ============================================ 信号定义:   1. 计算每

### Community 52 - "Community 52"
Cohesion: 0.67
Nodes (1): 冰点广度雷达 — 核心计算引擎 从当前时间往前推算，定位 60/120/250 日均线下市场广度首次跌破 10% 的触发日， 计算买入后持有 3 个月 / 6

### Community 53 - "Community 53"
Cohesion: 0.67
Nodes (1): 广度阈值多均线敏感性检验 验证"冰点买入/狂热撤退"结论是否跨参数稳健 同时测试 60日(季线)、120日(半年线)、250日(年线)

### Community 54 - "Community 54"
Cohesion: 0.67
Nodes (1): 市场广度阈值科学校准器 用历史数据验证：广度 = X% 时，未来收益如何？ 找出"最佳建仓广度"和"最佳撤退广度"的物理临界值

### Community 55 - "Community 55"
Cohesion: 0.67
Nodes (1): 冰点抄底 — 多持有期 × 市值分组胜率矩阵 (优化版) ================================================= 优

### Community 56 - "Community 56"
Cohesion: 0.67
Nodes (1): 冰点抄底时谁涨得最多？— 按市值分解 (修正版) ========================================== circ_mv 单位是【

### Community 57 - "Community 57"
Cohesion: 0.67
Nodes (1): 融资余额增速逃顶信号 — 参数鲁棒性测试 ======================================= 测试不同参数组合:   变化率窗口:

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (2): run_worker(), update_task()

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (2): _calc_max_drawdown(), run_decile_backtest()

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (0):

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (0):

### Community 62 - "Community 62"
Cohesion: 1.0
Nodes (0):

### Community 63 - "Community 63"
Cohesion: 1.0
Nodes (0):

### Community 64 - "Community 64"
Cohesion: 1.0
Nodes (0):

### Community 65 - "Community 65"
Cohesion: 1.0
Nodes (0):

### Community 66 - "Community 66"
Cohesion: 1.0
Nodes (0):

### Community 67 - "Community 67"
Cohesion: 1.0
Nodes (0):

### Community 68 - "Community 68"
Cohesion: 1.0
Nodes (1): 确保 Baostock 全局登录（只执行一次）

### Community 69 - "Community 69"
Cohesion: 1.0
Nodes (0):

### Community 70 - "Community 70"
Cohesion: 1.0
Nodes (0):

### Community 71 - "Community 71"
Cohesion: 1.0
Nodes (0):

### Community 72 - "Community 72"
Cohesion: 1.0
Nodes (0):

### Community 73 - "Community 73"
Cohesion: 1.0
Nodes (0):

### Community 74 - "Community 74"
Cohesion: 1.0
Nodes (0):

### Community 75 - "Community 75"
Cohesion: 1.0
Nodes (1): 该仓位的风险金额（到止损的距离 × 股数）

### Community 76 - "Community 76"
Cohesion: 1.0
Nodes (0):

### Community 77 - "Community 77"
Cohesion: 1.0
Nodes (0):

### Community 78 - "Community 78"
Cohesion: 1.0
Nodes (1): 计算当期目标持仓权重          Args:             date: 调仓日期             universe_df: 全市场截面数

## Knowledge Gaps
- **122 isolated node(s):** `回测引擎      接收策略产生的信号，模拟历史交易过程。`, `运行回测          Args:             df: 日线数据             signals: 策略生成的信号列表`, `Baostock 基本面历史数据获取器  用于突破免费 API 快照限制的“Point-In-Time”真实基本面溯源模块。 1. 提供历史上每日的 peTTM`, `确保 Baostock 全局登录（只执行一次）`, `转换 6 位数字代码为 baostock 的带市场前缀代码` (+117 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 60`** (2 nodes): `query_strategy_tasks.py`, `json_serial()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (2 nodes): `check_regime.py`, `check_regime()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (2 nodes): `fetch_baostock_balance.py`, `main()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (2 nodes): `scan_bull_markets.py`, `scan_bull_markets()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 64`** (2 nodes): `sync_all_a.py`, `main()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 65`** (2 nodes): `update_daily_data.py`, `main()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 66`** (2 nodes): `test_portfolio_click.py`, `test_portfolio_strategy_list()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 67`** (2 nodes): `test_volume_shock_observer.py`, `test_filter_volume_shock_signals_accepts_controlled_shock()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 68`** (1 nodes): `确保 Baostock 全局登录（只执行一次）`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 69`** (1 nodes): `query_db.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 70`** (1 nodes): `query_db2.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 71`** (1 nodes): `query_db3.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 72`** (1 nodes): `query_recent_tasks.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 73`** (1 nodes): `query_v2_holdings.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 74`** (1 nodes): `query_v2_trades.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 75`** (1 nodes): `该仓位的风险金额（到止损的距离 × 股数）`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 76`** (1 nodes): `scratch_test_vwap.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 77`** (1 nodes): `fetch_roa.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 78`** (1 nodes): `计算当期目标持仓权重          Args:             date: 调仓日期             universe_df: 全市场截面数`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DatabaseManager` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 5`, `Community 8`, `Community 44`, `Community 13`?**
  _High betweenness centrality (0.164) - this node is a cross-community bridge._
- **Why does `PortfolioStrategy` connect `Community 0` to `Community 2`, `Community 3`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Why does `DataFetcher` connect `Community 2` to `Community 8`, `Community 1`, `Community 3`, `Community 5`?**
  _High betweenness centrality (0.054) - this node is a cross-community bridge._
- **Are the 89 inferred relationships involving `DatabaseManager` (e.g. with `HoldingSnapshot` and `RebalanceRecord`) actually correct?**
  _`DatabaseManager` has 89 INFERRED edges - model-reasoned connections that need verification._
- **Are the 108 inferred relationships involving `PortfolioStrategy` (e.g. with `Position` and `TradeRecord`) actually correct?**
  _`PortfolioStrategy` has 108 INFERRED edges - model-reasoned connections that need verification._
- **Are the 101 inferred relationships involving `RebalanceConfig` (e.g. with `.__init__()` and `Barbell2026Strategy`) actually correct?**
  _`RebalanceConfig` has 101 INFERRED edges - model-reasoned connections that need verification._
- **Are the 42 inferred relationships involving `DataFetcher` (e.g. with `DatabaseManager` and `BaostockFetcher`) actually correct?**
  _`DataFetcher` has 42 INFERRED edges - model-reasoned connections that need verification._