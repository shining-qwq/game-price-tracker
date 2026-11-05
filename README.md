# 价格观察站（Steam / USD）

Python、Flask、SQLite、Jinja HTML 和 CSS 练习项目。支持最小注册、登录、退出和管理员身份；目标价与通知暂时仍共用原演示用户数据，尚未实现多用户隔离。内置 4 款演示游戏，可搜索加入其他 Steam 游戏，支持模拟同步与 CheapShark 动态真实同步。所有金额以整数美分保存，页面用 `$` 与 USD 表示。

## 安装、升级与启动（Windows PowerShell）

```powershell
cd E:\black_box
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app app upgrade-db
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

如果系统 Python 已安装 Flask，也可以直接使用 `python -m flask ...`。下文以 `python` 为例，请使用安装了 Flask 的同一个解释器。

`upgrade-db` 在美元数据库不存在时创建数据，已存在则以事务增加外部 ID、追踪状态和账号字段/索引，可以重复执行；不会清空已有美元数据，也不会重新开启已取消的追踪。升级失败会回滚失败事务并报错，请检查冲突，不要用 `init-db` 绕过。`init-db` 会清空并重建美元数据库，包括已有账号、真实价格、订阅和通知，只有明确需要重置时才执行。

- 模拟详情：<http://127.0.0.1:5000/games/1>（游戏 ID 1～4）
- 真实详情：<http://127.0.0.1:5000/games/101>（游戏 ID 101～104）
- 通知页：<http://127.0.0.1:5000/notifications>
- 游戏搜索：<http://127.0.0.1:5000/games/search>
- 注册：<http://127.0.0.1:5000/register>
- 登录：<http://127.0.0.1:5000/login>

详情页顶部可以切换模拟/真实来源。模拟启动和测试不需要联网或密钥。

第一栏在详情页、搜索页和通知页始终按“模拟数据 / 真实报价 / 搜索并加入游戏”排列，只切换高亮，不改变顺序。第二栏仅在模拟或真实分类中显示该分类的游戏名称；真实分类只显示正在追踪的游戏。全部取消后，真实分类仍然可以打开，并提示搜索加入游戏。

## 最小账号系统（本轮范围）

先执行 `python -m flask --app app upgrade-db`，然后重启服务，不能靠 `init-db` 清库升级。注册普通账号后登录，导航会显示用户名；管理员另显示角色标记。游戏详情、搜索和当前演示通知可以公开浏览；加入/取消追踪、保存目标价和退出须登录。注册和登录本身也有 CSRF 保护。

用户名为 3～24 位英文字母、数字或下划线，以英文字母开头，去掉首尾空格；保存原大小写用于显示，同时保存小写 `username_normalized`。数据库唯一索引使用 NOCASE，因此 `Alice` 和 `alice` 不能重复注册。密码为 8～128 个字符，不允许全部为空格，不会自动去掉密码中的空格。注册、登录页面的密码默认隐藏，可点击输入框右侧的小眼睛切换显示或隐藏；管理员 CLI 使用同样的长度规则，终端输入仍隐藏。

使用 [Werkzeug 官方密码工具](https://werkzeug.palletsprojects.com/en/stable/utils/#werkzeug.security.generate_password_hash)的 `generate_password_hash` 和 `check_password_hash`。数据库只保存带盐密码哈希，不保存可直接登录的明文；也不在日志、模板或 session 中输出密码。哈希不是可解密的密码密文，但泄漏后仍可能被离线猜测，不能取代强密码和其他生产防护。

### 原演示用户与旧数据

`users.id=1` 和原显示名“演示用户”保留，规范化用户名设为保留名 `demo`，角色为 `user`，密码哈希留空，因此不能登录，也没有公开默认密码。其他旧无身份字段用户使用 `legacy_原ID` 保留标识，密码哈希同样为空。已有价格、目标价的 user_id、通知和游戏记录不更改、不转移给新注册用户。

本轮 **只建立身份体系**：`DEMO_USER_ID=1` 仍用于原目标价及通知，普通账号登录后修改的仍是共享演示数据，不是自己的私有订阅。追踪列表也仍共用。页面有明确说明；不要把本轮版本当作已完成数据隔离的多用户服务公开部署。下一轮再设计各用户数据归属，管理员同步页面也尚未实现。

### 创建或提升管理员

```powershell
python -m flask --app app create-admin --username my_admin
```

终端会隐藏输入密码并要求确认；不要把密码写在命令行中。命令没有 `--password` 参数，密码不会进入 shell 命令历史。用户名不存在时创建管理员，已存在时提升为管理员并设置本次输入的新密码，保留原内部 ID 与创建时间。建议使用独立管理员用户名；不要随意把保留演示账号启用为管理员。

网页注册始终固定创建 `user`，即使伪造 `role=admin`、内部 ID 等字段也无效。数据库角色只允许 `user` 或 `admin`。`security.py` 提供 `login_required` 和 `admin_required`；后者对匿名用户跳到登录、普通用户返回 403。权限每次从数据库加载，不信任浏览器提交或 session 中的角色字段；本轮只提供装饰器和测试，未新增管理员业务页面。

### SECRET_KEY 与 Cookie

本地不设置环境变量时，首次启动生成随机密钥并保存在被 Git 忽略的 `instance/session_secret.key`，以后复用，避免每次重启退出登录。也支持从 `SECRET_KEY` 环境变量读取，至少 32 个字符，优先于本地文件。换密钥会使已有 session 失效。不要打印、截图、提交该文件或真实环境配置；`.env` 文件也已排除。

本地可在当前 PowerShell 临时生成环境密钥（赋值捕获输出，不会直接显示密钥，也不把密钥字面值写进命令历史）：

```powershell
$env:SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"
python -m flask --app app run --debug
```

生产配置用 `APP_ENV=production`，必须通过部署环境注入同一份强随机 `SECRET_KEY`，缺失时拒绝启动；不能依赖本地随机文件。此配置只用于 HTTPS 部署，开启 Secure Cookie。本地 HTTP 使用默认 development 配置，Secure 关闭；所有环境均启用 HttpOnly 和 SameSite=Lax。不要在生产使用 `--debug` 或 Flask 开发服务器；本轮不提供生产部署方案。

### CSRF、跳转与退出

所有 POST/PUT/PATCH/DELETE 请求统一验证 session 中的随机令牌，表单通过公共 `_csrf.html` 提交 `csrf_token`，也支持 `X-CSRF-Token`。失败返回 400，操作不会执行；保护不依赖 Referer。添加未来管理员 POST 表单时，继续包含该公共模板即可，不设置绕过标记。

登录成功先清空旧 session，再保存用户 ID 并轮换 CSRF 令牌；每个请求加载当前用户到 `g.user`。登录后的 `next` 只允许本站安全绝对路径，外站 URL、双斜线、反斜线、控制字符和编码绕过会被忽略，回首页。退出只能 POST 且校验 CSRF，然后清空 session。多标签页中的旧表单可能因登录/退出后的令牌变化而需要刷新。

### 浏览器验收及限制

1. 连续执行两次升级，检查原详情和目标价保留；匿名仍能浏览，修改操作会要求登录。
2. 注册普通用户，重复使用不同大小写的同一用户名应被拒绝；错误密码不能登录，正确密码登录后导航显示用户名。
3. 用退出按钮退出，身份清除；刷新后再修改应要求登录。删除/篡改表单 CSRF 令牌后提交应报 400，数据不变。
4. 用隐藏密码 CLI 创建独立管理员，再登录查看管理员角色标记；权限装饰器的匿名/普通/管理员分支由离线测试验证，本轮没有管理员页面可点击。
5. 打开 `/login?next=https://example.com` 并登录，不能跳到外站。手机宽度下检查注册、登录和导航布局。

限制：没有邮箱验证、验证码、找回密码、OAuth、头像或封禁功能；也没有登录限流、MFA、服务端会话撤销/设备管理和完整审计。退出清除当前浏览器 session，不代表能撤销已泄漏的其他会话副本。角色提升改密后不会自动撤销所有旧会话。目标价/通知/追踪仍共享，不是生产级完整身份系统。

测试命令仍为 `python -m unittest discover -s tests -v`。业务回归测试使用专门的测试用户及真实 session CSRF 令牌，没有关闭防护；账号安全测试使用原始 Flask 客户端，覆盖注册、哈希、退出、管理员 CLI、CSRF、权限、Cookie、跳转和无损迁移。所有价格 API 测试继续使用离线 mock。

## 游戏名称搜索

游戏搜索页只在用户输入名称并点击“搜索”后请求 CheapShark 的游戏目录，再按候选的 Steam AppID 批量查询 Steam/USD 报价；最多 20 个候选，通常是一次目录请求加一次 Steam 报价请求，不逐款查询，也不实现输入时自动补全或全量目录抓取。空关键词不会发起请求。搜索只读取本地追踪标记，不写入价格历史或同步状态。

结果中的 `gameID` 是 CheapShark 游戏标识，`Steam AppID` 仅在数据源确实提供时显示。卡片首先以大字号显示“Steam 当前报价（USD，来源 CheapShark）”，通过 `storeID=1` 的 `salePrice` 取得。`cheapest` 保留在后方的小字号“次要参考：跨商店最低报价”中，不用于 Steam 追踪、当前价或通知。所有报价来自 CheapShark 缓存，不保证等于 Steam 此刻结账价。

目录成功但 Steam 价格请求失败时，仍保留候选结果和明确标记的次要参考，并显示“Steam 报价暂不可用”及失败原因；没有 Steam 报价或 AppID 时显示缺失提示，不用跨商店最低价冒充。明确零价显示 `$0.00`，空值不显示为零价。

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
4. 验证通过后先提交 CheapShark / Steam / USD 真实档案，然后立即自动执行一次与 `sync-prices --source cheapshark` 相同的同步服务，最后重定向详情页。自动同步读取整个有效追踪列表，也会更新其他已追踪的真实游戏；成功后新游戏直接显示采集价、历史及已记录最低价，无需再手动执行命令。首次采集只建立基线，不触发通知。
5. 搜索时会根据本地 CheapShark gameID 或 Steam AppID 标记“已追踪”，原四款只有 Steam AppID 时也能识别。点击“已追踪”以 POST 取消追踪；未追踪的游戏显示“加入追踪”。取消请求只读取已保存的稳定 Steam AppID，不需要外部 API；返回搜索结果时会重新执行原关键词查询。重新加入仍需验证，并恢复原档案，不创建副本。遇到两个外部 ID 冲突会提示而不是覆盖。

取消追踪通过 `games.is_tracked=0` 保存，保留游戏档案、价格历史、目标价和旧通知。取消后不再展示旧详情，直接访问已取消的详情地址也会转到真实报价分类：有已追踪游戏则打开按内部 ID 升序的第一款，与第二栏顺序一致；没有则显示“暂无已追踪游戏”。重新加入会恢复原内部 ID、历史和目标价，并自动同步。重复取消幂等，不会删除数据，取消本身不生成价格或通知。

也可以从“真实报价 → 游戏名”的详情页取消：游戏标题下方提供“取消追踪”按钮，以 POST 提交存储的 Steam AppID，不调用外部接口。成功后从已追踪列表移除并跳转到当前第一款已追踪游戏；最后一款取消后显示空状态。模拟详情页不显示此操作。搜索页原有取消入口继续保留，并仍返回搜索结果。

加入和价格同步是两个阶段：验证不通过则不创建档案；已验证并入库后自动同步失败，保留已追踪档案并在详情页提示“追踪已保存，但自动同步失败”，旧价格和上次成功时间保留，可稍后手动重试。若同步成功但该游戏无报价，则提示缺失，不把加入验证时的报价或跨商店最低价当成历史写入。

`sync-prices --source cheapshark` 从数据库读取所有已开启追踪、来源为 CheapShark、平台/币种为 Steam/USD 且具有有效 Steam AppID 的档案，包括搜索加入的新游戏。取消追踪后不再请求报价，写入前也会再检查追踪状态；没有可同步游戏时不发送外部请求。

升级在 `games` 中增加 `cheapshark_game_id`、`steam_app_id` 和可空的 `demo_game_id`。真实来源下，CheapShark ID 和 Steam AppID 各有一个数据库唯一索引，事务写入防止重复请求创建两个档案。原四款真实游戏保留原内部 ID 与数据，仅补齐代码中已知的 Steam AppID、显式模拟关联；原先未知的 CheapShark gameID 保持 NULL，在“加入追踪”服务端验证后补齐，不联网猜测或按标题配对。

新游戏简介和类型显示“外部数据未提供”，配色使用固定界面默认值。详情页根据存储的关联显示模拟切换；没有模拟副本的新游戏不生成对应链接，不再使用内部 ID 加减 100。

### 手动验收

- 连续运行两次升级，确认原四款真实详情、目标价、价格历史和通知仍在。
- 搜索并加入不在原四款中的 Steam 游戏，检查标题来自 API、两个外部 ID、Steam/USD 来源；自动同步成功后应直接看到当前价和首次历史。后续同步缺价则提示并保留旧价。
- 返回搜索结果，确认按钮变成“已追踪”；点击后取消，按钮恢复“加入追踪”。再次加入进入同一个详情地址，数据库仍只有一条档案。
- 在“真实报价”选择游戏，点击标题下方“取消追踪”，确认跳转到第二栏第一款已追踪游戏；最后一款取消后显示“暂无已追踪游戏”。旧详情地址刷新也不能回到已取消的界面。数据库中旧价格、目标价和通知仍保留；模拟页没有取消按钮。
- 取消前后对比旧历史、目标价与通知数量，确认未删除。重复取消不会反向开启；升级再次执行也不会重开。
- 从不同分类来回切换，确认第一栏三项顺序固定，第二栏只显示对应游戏。全部取消后真实分类有清晰空状态。
- 加入已有的星露谷等原游戏，确认进入原 101～104 档案，不生成副本。
- 没有 Steam AppID 的候选不显示加入按钮；没有 Steam 报价的游戏在后端拒绝。
- 断网后点击加入应显示错误；已有详情和价格不变。浏览器检查手机宽度和成功/失败提示。
- 搜索价格卡片先显示 Steam 报价，再显示次要跨商店参考。Steam 报价接口失败时不得用参考价顶替。
- 加入成功但自动同步失败时，详情页应保留有效档案，给出可重试提示；重复加入后同步相同快照不增加重复历史。

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

-- 自动同步成功的新游戏应有真实历史；缺失或失败时可为 0，不能伪造价格
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

只查 Steam（storeID=1）的 USD 报价，游戏范围由数据库决定。下表是初始化时内置的档案，不是同步白名单：

| 模拟 ID | 真实 ID | 游戏 | Steam AppID |
|---|---|---|---|
| 1 | 101 | 星露谷物语 | 413150 |
| 2 | 102 | 空洞骑士 | 367520 |
| 3 | 103 | 双人成行 | 1426210 |
| 4 | 104 | 潜水员戴夫 | 1868140 |

CheapShark 无需 API 密钥，因此不需要设置环境变量。每批请求发送 User-Agent，网络操作超时设为 10 秒；保持 TLS 校验，不自动重试。429 时停止后续批次，报告失败批次和 Retry-After，稍后手动重试。这里只查询本地追踪列表，没有定时采集、后台线程或全站抓取。

### 动态数据流、分批与原子性

1. `services/price_sync.py` 中 `load_sync_games` 读取有效档案，生成不可变的 `PriceSyncGame(game_id, steam_app_id, title)` 对象。无效或缺少 AppID、非 Steam/USD 的档案不参与同步；取消追踪的档案也不参与。
2. CLI 和网页加入后的自动同步均调用 `sync_real_prices`，将对象列表传给 `CheapSharkSource`；适配器不接收数据库连接，不再内置 `STEAM_GAMES` 映射。历史升级中保留原四款的元数据补齐规则，它不决定运行时同步范围。
3. 根据 [官方文档](https://apidocs.cheapshark.com/)，`deals` 支持逗号分隔的 Steam AppID 查询，结果受 `pageSize` 限制，单页最多 60 条。本实现每批最多查询 60 个 AppID，`pageSize=60`，超过 60 款依次分批。例如 65 款分成 60 + 5 两批。对同一 Steam AppID 只接受一条 Steam 报价；若响应提示额外分页或重复报价，按不完整/歧义响应使整轮失败，不会只取第一页却声称完整成功。
4. 每条响应核对 `storeID=1`、AppID 必须属于当前批、有效价格及整数源更新时间。映射按 AppID 查找对应内部 ID，不依赖响应顺序。任何一批 HTTP、网络、JSON、分页或字段校验失败，均不返回部分价格事件。
5. 仅在所有外部批次全部获取、校验成功后，服务进入一个 SQLite 写事务，一起保存整轮价格、通知和成功同步状态；SQL 写入失败则整轮回滚。外部批次失败只写来源的失败状态，不写新价格或通知，不覆盖 `last_success_at`。
6. 缺失报价不算批次失败，报告标题与 Steam AppID 并保留旧价；同一稳定快照重放仍新增 0 条，旧源快照不覆盖当前价或触发通知。首次采集只建立基线，新游戏随后跨过目标价即可按原规则触发通知。

成功时 CLI 显示总追踪数、可同步数、收到报价数、新增历史数、缺失数和通知数，并报告跳过的不适用档案。失败时明确报告整轮新增历史与通知均为 0，报价/缺失数未统计，不把失败误报成缺失。数据源请求全部成功但所有游戏都缺价时，来源成功时间可以更新，旧采集价和各自采集时间保留。

详情页显示当前采集价、仅从真实记录计算的已记录最低价、历史和来源级别上次成功同步时间；后者表示整轮来源请求成功，不意味着每款都有新报价。字段 `last_attempt_at` 是开始时间，`last_success_at` 是全部批次获取并校验成功后的时间；失败时保留原成功时间。

### 离线验证与可选真实演示

```powershell
# 全套离线测试，HTTP 使用 mock，不依赖网络
python -m unittest discover -s tests -v

# 网页加入会自动同步；下面可用于已有空档案重试或后续手动更新
python -m flask --app app sync-prices --source cheapshark
# 报价快照不变时，再次同步应新增 0 条
python -m flask --app app sync-prices --source cheapshark
# 使用详情地址末尾的内部 ID 检查该游戏的真实历史
python scripts/inspect_db.py --game-id 新游戏内部ID
```

本轮无需新增表字段，原数据库升级命令仍可重复执行，不需要 `init-db`。离线测试覆盖动态五款、原四款回归、65 款分批、第二批失败全轮不写、错 AppID/商店、缺失与零价、重放与旧快照、动态游戏通知、加入后自动同步、自动失败保留档案及 Steam 价格优先展示。联网操作仅为手工验收，不作为离线测试通过的依据。

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
