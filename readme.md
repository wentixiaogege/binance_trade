# Freqtrade 实操手册（重整版）

本文件整理了本项目可复用的操作步骤，按实际使用顺序编排。  
目标：先稳定跑通，再逐步优化，不破坏已有功能。

## 0. 基本约定

- 当前目录：`/Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot`
- 主要配置（按需选择）：
  - 现货：`user_data/config_spot.json`
  - 合约：`user_data/config_futures.json`
  - 个人实验：`beat.json` 或 `user_data/new_config.json`
- 合约交易对格式必须使用：`ETH/USDT:USDT`
- 建议先用 `--dry-run`，确认稳定后再考虑实盘

---

## 1. 环境检查

```bash
which freqtrade
freqtrade --help
freqtrade list-exchanges
freqtrade list-strategies --recursive-strategy-search
```

如上命令正常返回，说明 CLI 与策略扫描基本可用。

---

## 2. 交易对与周期查询

### 2.1 现货交易对

```bash
freqtrade list-pairs --exchange binance --quote USDT
```

### 2.2 合约交易对

```bash
freqtrade list-pairs --exchange binance --quote USDT --trading-mode futures
```

### 2.3 可用周期

```bash
freqtrade list-timeframes --exchange binance
```

---

## 3. 下载数据（推荐命令）

## 3.1 现货（单对）

```bash
freqtrade download-data \
  --config user_data/config_spot.json \
  --exchange binance \
  --pairs ETH/USDT \
  --timeframes 5m 30m 4h 1d \
  --timerange 20250301-
```

## 3.2 现货（批量，正则）

```bash
freqtrade download-data \
  --exchange binance \
  --pairs ".*/USDT" \
  --timeframes 30m \
  --timerange 20240101-
```

## 3.3 合约（推荐，批量）

```bash
freqtrade download-data \
  --exchange binance \
  --trading-mode futures \
  --config user_data/config_futures.json \
  --pairs ".*/USDT:USDT" \
  --timeframes 5m 30m 4h 1d \
  --timerange 20250101-
```

## 3.4 校验数据是否下载成功

```bash
freqtrade list-data --config user_data/config_spot.json --show-timerange
freqtrade list-data --config user_data/config_futures.json --show-timerange
```

---

## 4. 回测（Backtesting）

## 4.1 现货回测示例

```bash
freqtrade backtesting \
  --config user_data/config_spot.json \
  --pairs ETH/USDT \
  --strategy SupertrendStrategy \
  --timeframe 30m \
  --timerange 20250501-20250701
```

## 4.2 合约回测示例

```bash
freqtrade backtesting \
  --config user_data/config_futures.json \
  --pairs ETH/USDT:USDT \
  --strategy FAdxSmaStrategy \
  --timeframe 1h \
  --timerange 20250501-20250701
```

## 4.3 指定策略目录（futures 子目录）

```bash
freqtrade backtesting \
  --config beat.json \
  --strategy-path user_data/strategies/futures \
  --pairs ETH/USDT:USDT \
  --strategy VolatilitySystem \
  --timeframe 1h \
  --timerange 20260101-
```

---

## 5. 参数优化（Hyperopt）

```bash
freqtrade hyperopt \
  --config user_data/config_spot.json \
  --strategy MyAwesomeStrategy \
  --hyperopt-loss SharpeHyperOptLossDaily \
  --spaces roi stoploss trailing \
  --epochs 100 \
  --timeframe 30m \
  --timerange 20250301-20250801
```

说明：
- `epochs` 越大越慢，建议先小规模验证再放大。
- 优化前请先确认策略本身回测可运行。

---

## 6. 模拟交易（Dry Run）

```bash
freqtrade trade \
  --config user_data/config_spot.json \
  --strategy SupertrendStrategy \
  --dry-run
```

查看结果：

```bash
freqtrade show-trades --config user_data/config_spot.json
freqtrade list-orders
```

---

## 7. Web UI（可选）

```bash
freqtrade install-ui
freqtrade webserver --config user_data/config_spot.json
```

---

## 8. FreqAI（实验）

```bash
freqtrade backtesting \
  --strategy FreqaiExampleStrategy \
  --strategy-path freqtrade/templates \
  --config config_examples/config_freqai.example.json \
  --freqaimodel LightGBMRegressor \
  --timerange 20250601-20250901
```

仅用于功能验证。生产使用前需独立评估数据、特征和风险。

---

## 9. 常见问题与避坑

- **币对格式错**：合约必须 `XXX/USDT:USDT`，现货是 `XXX/USDT`
- **配置混用**：现货策略用 `config_spot.json`，合约策略用 `config_futures.json`
- **无数据回测失败**：先 `download-data`，再 `list-data --show-timerange` 检查
- **策略找不到**：用 `list-strategies --recursive-strategy-search` 或补 `--strategy-path`
- **先模拟后实盘**：不要跳过 `--dry-run`

---

## 10. 安全说明（重要）

- 不要在文档里保存任何明文密钥（API Key、Token、密码）。
- 若曾经误写到文档，请立即去对应平台旋转/作废并替换。
- 建议把密钥放到本地环境变量或未提交的私有配置文件中。

---

## 11. 一条最小可执行链路（新机器）

```bash
# 1) 检查
freqtrade list-strategies --recursive-strategy-search

# 2) 下载数据（合约）
freqtrade download-data \
  --exchange binance \
  --trading-mode futures \
  --config user_data/config_futures.json \
  --pairs ETH/USDT:USDT \
  --timeframes 1h \
  --timerange 20250101-

# 3) 回测
freqtrade backtesting \
  --config user_data/config_futures.json \
  --pairs ETH/USDT:USDT \
  --strategy FAdxSmaStrategy \
  --timeframe 1h \
  --timerange 20250501-20250701

# 4) 模拟运行
freqtrade trade \
  --config user_data/config_spot.json \
  --strategy SupertrendStrategy \
  --dry-run
```

完成以上 4 步，说明项目从数据到策略执行链路已跑通。
http://freqst.com/



freqtrade list-strategies

freqtrade download-data --exchange binance --trading-mode futures --config user_data/config_futures.json  --pairs ".*/USDT:USDT"  --timeframes 1h --timerange 20260101-

freqtrade download-data --exchange binance --trading-mode futures --config user_data/config_futures.json  --pairs ".*/USDT:USDT"  --timeframes 15m --timerange 20260101-


freqtrade download-data -c user_data/beat.json --exchange binance --timeframes 3m 15m 4h  --timerange 20260101-


freqtrade list-data -c user_data/beat.json  --pairs BTC/USDT ETH/USDT
freqtrade download-data -c user_data/beat.json --exchange binance --trading-mode spot --pairs BEAT/USDT  --timeframes 3m 15m 4h  --timerange 20260101-

freqtrade list-data  --config user_data/config_futures.json  --pairs BTC/USDT ETH/USDT

# 列出 Binance 交易所所有 USDT 报价的交易对
freqtrade list-pairs --exchange binance --quote USDT

# 列出所有交易对（不限制报价货币）
freqtrade list-pairs --exchange binance

# 将结果输出到文件
freqtrade list-pairs --exchange binance --quote USDT --print-list > pairs.txt



freqtrade list-pairs  -c beat.json --exchange binance --print-json | grep BEAT

freqtrade backtesting  -c beat.json --timerange 20260201- --timeframe 3m  --strategy SampleStrategy


freqtrade trade -c config.json --strategy Strategy003


O elliott@qiuzhi2046demac Qiuzhi2046 % export ANTHROPIC_BASE_URL="your-api-url"
export ANTHROPIC_AUTH_TOKEN="your-token-here"


# figma token removed


freqtrade download-data -c beat.json --days 90 --timeframes 3m 15m 1h 4h 1d


 freqtrade hyperopt \
  -c config.json \
  --strategy ADXTrendStrategy \
  --hyperopt-loss SharpeHyperOptLoss \
  --spaces buy sell \
  --epochs 100 \
  --timerange 20260101-


freqtrade backtesting  -c beat.json --timerange 20260201- --timeframe 3m  --strategy SampleStrategy


freqtrade trade -c config.json --strategy Strategy001



AIchemist.py
Athena.py

几十毫秒就很差。东京几毫秒。

noltion笔记本软件？


freqtrade backtesting --config beat.json  --strategy-path user_data/strategies/futures  --pairs ETH/USDT:USDT --strategy FAdxSmaStrategy  --timeframe 1h --timerange 20260101-
freqtrade backtesting --config beat.json  --strategy-path user_data/strategies/futures  --strategy VolatilitySystem  --timeframe 1h --timerange 20260101-



freqtrade list-strategies --userdir /Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot/user_data/strategies/futures
freqtrade list-strategies --strategy-path user_data/strategies/futures


freqtrade list-strategies  --recursive-strategy-search

 93  freqtrade   freqtrade trade \\n  --config user_data/config.json \\n  --strategy MyStrategy \\n  --dry-run\n--dry-run：模拟交易，不真实下单（默认建议开启）\n
   94  freqtrade   freqtrade trade \\n  --config user_data/config.json \\n  --strategy MyStrategy \\n  --dry-run\n--dry-run：模拟交易，不真实下单（默认建议开启）\n
   95  freqtrade trade \\n  --config user_data/config.json \\n  --strategy MyStrategy \\n  --dry-run
   96  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
   97  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
   98  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
   99  freqtrade download-data \\n  --exchange okx \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  100  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  101  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  102  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  103  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  167  cd /Users/wentixiaogege/PycharmProjects/freqtrade
  171  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301\n
  173  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301\n
  174  ls /Users/wentixiaogege/PycharmProjects/freqtrade/user_data/data/binance
  175  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  178  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  179  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  180  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  181  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  183  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  184  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  185  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  186  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  187  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  188  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  189  freqtrade new-config --config user_data/config.json\n
  190  freqtrade download-data --timeframes 1h
  191  freqtrade download-data --timeframes 130m
  192  freqtrade download-data --timeframes 30m
  194  freqtrade download-data --timeframes 30m
  195  freqtrade download-data --timeframes 30m --config /quants/freqtrade/user_data/config.json
  196  freqtrade download-data --timeframes 30m --config ./user_data/config.json
  197  freqtrade download-data --timeframes 30m --pairs BTC/USDT,ETH/USDT,BNB/USDT\n
  198  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT,DOGE/USDT,ETH/USDT \\n  --timeframes 1h
  199  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT,DOGE/USDT,ETH/USDT \\n  --timeframes 1h\n
  200  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  202  cd /Users/wentixiaogege/PycharmProjects/freqtrade\n
  204  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  205  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20250301
  206  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  207  freqtrade download-data --timeframes 1h
  208  freqtrade download-data --timeframes 1h --exchange binance
  209  freqtrade new-config --config user_data/config.json\n
  210  freqtrade download-data \\n  --config ./user_data/config.json \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  211  freqtrade download-data \\n  --pairs BTC/USDT,ETH/USDT,BNB/USDT\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  212  freqtrade download-data \\n  --pairs BTC/USDT,ETH/USDT,BNB/USDT \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  213  freqtrade download-data \\n  --pairs BTC/USDT \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  214  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20250301
  215  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 1h \\n  --timerange 20230101-20230301
  216  freqtrade download-data \\n  --pairs BTC/USDT,ETH/USDT,BNB/USDT \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  217  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  218  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT,ETH/USDT,DOGE/USDT \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  219  freqtrade download-data \\n  --exchange binance \\n  --pairs "BTC/USDT","ETH/USDT","DOGE/USDT" \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  220  freqtrade download-data \\n  --exchange binance \\n  --pairs "BTC/USDT,ETH/USDT,DOGE/USDT" \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  221  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT ETH/USDT DOGE/USDT \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  222  freqtrade new-config --config user_data/config.json\n
  223  freqtrade download-data \\n  --exchange binance \\n  --config ./user_data/config.json \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  224  freqtrade download-data \\n  --config ./user_data/config.json \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  225  freqtrade download-data \\n  --exchange binance \\n  --paire "BTC/USDT", "ETH/USDT", "XRP/USDT", "SOL/USDT", "DOGE/USDT", "ADA/USDT", "TRX/USDT", "AVAX/USDT", "SUI/USDT", "LTC/USDT", "TON/USDT", "HBAR/USDT", "OM/USDT", "BCH/USDT", "NEAR/USDT", "APT/USDT", "TAO/USDT", "ICP/USDT", "ETC/USDT", "S/USDT", "VET/USDT", "ALGO/USDT" \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  226  freqtrade download-data \\n  --exchange binance \\n  --pairs "BTC/USDT", "ETH/USDT", "XRP/USDT", "SOL/USDT", "DOGE/USDT", "ADA/USDT", "TRX/USDT", "AVAX/USDT", "SUI/USDT", "LTC/USDT", "TON/USDT", "HBAR/USDT", "OM/USDT", "BCH/USDT", "NEAR/USDT", "APT/USDT", "TAO/USDT", "ICP/USDT", "ETC/USDT", "S/USDT", "VET/USDT", "ALGO/USDT" \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  227  freqtrade download-data \\n  --exchange binance \\n  --pairs "BTC/USDT" "ETH/USDT" "XRP/USDT" "SOL/USDT" "DOGE/USDT" "ADA/USDT" "TRX/USDT" "AVAX/USDT" "SUI/USDT" "LTC/USDT" "TON/USDT" "HBAR/USDT" "OM/USDT" "BCH/USDT" "NEAR/USDT" "APT/USDT" "TAO/USDT" "ICP/USDT" "ETC/USDT" "S/USDT" "VET/USDT" "ALGO/USDT" \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  228  freqtrade download-data \\n  --exchange binance \\n  --pairs "BTC/USDT" "ETH/USDT" "XRP/USDT" "SOL/USDT" "DOGE/USDT" "ADA/USDT" "TRX/USDT" "AVAX/USDT" "SUI/USDT" "LTC/USDT" "TON/USDT" "HBAR/USDT" "OM/USDT" "BCH/USDT" "NEAR/USDT" "APT/USDT" "TAO/USDT" "ICP/USDT" "ETC/USDT" "VET/USDT" "ALGO/USDT" \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  229  freqtrade download-data \\n  --exchange binance \\n  --pairs  BTC/USDT ETH/USDT XRP/USDT SOL/USDT DOGE/USDT ADA/USDT TRX/USDT AVAX/USDT SUI/USDT LTC/USDT TON/USDT HBAR/USDT OM/USDT BCH/USDT NEAR/USDT APT/USDT TAO/USDT ICP/USDT ETC/USDT VET/USDT ALGO/USDT  \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  230  freqtrade download-data \\n  --exchange binance \\n  --pairs  BTC/USDT ETH/USDT XRP/USDT SOL/USDT DOGE/USDT ADA/USDT TRX/USDT ICP/USDT \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  232  freqtrade download-data\n --exchange binance\n --pairs BTC/USDT\n --timeframes 1h\n --timerange 20230101-20230301
  233  freqtrade download-data \\n --exchange binance \\n --pairs BTC/USDT \\n --timeframes 1h \\n --timerange 20230101-20230301
  234  freqtrade download-data \\n  --exchange binance \\n  --pairs BTC/USDT ETH/USDT DOGE/USDT \\n  --timeframes 30m \\n  --timerange 20220101-20250801\n
  235  freqtrade download-data \\n --exchange binance \\n --pairs BTC/USDT \\n --timeframes 1h \\n --timerange 20230101-20230301
  236  freqtrade download-data \\n  --exchange binance \\n  --pairs  BTC/USDT ETH/USDT XRP/USDT SOL/USDT DOGE/USDT ADA/USDT TRX/USDT ICP/USDT \\n  --timeframes 30m \\n  --timerange 20220101-20250801
  237  freqtrade download-data --exchange binance --pairs ".*/USDT"
  238  freqtrade new-config --config user_data/config.json\n
  239  freqtrade new-strategy --strategy AwesomeStrategy
  240  freqtrade backtesting --timerange 20220101- --timeframe 5m --pairs "BTC/USDT" --strategy AwesomeStrategy
  241  freqtrade backtesting --timerange 20220101- --timeframe 1h --pairs "BTC/USDT" --strategy AwesomeStrategy
  242  freqtrade test-pairlist
  243  freqtrade download-data --exchange binance --pairs ".*/USDT" --timeframes 4h
  244  freqtrade test-pairlist
  245  freqtrade new-config --config user_data/config.json\n
  246  freqtrade test-pairlist
  247  cd /Users/wentixiaogege/PycharmProjects/freqtrade\n
  249  freqtrade backtesting --timerange 20220101- --timeframe 5m --pairs "BTC/USDT" --strategy AwesomeStrategy
  250  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy AwesomeStrategy
  251  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy MultiMa
  252  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy Diamond
  253  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy GodStra
  254  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  267  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  270  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  275  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  276  freqtrade list-strategies\n\n
  277  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy hlhb
  278  freqtrade list-strategies\n\n
  279  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  281  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  282  freqtrade --help
  283  freqtrade -install-ui
  284  freqtrade --install-ui
  285  freqtrade install-ui
  286  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrendfreqtrade list-strategies
  287  freqtrade list-strategies
  288  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SwingHighToSky
  289  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  290  pip freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  291  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  292  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy MultiMa
  293  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SuperTrend
  294  freqtrade backtesting --timerange 20220101- --timeframe 4h --pairs "BTC/USDT" --strategy SupertrendStrategy
  295  freqtrade plot-dataframe \\n  --config user_data/config.json \\n  --strategy MyStrategy \\n  --timerange 20230101-20230201
  303  freqtrade plot-dataframe \\n  --config user_data/config.json \\n  --strategy MyStrategy \\n  --timerange 20230101-20230201
  304  freqtrade plot-dataframe \\n  --config user_data/config.json \\n  --strategy SupertrendStrategy \\n  --timerange 20230101-20230201
  305  freqtrade plot-dataframe \\n  --config user_data/config.json \\n  --strategy SupertrendStrategy \\n  --timerange 20230101-20230201\n
  306  freqtrade plot-dataframe \\n  --config user_data/config.json \\n  --strategy SupertrendStrategy \\n  --timerange 20230101-20230201
  307  freqtrade plot-dataframe \\n  --config user_data/config.json \\n  --strategy SupertrendStrategy \\n  --timerange 20230101-20230201\n
  308  freqtrade plot-dataframe \\n  --config user_data/config.json \\n  --strategy SupertrendStrategy \\n  --timerange 20230101-20230201
  309  freqtrade webserver \\n  --config user_data/config.json
  311  freqtrade download-data --exchange binance --pairs ".*/USDT" --timeframes 30m
  313  freqtrade download-data --exchange binance --pairs ".*/USDT" --timeframes 30m
  314  freqtrade webserver \\n  --config user_data/config.json
  315  freqtrade webserver \\n  --config user_data/config.json
  316  freqtrade download-data --exchange binance --pairs "ETH/USDT" --timeframes 1h
  317  freqtrade download-data --exchange binance --pairs "DOGE/USDT"  --timeframes 1h
  318  freqtrade backtesting \\n  --pairs ETH/USDT \\n  --strategy SupertrendStrategy \\n  --timeframe 1h \\n  --timerange 20250101-20250701
  319  freqtrade download-data --exchange binance --pairs "DOGE/USDT"  --timeframes 1h --timerange 20250101-
  320  freqtrade download-data --exchange binance --pairs "DOGE/USDT"  --timeframes 1h --timerange 20250101- --prepend
  321  freqtrade download-data --exchange binance --pairs "ETH/USDT"  --timeframes 1h --timerange 20250101- --prepend
  322  freqtrade backtesting \\n  --pairs ETH/USDT \\n  --strategy SupertrendStrategy \\n  --timeframe 1h \\n  --timerange 20250101-20250701
  323  freqtrade --help
  324  freqtrade backtesting \\n  --pairs ETH/USDT \\n  --strategy SupertrendStrategy \\n  --timeframe 1h \\n  --timerange 20250101-20250701
  325  freqtrade download-data --exchange binance --pairs "ETH/USDT"  --timeframes 1h --timerange 20240101- --prepend
  327  freqtrade download-data --exchange binance --pairs "ETH/USDT"  --timeframes 1h --timerange 20240101- --prepend
  328  freqtrade backtesting \\n  --pairs ETH/USDT \\n  --strategy SupertrendStrategy \\n  --timeframe 1h \\n  --timerange 20250101-20250701
  329  freqtrade webserver \\n  --config user_data/config.json
  330  freqtrade backtesting \\n  --pairs ETH/USDT \\n  --strategy SupertrendStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  331  freqtrade new-config --config user_data/new_config.json
  332  freqtrade list-strategies
  333  freqtrade backtesting \\n  --pairs ETH/USDT \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  334  freqtrade backtesting --config user_data/new_config.json\\n  --pairs ETH/USDT \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  335  freqtrade backtesting --config user_data/new_config.json\\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  336  freqtrade backtesting --config user_data/new_config.json \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  337  freqtrade backtesting --config user_data/new_config.json \\n  --pairs ETH/USDT \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  338  freqtrade download-data --exchange binance --pairs "ETH/USDT"  --timeframes 1h --timerange 20240101- --prepend
  339  freqtrade download-data --exchange binance --trading-mode futures  --pairs "ETH/USDT"  --timeframes 1h --timerange 20240101-
  340  freqtrade backtesting --config user_data/new_config.json \\n  --pairs ETH/USDT \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  341  freqtrade backtesting --config user_data/config.json \\n  --pairs ETH/USDT \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  342  freqtrade backtesting --config user_data/config.json \\n  --pairs ETH/USDT \\n  --strategy Diamond \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  343  freqtrade backtesting --config user_data/new_config.json \\n  --pairs ETH/USDT \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  344  freqtrade download-data --exchange binance --trading-mode futures  --pairs "ETH/USDT"  --timeframes 1h --timerange 20240101-
  345  freqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs "ETH/USDT"  --timeframes 1h --timerange 20240101-
  346  freqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs "ETH/USDT:USDT"  --timeframes 1h --timerange 20240101-
  347  freqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs ".*/USDT:USDT"  --timeframes 1h --timerange 20240101-
  348  freqtrade backtesting --config user_data/new_config.json \\n  --pairs ETH/USDT:USDT \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250501-20250701
  349  freqtrade backtesting --config user_data/new_config.json \\n  --pairs ETH/USDT:USDT \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250101-20250701
  350  freqtrade backtesting --config user_data/new_config.json \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250101-20250701
  351  freqtrade backtesting --config user_data/new_config.json \\n  --pairs ETH/USDT:USDT \\n  --strategy FAdxSmaStrategy \\n  --timeframe 1h \\n  --timerange 20250101-20250701
  352  freqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs ".*/USDT:USDT"  --timeframes 1h --timerange 20240101-
  353  freqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs ".*/USDT"  --timerange 20240101-
  354  freqtrade download-data --exchange binance --pairs ".*/USDT"  --timerange 20240101-
  355  freqtrade list-exchanges
  356  freqtrade list-pairs --exchange binance --quote USDT
  363  /Users/wentixiaogege/PycharmProjects/freqtrade
  364  cd /Users/wentixiaogege/PycharmProjects/freqtrade
  416  freqtrade list-pairs --exchange binance --quote USDT
  418  freqtrade list-pairs --exchange binance --quote USDT
  420  freqtrade list-pairs --exchange binance --quote USDT
  422  freqtrade list-pairs --exchange binance --quote USDT
  440  freqtrade list-pairs --exchange binance --quote USDT
  446  freqtrade list-pairs --exchange binance --quote USDT
  449  freqtrade list-pairs --exchange binance --quote USDT
  451  cd /Users/wentixiaogege/PycharmProjects/freqtrade\n
  452  freqtrade list-pairs --exchange binance --quote USDT\n
  455  which freqtrade
  457  freqtrade list-pairs --exchange binance --quote USDT\n
  459  freqtrade list-pairs --exchange binance --quote USDT\n
  461  freqtrade list-pairs --exchange binance --quote USDT\n
  467  freqtrade list-pairs --exchange binance --quote USDT\n
  470  freqtrade list-pairs --exchange binance --quote USDT\n
  472  freqtrade list-pairs --exchange binance --quote USDT\n
  474  freqtrade list-pairs --exchange binance --quote USDT\n
  476  freqtrade list-pairs --exchange binance --quote USDT\n
  478  freqtrade list-pairs --exchange binance --quote USDT\n
  479  freqtrade list-timeframes --exchange binance
  481  freqtrade download-data --exchange binance --pairs ".*/USDT"  --timerange 20240101-
  482  freqtrade download-data --exchange binance --pairs ".*/USDT"  --timerange 20240101-  -t 5m 30m 4h 1d
  483  freqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs ".*/USDT:USDT"  -t 5m 30m 4h 1d --timerange 20240101-
  485  freqtrade list-pairs --exchange binance--quote USDT  --trading-mode futures\n
  486  freqtrade list-pairs --exchange binance --quote USDT  --trading-mode futures\n
  487  freqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs ".*/USDT:USDT"  -t 5m 30m 4h 1d --timerange 20250101-
  488  \nfreqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs ".*/USDT:USDT"  -t 5m 30m 4h 1d --timerange 20250101-\n
  489  freqtrade list-pairs --exchange binance --quote USDT\n
  490  freqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs ".*/USDT:USDT"  -t 5m 30m 4h 1d --timerange 20250101-
  492  freqtrade download-data --exchange binance --trading-mode futures --config user_data/new_config.json  --pairs ".*/USDT:USDT"  -t 5m 30m 4h 1d --timerange 20250101-
  494  freqtrade backtesting --config user_data/config.json  --pairs ETH/USDT  --strategy Diamond  --timeframe 1h  --timerange 20250501-20250701
  495  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy Diamond  --timeframe 1h  --timerange 20250501-20250701\n
  496  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy Diamond  --timeframe 4h  --timerange 20250501-20250701
  497  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy Diamond  --timeframe 1h  --timerange 20250501-20250701\n
  498  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy Diamond  --timeframe 4h  --timerange 20250501-20250701
  499  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy Diamond  --timeframe 30m  --timerange 20250501-20250701
  500  freqtrade list-strategies
  501  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy SupertrendStrategy  --timeframe 30m  --timerange 20250501-20250701
  502  freqtrade backtesting --config user_data/config_futures.json  --pairs ETH/USDT:USDT  --strategy SupertrendStrategy  --timeframe 30m  --timerange 20250501-20250701
  504  freqtrade backtesting --config user_data/config_futures.json  --pairs ETH/USDT:USDT --strategy FAdxSmaStrategy  --timeframe 1h --timerange 20250501-20250701
  505  freqtrade backtesting --config user_data/config_futures.json  --pairs ETH/USDT:USDT --strategy FAdxSmaStrategy  --timeframe 30m --timerange 20250501-20250701
  506  freqtrade backtesting --config user_data/config_futures.json  --pairs ETH/USDT:USDT --strategy TrendFollowingStrategy  --timeframe 30m --timerange 20250501-20250701
  507  freqtrade backtesting --config user_data/config_futures.json  --pairs ETH/USDT:USDT --strategy FReinforcedStrategy  --timeframe 30m --timerange 20250501-20250701
  508  freqtrade backtesting --config user_data/config_futures.json  --pairs ETH/USDT:USDT --strategy FReinforcedStrategy  --timeframe 30m --timerange 20250501-20250701\n
  509  freqtrade list-strategies
  510  freqtrade backtesting --config user_data/config_futures.json  --pairs ETH/USDT:USDT --strategy FReinforcedStrategy  --timeframe 30m --timerange 20250501-20250701
  511  freqtrade backtesting --config user_data/config_futures.json  --pairs ETH/USDT:USDT --strategy FTrendFollowingStrategy  --timeframe 30m --timerange 20250501-20250701\n
  512  freqtrade backtesting --config user_data/config_futures.json  --pairs ETH/USDT:USDT --strategy FSupertrendStrategy  --timeframe 30m --timerange 20250501-20250701
  513  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy
  514  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  515  freqtrade list-data
  516  freqtrade list-pairs --exchange binance --quote USDT\n
  519  freqtrade list-pairs --exchange binance --quote USDT\n
  521  freqtrade list-data --config user_data/config_spot.json --show-timerange\n
  522  freqtrade list-pairs --config user_data/config_spot.json  --exchange binance --quote USDT\n
  524  freqtrade download-data --exchange binance --pairs ".*/USDT" --timeframes 30m
  525  freqtrade download-data --config user_data/config_spot.json --exchange binance --timeframes 30m
  526  freqtrade list-data --config user_data/config_spot.json --show-timerange\n
  527  freqtrade list-data --config user_data/config_spot.json --show-timerange --timefrrame 30m\n
  528  freqtrade list-data --config user_data/config_spot.json --show-timerange\n
  529  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  530  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  531  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --pairs ETH/USDT \\n  --dry-run
  533  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --pairs ETH/USDT \\n  --dry-run
  534  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  536  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  538  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  540  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  542  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  544  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  546  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  548  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  550  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  551  freqtrade list-pairs --config user_data/config_spot.json  --exchange binance --quote USDT\n
  552  freqtrade list-data --config user_data/config_spot.json --show-timerange\n
  553  freqtrade list-data --config user_data/config_spot.json --show-timerange | grep ETH\n
  554  freqtrade download-data --config user_data/config_spot.json --exchange binance --pairs "ETH/USDT" --timeframes 1m --timerange 20250928-\n
  555  freqtrade list-data --config user_data/config_spot.json --show-timerange | grep ETH\n
  557  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  558  freqtrade list-data --config user_data/config_spot.json --show-timerange | grep ETH\n
  559  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  562  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  565  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  566  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  567  freqtrade list-orders
  568  freqtrade show-trades
  569  freqtrade  --config user_data/config_spot.json  show-trades
  570  freqtrade show-trades  --config user_data/config_spot.json
  571  freqtrade trade \\n  --config user_data/config_spot.json \\n  --strategy SupertrendStrategy \\n  --dry-run
  573  freqtrade hyperopt --hyperopt-loss SharpeHyperOptLossDaily --spaces roi stoploss trailing --strategy MyAwesomeStrategy --config user_data/config_spot.json -e 100 --timerange 20210101-20210201\n
  574  freqtrade hyperopt --hyperopt-loss SharpeHyperOptLossDaily --spaces roi stoploss trailing --strategy MyAwesomeStrategy --config user_data/config_spot.json -e 100 --timerange 20250301-20250801
  575  freqtrade hyperopt --hyperopt-loss SharpeHyperOptLossDaily --spaces roi stoploss trailing --strategy MyAwesomeStrategy --config user_data/config_spot.json -e 100 --timerange 20250301-20250801 --timeframe 30m
  576  freqtrade trade --config config_examples/config_freqai.example.json --strategy FreqaiExampleStrategy --freqaimodel LightGBMRegressor --strategy-path freqtrade/templates\n
  578  freqtrade trade --config config_examples/config_freqai.example.json --strategy FreqaiExampleStrategy --freqaimodel LightGBMRegressor --strategy-path freqtrade/templates\n
  580  freqtrade trade --config config_examples/config_freqai.example.json --strategy FreqaiExampleStrategy --freqaimodel LightGBMRegressor --strategy-path freqtrade/templates\n
  581  freqtrade hyperopt --hyperopt-loss SharpeHyperOptLossDaily --spaces roi stoploss trailing --strategy MyAwesomeStrategy --config user_data/config_spot.json -e 100 --timerange 20250301-20250801\n
  582  freqtrade trade --config config_examples/config_freqai.example.json --strategy FreqaiExampleStrategy --freqaimodel LightGBMRegressor --strategy-path freqtrade/templates\n
  583  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250801\n
  584  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250301-20250801\n
  585  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250801\n
  586  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250901\n
  589  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250901\n
  591  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250901\n
  592  freqtrade download-data --exchange binance  --timerange 20250301-20250801  --config config_examples/config_freqai.example.json\n
  593  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250901\n
  594  freqtrade download-data --exchange binance  --timerange 20250301-20250801  --pairs ALGO/USDT:USDT --config config_examples/config_freqai.example.json\n
  595  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250901\n
  596  freqtrade download-data --exchange binance  --trading-mode futures --timerange 20250301-20250801 --config config_examples/config_freqai.example.json\n
  597  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250901\n
  598  freqtrade download-data --exchange binance  --trading-mode futures --timerange 20250301-20250801 --pairs ALGO/USDT:USDT --config config_examples/config_freqai.example.json\n
  599  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250901\n
  600  freqtrade download-data --exchange binance --trading-mode futures --config config_examples/config_freqai.example.json  -t 5m 30m 4h 1d --timerange 20250101-\n
  601  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250901\n
  602  freqtrade download-data --exchange binance --trading-mode futures --config config_examples/config_freqai.example.json  -t 3m --timerange 20250101-\n
  603  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250901\n
  604  freqtrade backtesting --strategy FreqaiExampleStrategy --strategy-path freqtrade/templates --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor --timerange 20250601-20250801\n
  605  freqtrade trade --strategy FreqaiExampleStrategy --config config_freqai.example.json --freqaimodel LightGBMRegressor\n
  606  freqtrade trade --strategy FreqaiExampleStrategy config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor\n
  607  freqtrade trade --strategy FreqaiExampleStrategy --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor\n
  608  freqtrade trade --strategy FreqaiExampleStrategy --strategy-path freqtrade/template --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor\n
  609  freqtrade trade --strategy FreqaiExampleStrategy --config config_examples/config_freqai.example.json --freqaimodel LightGBMRegressor\n
  611  freqtrade trade  --config user_data/config_spot.json --strategy SupertrendStrategy --dry-run
  612  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy SupertrendStrategy  --timeframe 1m  --timerange 20250801-20251001\n
  613  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy SupertrendStrategy  --timeframe 5m  --timerange 20250801-20251001\n
  614  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy SupertrendStrategy  --timeframe 5m  --timerange 20250301-20251001\n
  615  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy SupertrendStrategy  --timeframe 4h  --timerange 20250301-20251001\n
  616  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy SupertrendStrategy  --timeframe 4h  --timerange 20250801-20251001\n
  617  freqtrade create-userdir --userdir user_cp01
  618  freqtrade -h
  619  freqtrade install-ui
  620  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy ElliotV5_SMA  --timeframe 5m  --timerange 20250801-\n
  621  freqtrade backtesting --config user_data/config_spot.json  --pairs ETH/USDT  --strategy ElliotV5_SMA  --timeframe 5m  --timerange 20250301-\n
  622  freqtrade backtesting --config user_data/config_spot.json  --strategy ElliotV5_SMA  --timeframe 5m  --timerange 20250301-\n