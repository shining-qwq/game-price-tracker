# 价格观察站（Steam / USD）

Python、Flask、SQLite、Jinja HTML 和 CSS 练习项目。固定演示用户，无登录；4 款游戏，支持模拟同步与 CheapShark 真实同步。所有金额以整数美分保存，页面用 `$` 与 USD 表示。

## 安装、升级与启动（Windows PowerShell）

```powershell
cd E:\black_box
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app app upgrade-db
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

如果系统 Python 已安装 Flask，也可以直接使用 `python -m flask ...`。下文以 `python` 为例，请使用安装了 Flask 的同一个解释器。

`upgrade-db` 在美元数据库不存在时创建数据，已存在则保留。`init-db` 会清空并重建美元数据库，包括已有真实价格、订阅和通知，只有需要重置时才执行。

- 模拟详情：<http://127.0.0.1:5000/games/1>（游戏 ID 1～4）
- 真实详情：<http://127.0.0.1:5000/games/101>（游戏 ID 101～104）
- 通知页：<http://127.0.0.1:5000/notifications>
- 游戏搜索：<http://127.0.0.1:5000/games/search>

详情页顶部可以切换模拟/真实来源。模拟启动和测试不需要联网或密钥。

## 游戏名称搜索

游戏搜索页只在用户输入名称并点击“搜索”后请求 CheapShark 的游戏目录；空关键词不会发起请求，也不实现输入时自动补全或全量目录抓取。搜索依赖外部网络，可能因超时、代理或 CheapShark 限流而暂时失败，但不会读取或修改本地价格数据库。

结果中的 `gameID` 是 CheapShark 游戏标识，`Steam AppID` 仅在数据源确实提供时显示。`cheapest` 被标注为“CheapShark 当前跨商店最低报价（USD）”，它可能来自非 Steam 商店，不能视为 Steam 当前价格。当前轮次只展示候选结果，尚不能把搜索结果加入本地追踪。

已知限制：CheapShark 搜索结果不保证每项都有 Steam AppID；价格统一为 USD；请求过于频繁可能收到 429；网络错误、无结果和异常响应只显示提示，不会生成游戏或价格记录。

## 人民币旧库与美元新库

旧库 `instance/price_tracker.sqlite3` 原样保留，包含原 CNY 价格、目标价和通知。新版本使用 `instance/price_tracker_usd.sqlite3`，没有对旧 CNY 数据直接换符号或做汇率换算。新的模拟美元金额是虚构演示数据，需要真实订阅时请在真实详情页重新设置美元目标价。

同一个美元库中，模拟游戏 1～4 与真实游戏 101～104 是独立档案，历史、已记录最低价、目标价和触发通知均按游戏 ID 隔离；同步状态也按来源隔离。通知列表显示各自来源。所有本地数据库都在 Git 忽略规则内。

## 任意整数模拟价格

```powershell
# price-N 的 N 表示整数美元，不是美分
python -m flask --app app sync-prices --scenario price-123
python -m flask --app app sync-prices --scenario price-0
python -m flask --app app sync-prices --scenario price-2147483647
```

N 的范围是 `0～2147483647`（C++ 常见的有符号 32 位 int 上限）。小数、负数和超界值会被拒绝。例如 `price-123` 保存为 12300 美分。金额乘以 100 后使用 SQLite 64 位 INTEGER 存储，不在 32 位整数中相乘。

每次任意整数命令默认创建一个新的模拟事件，观测时间递增。因此 `price-90 → price-128 → price-90` 可以模拟第二次达标。显式重放同一事件需复用事件 ID：

```powershell
python -m flask --app app sync-prices --scenario price-90 --event-id drop-001
python -m flask --app app sync-prices --scenario price-90 --event-id drop-001
```

第二次新增 0 条。不同观测应使用不同 ID；同一 ID 搭配不同金额会报错并回滚。事件 ID 支持 1～80 个字母、数字、下划线或短横线。

保留兼容场景：`price-drop`（默认固定 88 美元事件）、`late`（固定旧观测）、`failure`（模拟失败）和 `sequence`（固定 100、88、120、90 序列）。这些固定场景的事件可重复执行；任意金额命令只作用于模拟游戏 1。场景序列测试请从重置后的模拟基线开始，避免较旧固定时间被判为晚到事件。

## 通知验证：128 → 100 → 88 → 120 → 90

以下 `init-db` 会清空美元库，请先备份需要保留的 `instance/price_tracker_usd.sqlite3`。

```powershell
python -m flask --app app init-db
# 默认模拟游戏 1 当前 $128，目标 $100
python -m flask --app app sync-prices --scenario price-100 --event-id step-100
python -m flask --app app sync-prices --scenario price-88 --event-id step-88
python -m flask --app app sync-prices --scenario price-120 --event-id step-120
python -m flask --app app sync-prices --scenario price-90 --event-id step-90
python -m flask --app app sync-prices --scenario price-90 --event-id step-90
```

每次新增通知应为 `1、0、0、1、0`，最终通知两条，触发价格是 10000 和 9000 美分。游戏 1 历史共 9 条，重放的 90 美元事件只有一条。

目标价必须大于零、最多两位小数。固定使用用户 1，不读取表单传入的用户 ID。保存目标价只更新订阅。后续同步中，上一条有效价格高于目标价且新价格小于等于目标价才通知；持续低价不重复，涨回以上后可再次触发。首次真实采集没有上一条价格，只建立基线，不通知。

价格、通知和成功同步状态在 `BEGIN IMMEDIATE` 事务中一起提交；写入失败一起回滚。通知具有 `UNIQUE(user_id, game_id, source_event_id)` 约束，并保存触发价格与当时目标价快照。修改订阅不会改写旧通知。

## CheapShark 真实手动同步

```powershell
python -m flask --app app sync-prices --source cheapshark
```

只查 Steam（storeID=1），批量查询四款游戏的 USD 报价：

| 模拟 ID | 真实 ID | 游戏 | Steam AppID |
|---|---|---|---|
| 1 | 101 | 星露谷物语 | 413150 |
| 2 | 102 | 空洞骑士 | 367520 |
| 3 | 103 | 双人成行 | 1426210 |
| 4 | 104 | 潜水员戴夫 | 1868140 |

CheapShark 无需 API 密钥，因此不需要设置环境变量。请求发送 User-Agent，网络操作超时设为 10 秒；保持 TLS 校验，不自动重试。429 时报告 Retry-After，稍后手动重试。每次只发起一个小批量请求，没有定时采集或全站抓取。

币种统一 USD；接口不提供明确国家/地区，因此页面标为“地区未由源提供”，不假装是美国区，也不请求或混入其他商店、币种。响应游戏标识或商店不匹配、价格非法、源时间缺失或网络失败时整批不写价格，保留旧价格和上次成功同步时间。

空列表或缺少报价：跳过该游戏并报告缺失，保留此前价格；如果从未采集则显示暂无报价。明确返回 `0.00` 才记录为 0，不把缺失当免费；零价不等于已确认永久免费。全空响应可表示请求成功但没有报价，成功同步时间表示数据源请求成功，并不保证所有游戏有价格。

源没有稳定价格事件 ID，所以用“来源 + 商店 + USD + Steam AppID + lastChange + 价格”的摘要构造稳定快照 ID。相同报价快照重放不会增加历史；若来源漏掉中间价格变动，无法恢复这些未采集的变化。`observed_at` 是本项目首次采集该快照的 UTC 时间，`source_updated_at` 保留源 lastChange；更旧的源快照不覆盖当前价、不触发通知。通知的时间显示为本项目观测时间。

真实历史初始为空，已记录最低价仅从首次真实采集开始计算，不使用源提供的完整历史低价或模拟历史。显示的是源缓存报价，不保证实时成交价。

## 官方要求与实际验证记录

依据 [CheapShark 官方文档](https://apidocs.cheapshark.com/)，公开 API 可供开发者使用；如果提供购买链接须使用 CheapShark 的跳转链接，不可替换为自己的推广链接。目前页面只标注来源，不提供购买跳转。限流配额未公开，本次没有主动压测。

前一轮 CN/CNY 条件下的只读验证保留在 `docs/external-source-check-2026-11-05.json`：Stardew Valley 返回 `14.99`，Dota 2 返回空列表，免费零价仍未在线验证。当时因地区和币种不符保留模拟版；本轮已得到用户允许改为 USD，因此正式接入上述限定的真实同步。

2026-11-05 17:29（北京时间）真实同步实测：四款报价依次为 `$14.99、$14.99、$39.99、$19.99`，新增真实历史 4 条、通知 0 条。再次同步相同快照新增 0 条、通知 0 条，模拟历史保持 20 条。具体国家仍无法验证；真实零价、429 和超时分支只通过离线测试，不宣称已在线触发。

## 数据库检查与测试

```powershell
# 默认查看模拟游戏 1；所有金额为整数美分
python scripts/inspect_db.py
# 查看真实游戏 101
python scripts/inspect_db.py --game-id 101
# 重跑原始两款游戏的只读外部源探测（需联网）
python scripts/probe_cheapshark.py
# 全部离线测试
python -m unittest discover -s tests -v
```

也可在 SQLite 工具直接执行：

```sql
SELECT g.id, g.title, g.data_source, g.currency, p.price_cents, p.observed_at
FROM games g JOIN price_observations p ON p.game_id = g.id
ORDER BY g.id, p.observed_at;

SELECT n.user_id, g.title, g.data_source, n.trigger_price_cents, n.target_cents,
       n.source_event_id, n.observed_at
FROM notifications n JOIN games g ON g.id=n.game_id
ORDER BY n.observed_at;

SELECT COUNT(*) FROM price_observations
WHERE source_event_id='demo-manual-step-90'; -- 重放后仍为 1
```

## 代码入口

- `app.py`：路由、目标价表单、命令、演示初始化。
- `adapters/contracts.py`：统一价格事件格式。
- `adapters/demo_price_source.py`：任意模拟金额与固定场景。
- `adapters/cheapshark.py`：HTTP 请求、字段校验、真实快照标识。
- `services/price_sync.py`：事务、幂等、旧事件防护、跨阈值通知。
- `schema.sql` 与 `notifications.sql`：表结构和唯一约束。
- `templates/`：详情页和站内通知页。
