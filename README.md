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

`upgrade-db` 在美元数据库不存在时创建数据，已存在则以事务增加外部 ID 字段、模拟关联、追踪状态和唯一索引，可以重复执行；不会清空已有美元数据，也不会重新开启已取消的追踪。升级失败会回滚并报错，请检查冲突，不要用 `init-db` 绕过。`init-db` 会清空并重建美元数据库，包括已有真实价格、订阅和通知，只有需要重置时才执行。

- 模拟详情：<http://127.0.0.1:5000/games/1>（游戏 ID 1～4）
- 真实详情：<http://127.0.0.1:5000/games/101>（游戏 ID 101～104）
- 通知页：<http://127.0.0.1:5000/notifications>
- 游戏搜索：<http://127.0.0.1:5000/games/search>

详情页顶部可以切换模拟/真实来源。模拟启动和测试不需要联网或密钥。

第一栏在详情页、搜索页和通知页始终按“模拟数据 / 真实报价 / 搜索并加入游戏”排列，只切换高亮，不改变顺序。第二栏仅在模拟或真实分类中显示该分类的游戏名称；真实分类只显示正在追踪的游戏。全部取消后，真实分类仍然可以打开，并提示搜索加入游戏。

## 游戏名称搜索

游戏搜索页只在用户输入名称并点击“搜索”后请求 CheapShark 的游戏目录；空关键词不会发起请求，也不实现输入时自动补全或全量目录抓取。搜索依赖外部网络，可能因超时、代理或 CheapShark 限流而暂时失败，但不会读取或修改本地价格数据库。

结果中的 `gameID` 是 CheapShark 游戏标识，`Steam AppID` 仅在数据源确实提供时显示。`cheapest` 被标注为“CheapShark 当前跨商店最低报价（USD）”，它可能来自非 Steam 商店，不能视为 Steam 当前价格。搜索 GET 只显示结果；点击“加入追踪”才会验证并创建档案。

已知限制：CheapShark 搜索结果不保证每项都有 Steam AppID；价格统一为 USD；请求过于频繁可能收到 429；网络错误、无结果和异常响应只显示提示，不会生成游戏或价格记录。

## 把搜索结果加入追踪（第二轮）

已有数据库先执行升级，不需要清库：

```powershell
python -m flask --app app upgrade-db
# 同一命令可再次执行，已有价格、目标价和通知保留
python -m flask --app app upgrade-db
python -m flask --app app run --debug
```

1. 打开 <http://127.0.0.1:5000/games/search>，搜索例如 `Hades`，选择有 Steam AppID 的候选。
2. 点击“加入追踪”。表单以 POST 提交唯一的 `cheapshark_game_id`，不提交标题或价格。即使手动伪造其他表单字段，后端也不读取它们。
3. 后端调用官方 `games?id=...` 查询，以响应的 `info.title`、`info.steamAppID` 验证游戏，要求 `deals` 中存在 `storeID=1` 且有有效 `price` 的报价。其他商店报价、搜索结果 `cheapest` 和历史低价均不用于加入验证或写入 Steam 价格。
4. 验证通过后创建 CheapShark / Steam / USD 真实档案并重定向详情页。新档案没有价格历史、不生成目标价或通知，页面显示“暂无已记录报价”。验证得到的 Steam 价格本轮不保存，避免把加入操作伪装成同步。
5. 搜索时会根据本地 CheapShark gameID 或 Steam AppID 标记“已追踪”，原四款只有 Steam AppID 时也能识别。点击“已追踪”以 POST 取消追踪；未追踪的游戏显示“加入追踪”。取消请求只读取已保存的稳定 Steam AppID，不需要外部 API；返回搜索结果时会重新执行原关键词查询。重新加入仍需验证，并恢复原档案，不创建副本。遇到两个外部 ID 冲突会提示而不是覆盖。

取消追踪通过 `games.is_tracked=0` 保存，保留游戏档案、价格历史、目标价和旧通知；直接访问旧详情仍可查看，但明确标注暂停状态。恢复后使用原内部 ID、历史和目标价。重复取消幂等，不会删除数据。取消与重新加入只改变追踪状态，本身不生成价格或通知。

本轮没有扩展批量同步的游戏范围：`sync-prices --source cheapshark` 仍只处理原四款中开启追踪的游戏。取消的原游戏不再请求报价，全部取消时不发送外部请求；已发出的旧请求即使返回，写入前也会再检查追踪状态，不记录暂停游戏的新价格或通知。新加入的其他游戏可以进入详情、设置目标价，但暂时没有价格采集或降价通知。这是下一轮同步改造的边界。

升级在 `games` 中增加 `cheapshark_game_id`、`steam_app_id` 和可空的 `demo_game_id`。真实来源下，CheapShark ID 和 Steam AppID 各有一个数据库唯一索引，事务写入防止重复请求创建两个档案。原四款真实游戏保留原内部 ID 与数据，仅补齐代码中已知的 Steam AppID、显式模拟关联；原先未知的 CheapShark gameID 保持 NULL，在“加入追踪”服务端验证后补齐，不联网猜测或按标题配对。

新游戏简介和类型显示“外部数据未提供”，配色使用固定界面默认值。详情页根据存储的关联显示模拟切换；没有模拟副本的新游戏不生成对应链接，不再使用内部 ID 加减 100。

### 手动验收

- 连续运行两次升级，确认原四款真实详情、目标价、价格历史和通知仍在。
- 搜索并加入不在原四款中的 Steam 游戏，检查标题来自 API、两个外部 ID、Steam/USD 来源，以及空历史提示。
- 返回搜索结果，确认按钮变成“已追踪”；点击后取消，按钮恢复“加入追踪”。再次加入进入同一个详情地址，数据库仍只有一条档案。
- 取消前后对比旧历史、目标价与通知数量，确认未删除。重复取消不会反向开启；升级再次执行也不会重开。
- 从不同分类来回切换，确认第一栏三项顺序固定，第二栏只显示对应游戏。全部取消后真实分类有清晰空状态。
- 加入已有的星露谷等原游戏，确认进入原 101～104 档案，不生成副本。
- 没有 Steam AppID 的候选不显示加入按钮；没有 Steam 报价的游戏在后端拒绝。
- 断网后点击加入应显示错误；已有详情和价格不变。浏览器检查手机宽度和成功/失败提示。

### 直接检查数据库

数据库仍为 `instance/price_tracker_usd.sqlite3`。用 SQLite 工具执行：

```sql
-- 检查真实档案及外部 ID
SELECT id, title, data_source, platform, currency,
       cheapshark_game_id, steam_app_id, demo_game_id, is_tracked
FROM games WHERE data_source='cheapshark' ORDER BY id;

-- is_tracked=1 为已追踪，0 为已取消；取消后档案仍然存在

-- 用刚加入游戏的 CheapShark ID 替换 这里的ID；重复点击后仍应为 1
SELECT COUNT(*) FROM games
WHERE data_source='cheapshark' AND cheapshark_game_id='这里的ID';

-- 新游戏应为 0 条价格，原游戏历史数量保持不变
SELECT g.id, g.title, COUNT(p.id) AS observations
FROM games g LEFT JOIN price_observations p ON p.game_id=g.id
WHERE g.data_source='cheapshark' GROUP BY g.id ORDER BY g.id;

-- 检查唯一约束及外键；foreign_key_check 应无结果
PRAGMA index_list(games);
PRAGMA foreign_key_check;
```

不安装 SQLite 工具也可用 `python scripts/inspect_db.py --game-id 新内部ID` 检查该游戏空历史；详情页 URL 最后的数字是内部 ID，不是 CheapShark gameID。

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
- `services/game_tracking.py`：服务端验证后的游戏入库、外部 ID 去重。
- `migrations.py`：可重复执行的外部 ID 字段和唯一索引升级。
- `schema.sql` 与 `notifications.sql`：表结构和唯一约束。
- `templates/`：详情页和站内通知页。
