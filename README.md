# 价格观察站（Steam / USD）

Python、Flask、SQLite、Jinja HTML 和 CSS 练习项目。支持注册、登录、退出和管理员身份；追踪关系、目标价与通知按当前登录用户隔离，游戏档案和价格历史全站共享。内置 4 款演示游戏，可搜索加入其他 Steam 游戏，支持模拟同步与 CheapShark 动态真实同步。所有金额以整数美分保存，页面用 `$` 与 USD 表示。

## 安装、升级与启动（Windows PowerShell）

```powershell
cd E:\black_box
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app app upgrade-db
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

如果系统 Python 已安装 Flask，也可以直接使用 `python -m flask ...`。下文以 `python` 为例，请使用安装了 Flask 的同一个解释器。

`upgrade-db` 在美元数据库不存在时创建数据，已存在则在同一事务中升级外部 ID、账号、通知重试、个人追踪及管理员审计表，可以重复执行；不会清空已有美元数据，也不会恢复已取消的个人追踪。升级失败整轮回滚并报错，请检查冲突，不要用 `init-db` 绕过。`init-db` 会清空并重建美元数据库，包括已有账号、追踪关系、真实价格、订阅、通知和管理员审计，只有明确需要重置时才在本地终端执行。

- 模拟详情：<http://127.0.0.1:5000/games/1>（游戏 ID 1～4）
- 真实详情：<http://127.0.0.1:5000/games/101>（游戏 ID 101～104）
- 通知页：<http://127.0.0.1:5000/notifications>
- 游戏搜索：<http://127.0.0.1:5000/games/search>
- 注册：<http://127.0.0.1:5000/register>
- 登录：<http://127.0.0.1:5000/login>
- 管理员同步控制台：<http://127.0.0.1:5000/admin/sync>（需管理员身份）

详情页顶部可以切换模拟/真实来源。模拟启动和测试不需要联网或密钥。

第一栏在详情页、搜索页和通知页始终按“模拟数据 / 真实报价 / 搜索并加入游戏”排列，只切换高亮，不改变顺序。“模拟数据”始终进入 `/games?source=demo` 的完整管理表，不跳到第一款详情。第二栏只显示当前用户在该分类的个人追踪游戏；空提示按分类区分。匿名可以浏览共享详情和模拟管理表，但没有个人列表。

## 模拟游戏管理与真实游戏搜索

模拟数据固定为现有四款种子游戏（内部 ID 1～4），不新增第五款，也不通过 CheapShark 搜索生成模拟档案。打开 <http://127.0.0.1:5000/games?source=demo>，无论本人追踪了 0、1 还是 4 款，都按固定顺序展示四款游戏的名称、类型、共享模拟价格、本人追踪状态、详情和操作按钮。

- 模拟价格明确标注为模拟数据。模拟游戏档案和价格历史全站共享；本人状态只查询 `user_game_tracking`，管理员在此也只看自己的状态，不会复制游戏或历史。
- 登录用户可在管理表或详情页加入/取消模拟追踪。两个入口复用 `change_demo_tracking`，要求 POST、登录和 CSRF，所有者只来自服务端 `g.user`。重复操作幂等，取消只删除自己的关联，保留原目标价、通知、重试和全局历史。重新加入恢复关联和原目标价，不补发取消期间错过的通知。
- 全部取消后，页面提示“你暂未追踪模拟游戏，可以在下表选择固定的演示游戏。”，下方四行仍可加入；不会引导去真实搜索添加模拟游戏。管理表操作 303 跳回同一管理页，刷新不会重放。
- 模拟详情原按钮继续保留。详情加入后仍返回该详情，取消后回管理页；返回位置只接受服务器限定选项，不使用任意 URL。
- 匿名仍能看到四款游戏和共享价格，状态显示“登录后管理个人追踪”，没有可提交的追踪按钮；登录链接安全返回管理页。管理页面和所有模拟追踪操作均不请求外部 API。
- 真实游戏继续在“搜索并加入游戏”中通过 CheapShark 搜索。真实分类为空时提示“你暂未追踪真实报价游戏，可以前往‘搜索并加入游戏’。”；模拟和对应真实档案拥有不同 game_id，个人追踪关系相互独立。

本轮只调整页面流程和模拟操作的固定返回位置，不改变数据库结构、四款种子或已有价格事件，也不需要清库。管理员同步控制台、真实搜索与同步逻辑保留。

两用户浏览器验收：普通窗口登录 A，无痕或另一浏览器登录 B。A 在管理表加入四款，B 仍应全部未追踪；B 加入其中一款后，A 取消该款不影响 B。A 再取消全部，管理表仍有四行和加入按钮，可直接恢复。核对原目标价、旧通知与历史未删除；分别打开详情，按钮状态应与管理表一致。退出后四行仍公开，但不显示操作表单；真实空分类仍可进入 CheapShark 搜索。手机宽度下检查表格横向滚动。

## 个人追踪与共享档案

`games` 每款游戏仍只有一份档案，没有 `user_id`；Steam/CheapShark 外部 ID 的唯一索引继续生效，价格历史仍按全局 `game_id` 保存一份。新增 `user_game_tracking(user_id, game_id, created_at)`，以 `(user_id, game_id)` 为主键，支持按用户查询；另有 `(game_id, user_id)` 索引，支持同步查找游戏的追踪者。

A 和 B 追踪同一款游戏只会增加两条关联，不会复制游戏或历史。取消只删除操作者自己的关联，不删除目标价、通知、通知重试或全局档案。保存目标价只保存个人订阅，不自动加入追踪；尚未追踪或已取消时暂停新通知。重新加入恢复原目标价，但加入本身不补发未追踪期间错过的通知，也不重放旧历史。

### 旧数据库的一次性迁移

- 在事务中创建关联表和索引，并在 `schema_migrations` 保存 `user_game_tracking_v1` 标记。
- 首次升级将旧 `games.is_tracked=1` 的游戏分别关联到升级时所有现有用户，包括原演示用户 1 和管理员；旧标记为 0 的游戏不自动加入任何用户列表。这保留迁移前各用户看到的共享列表。尚未有旧标记的更早版本按当时全部可见的档案迁移。
- 已有用户、密码哈希、游戏档案、外部 ID、价格历史、目标价、通知、失败重试和同步状态均保留。旧 `is_tracked` 列可以留在旧库中，但已废弃：仅首次迁移读取，运行时代码不再读取或修改它；新安装的 `schema.sql` 没有这列。
- 表、回填、索引和迁移标记随升级一起提交或回滚。重复升级不重复回填，因此不会恢复之后取消的关系；迁移后新注册用户和新建管理员的个人列表从空开始。
- 迁移包括所有旧用户，因此即使网页上的 A、B 都取消，仍可能有演示用户或其他旧用户追踪；只有真实关系数降到 0 才停止真实采集。不要为制造“最后一人取消”的场景删除旧账号；可用升级后新加入的一款游戏验收。

新安装仍保留四款模拟/真实档案；演示用户只预置模拟追踪，不会替新注册用户追踪真实游戏。模拟命令是主动演示，可继续追加全局模拟历史；模拟通知和重试也必须匹配接收者自己的关联。

### 两个会话验收个人追踪

1. 执行两次 `python -m flask --app app upgrade-db` 并重启。使用普通窗口的 A 和无痕/另一浏览器的 B；两个普通标签页共享 session，不能用于这个验证。
2. 为避开旧列表迁移，选择升级后新注册的 A、B 或一款此前没有任何人追踪的真实游戏。A 搜索加入后 A 显示“已追踪”，B 搜索相同结果仍显示“加入追踪”；B 的第二栏不显示 A 的个人游戏。
3. B 加入同一游戏后，两人详情地址相同；已有档案不会再次请求验证接口，也不会复制历史或立即重采价格。执行 `python -m flask --app app sync-prices --source cheapshark` 时，该游戏只查询一次。明确的联网验收可能遇到限流或失败，不代表离线测试失败。
4. 两人分别保存目标价。A 取消后，A 列表移除此游戏，B 的按钮和列表不变，B 继续参与通知；A 的原目标价、旧通知和共享历史仍能在公开详情或个人通知中看到。真实 API 价格变化不可控，确定性的降价通知、重试及并发取消由离线测试验证。
5. B 作为最后一人取消后，本地档案和历史仍在，下一次真实同步不请求该游戏。A 直接打开旧详情地址可以浏览，并点击“加入我的追踪”恢复同一档案和原目标价；加入动作本身不产生通知。
6. A 在请求的查询参数、表单或 JSON 中伪造 B 的 `user_id`，只能改变 A 自己的关联或被拒绝；缺失 CSRF 返回 400，匿名不能加入/取消。管理员在普通页面同样只看到自己的列表。

可在本地 SQLite 工具中核对关联、共享档案和迁移标记：

```sql
SELECT u.username, g.id, g.title, ugt.created_at
FROM user_game_tracking ugt JOIN users u ON u.id=ugt.user_id
JOIN games g ON g.id=ugt.game_id ORDER BY u.id,g.id;
SELECT g.id,g.title,COUNT(ugt.user_id) AS followers FROM games g
LEFT JOIN user_game_tracking ugt ON ugt.game_id=g.id GROUP BY g.id ORDER BY g.id;
SELECT steam_app_id,COUNT(*) FROM games WHERE data_source='cheapshark'
GROUP BY steam_app_id HAVING COUNT(*) > 1; -- 非空有效 AppID 应无重复
SELECT version,applied_at FROM schema_migrations;
PRAGMA foreign_key_check; -- 应无结果
```

## 账号系统与个人数据

先执行 `python -m flask --app app upgrade-db`，然后重启服务，不能靠 `init-db` 清库升级。注册普通账号后登录，导航会显示用户名；管理员另显示角色标记。游戏详情、价格历史和搜索可以公开浏览；通知页、加入/取消追踪、保存目标价和退出须登录。匿名详情只显示登录提示，不查询到任何人的目标价。注册和登录本身也有 CSRF 保护。

用户名为 3～24 位英文字母、数字或下划线，以英文字母开头，去掉首尾空格；保存原大小写用于显示，同时保存小写 `username_normalized`。数据库唯一索引使用 NOCASE，因此 `Alice` 和 `alice` 不能重复注册。密码为 8～128 个字符，不允许全部为空格，不会自动去掉密码中的空格。注册、登录页面的密码默认隐藏，可点击输入框右侧的小眼睛切换显示或隐藏；管理员 CLI 使用同样的长度规则，终端输入仍隐藏。

使用 [Werkzeug 官方密码工具](https://werkzeug.palletsprojects.com/en/stable/utils/#werkzeug.security.generate_password_hash)的 `generate_password_hash` 和 `check_password_hash`。数据库只保存带盐密码哈希，不保存可直接登录的明文；也不在日志、模板或 session 中输出密码。哈希不是可解密的密码密文，但泄漏后仍可能被离线猜测，不能取代强密码和其他生产防护。

### 原演示用户与旧数据

`users.id=1` 和原显示名“演示用户”保留，规范化用户名设为保留名 `demo`，角色为 `user`，密码哈希留空，因此不能登录，也没有公开默认密码。其他旧无身份字段用户使用 `legacy_原ID` 保留标识，密码哈希同样为空。已有价格、目标价的 user_id、通知和游戏记录不更改、不转移给新注册用户。

`DEMO_USER_ID=1` 现在只用于初始演示数据创建。运行中的详情查询、目标价保存和通知列表全部使用服务器从 session 加载的 `g.user.id`；旧用户 1 的目标价和历史通知继续归属用户 1，不会分配给第一位注册者。旧演示订阅仍按原条件参与后台同步，但其账号默认不能登录，所以新用户网页不会看到这些通知。上轮任何人以共享演示身份保存的旧目标价无法可靠区分实际操作者，全部保留原归属，不猜测迁移。新用户从空的个人目标价列表开始，需要自行设置。

数据库保留 `price_targets` 的 `(user_id, game_id)` 主键与通知 `(user_id, game_id, source_event_id)` 唯一约束。请求中的 `user_id`、角色、内部用户 ID 均不能改变数据归属；没有按 URL 用户 ID 查询他人数据的接口。管理员在这些页面同样只看自己的目标价和通知，没有默认越权读取能力。

追踪列表、目标价和通知均按本人隔离，只有游戏档案与公开价格全站共用。管理员控制台只提供固定的手动同步和全站汇总，不改变普通页面的数据权限，也不创建共享账号。

### 两个浏览器会话验收多用户隔离

1. 运行 `python -m flask --app app upgrade-db` 两次并重启。普通浏览器登录用户 A，无痕窗口（或另一浏览器）登录用户 B；同一浏览器的两个普通标签页会共享 Cookie，不算两个会话。
2. 在终端为模拟游戏 1 建立本次 128 美元基线，不清库：

   ```powershell
   $demoRun = Get-Date -Format 'yyyyMMddHHmmssfff'
   python -m flask --app app sync-prices --scenario price-128 --event-id "$demoRun-base"
   ```

3. 两边打开 <http://127.0.0.1:5000/games/1>，各自点击“加入模拟追踪”，再由 A 保存 `100.00`、B 保存 `90.00`；各自刷新应保留自己的值，保存本身不发送通知。新注册账号不会继承旧演示目标价。
4. 在保留 `$demoRun` 的同一个 PowerShell 中执行：

   ```powershell
   python -m flask --app app sync-prices --scenario price-100 --event-id "$demoRun-100"
   python -m flask --app app sync-prices --scenario price-88 --event-id "$demoRun-88"
   python -m flask --app app sync-prices --scenario price-120 --event-id "$demoRun-120"
   python -m flask --app app sync-prices --scenario price-90 --event-id "$demoRun-90"
   python -m flask --app app sync-prices --scenario price-90 --event-id "$demoRun-90"
   ```

5. 在各自的通知页核对本次新增：A 在 `100、90` 触发，B 在 `88、90` 触发，各两条；最后重放无新增。CLI 统计所有订阅者，可能还包含旧演示用户及其他账号，不应直接拿全站总数与某个用户页面比较。若这些账号已有历史通知，只比较本次事件前后的增量。
6. A 打开 `/notifications?user_id=B的ID`、`/games/1?user_id=B的ID`，仍只能看到 A 的个人数据。用开发者工具给目标价表单添加 `user_id=B的ID` 再保存，也只能修改 A；缺失或伪造 CSRF 令牌返回 400。退出后通知页跳转登录，详情仍公开且不显示目标价输入框。
7. 可再用独立管理员会话检查：导航显示管理员标记，但目标价与通知仍为其本人。手机宽度、切换账号后的缓存表单和提示需手动检查。

以上命令会追加共享模拟价格历史，也可能通知其他符合条件的订阅者；不会修改真实报价。想重复一轮演示时，重新生成 `$demoRun`，不要为新观测复用旧事件 ID。

直接检查数据库时先查自己的内部 ID：

```sql
SELECT id, username, role FROM users ORDER BY id;
-- 将 你的用户ID 替换为上面查到的整数；不要查询或输出密码哈希。
SELECT user_id, game_id, target_cents FROM price_targets
WHERE user_id=你的用户ID ORDER BY game_id;
SELECT user_id, game_id, source_event_id, trigger_price_cents, target_cents, observed_at
FROM notifications WHERE user_id=你的用户ID ORDER BY observed_at, id;
SELECT user_id, game_id, source_event_id, attempts, error_code FROM notification_retries
WHERE user_id=你的用户ID;
```

也可以 `python scripts/inspect_db.py --user-id 你的用户ID --game-id 1`。这是拥有本地数据库文件的开发者工具，不是网站 API，不提供网页账号级文件访问控制；网站不会把请求里的用户 ID 传给这个脚本。

### 单个通知失败与重试

同步与当前请求是否登录无关，始终匹配所有符合条件的订阅。每个收件人的通知插入使用 SQLite 保存点：唯一约束冲突只跳过该用户的重复通知；单用户约束失败时保存 `notification_retries`，继续处理其他用户。失败记录保留当时的目标价、触发价格、观测时间和首次生成时间，仅保存通用错误码，不把数据库错误详情展示给其他用户。

价格、成功通知、失败记录和来源成功状态在同一外层事务提交；数据库整体错误、事务级回滚或无法保存失败记录时整轮回滚，旧价格和成功时间保留。CLI 会明确显示本轮通知失败次数及待重试数量，不能只看“同步成功”就认为全部通知成功。来源成功时间仍表示报价采集成功。网页不展示其他用户的失败详情或订阅统计。

下次成功同步时，在同一事务中重试接收者本人仍在追踪的失败通知，即使收到的价格快照全部重复也会重试；不会重新发送已存在的通知。重试使用原快照，不因目标价后来修改而变成另一个通知；新订阅不会因重放旧价格而补发通知。A 取消时只暂停 A 的失败重试，B 仍追踪时 B 的通知和重试照常；不能用“还有其他人在追踪”代替接收者本人的关联判断。重新加入本身不触发重试，后续成功同步可以恢复取消前已有的失败通知；取消期间没有为该用户生成新的失败快照。外部源失败时不尝试重试，也不修改失败记录或旧成功时间。没有定时任务、后台线程或外部消息服务。

### 创建或提升管理员

```powershell
python -m flask --app app create-admin --username my_admin
```

终端会隐藏输入密码并要求确认；不要把密码写在命令行中。命令没有 `--password` 参数，密码不会进入 shell 命令历史。用户名不存在时创建管理员，已存在时提升为管理员并设置本次输入的新密码，保留原内部 ID 与创建时间。建议使用独立管理员用户名；不要随意把保留演示账号启用为管理员。

网页注册始终固定创建 `user`，即使伪造 `role=admin`、内部 ID 等字段也无效。数据库角色只允许 `user` 或 `admin`。`security.py` 提供 `login_required` 和 `admin_required`；后者对匿名用户跳到登录、普通用户返回 403。权限每次从数据库加载，不信任浏览器提交或 session 中的角色字段。管理员登录后，导航出现“同步控制台”；普通页面的追踪、目标价和通知仍只属于本人。

## 管理员手动同步控制台

已有数据库先升级，不清库：

```powershell
python -m flask --app app upgrade-db
python -m flask --app app create-admin --username my_admin
python -m flask --app app run --debug
```

创建管理员时在终端隐藏输入密码并确认；若管理员已存在且不需要改密，不必再次运行 `create-admin`。先通过网页注册普通账号再用终端提升也可以。用户名和密码规则沿用账号系统，密钥仍由环境变量或本地受忽略的随机密钥文件提供。

登录管理员后打开 <http://127.0.0.1:5000/admin/sync>。页面显示模拟和 CheapShark 来源的最近尝试、最近成功、错误分类摘要、全站去重后的已追踪游戏数和历史记录数，以及最近 20 次管理员操作。这里只展示全站汇总，不开放按用户浏览个人目标价或通知的接口。

### 固定功能和复用路径

- 真实同步按钮：POST `/admin/sync/cheapshark`，由 `load_sync_games` 读取所有用户的追踪并集，再构造 `CheapSharkSource`，直接调用 `sync_prices`。不调用 Flask CLI，也不启动子进程。Steam/USD、超时、User-Agent、分批、限流、缺价和旧事件保护全部沿用原适配器和服务。
- 模拟同步表单：POST `/admin/sync/demo`，通过数据库生成的下拉框选择四款模拟游戏中的任意一款（ID 1～4），不能输入任意标题。后端重新验证 `game_id` 是规范正整数、属于固定四款、在库中存在且 `data_source='demo'`，不信任表单的标题、来源、用户 ID 或金额单位。
- 模拟场景只接受 `price-整数`，整数美元范围 0～2147483647；事件 ID 必填，1～80 位字母、数字、下划线或短横线。`services/sync_inputs.py` 的 `validate_demo_request`、`resolve_demo_game` 和 `prepare_demo_sync` 供 CLI 与网页共用，沿用 `validate_scenario`；网页不开放 `late`、`sequence`、`failure`、`price-drop` 兼容场景。金额乘以 100 后以整数美分保存，不使用浮点数存储。
- `DemoPriceSource(game_id=...)` 将整数场景生成到选中的游戏；`prepare_demo_sync` 只查询该游戏自己的最新观测时间，保证时间递增，不借用另一游戏的时间。未传目标 ID 时仍默认游戏 1，旧固定场景的 ID、时间和含义不变；指定其他游戏时不能调用这些旧场景。
- 所有操作复用 `admin_required` 和全局 CSRF，只有固定 POST 路由可以执行。GET 页面只读；POST 完成后 303 跳回 GET，刷新结果页不会重放操作。每次只更新选中模拟游戏的共享价格历史，不修改另外三款或任何真实 API 历史。用户追踪、目标价和通知仍按本人隔离，通知完全复用 `sync_prices`；无人追踪也允许管理员更新模拟历史，但不产生通知。
- 四款共享状态表以数据库聚合展示当前模拟价、已记录最低价、历史数量和追踪人数，不逐游戏循环查询。成功反馈和审计使用服务端确认的名称及 ID，不采用浏览器提交的元数据。

选定游戏的整数演示还会把原服务的通知重试范围限定到该游戏，避免修改游戏 2 却补发游戏 3 的旧通知。其他游戏的失败快照原样保留，可在对应游戏后续同步或真实同步中重试；真实同步和旧兼容场景保持原来的重试范围。通知阈值判断仍全部由 `sync_prices` 处理，没有在路由中重写。
- 无有效真实追踪游戏时不发外部请求，记录一次成功的“无需同步”审计；来源的尝试时间不更新，因为没有调用报价源。参数校验失败同样只记录失败审计，不改变来源同步状态。

### 双击、并发与失败

模拟事件 ID 在整个数据库中全局唯一，继续沿用 `demo-manual-事件ID` 格式，不按游戏另建命名空间。同一 ID、同一游戏、同一金额重放时新增历史和通知为 0；同一 ID 换金额或换游戏均失败，事务不写入价格。表单预填随机新 ID，双击同一表单会带上相同 ID；结果页会生成下一个建议 ID，要演示重放请重新选择原游戏并填回原 ID 和金额。不同 ID 表示新观测。

真实来源继续用稳定快照 ID 去重。重复或并发点击可能发出两次外部请求，但相同快照只保存一次；若数据源期间确实产生新快照，它属于新观测。网络阶段不占用 SQLite 写事务，写入由原 `BEGIN IMMEDIATE` 串行处理，仍会检查最后一位用户是否已取消及各接收者的追踪关系。数据库繁忙会给出稍后重试提示，不增加后台队列或线程。

API 失败返回错误分类摘要，不显示完整响应；旧价格和 `last_success_at` 保留，普通用户仍能读取原详情。通知的单用户失败继续保留重试快照，结果会明确显示失败次数和待重试数；“成功”表示本轮报价同步完成，不等于所有通知都已送达。

### 审计和明确禁止的操作

新增 `admin_action_log`，保存服务器确认的管理员 `user_id`、固定动作、固定来源、执行结果、摘要、开始和完成时间。通过鉴权和 CSRF 后，先记录“执行中”，再执行同步，最后保存成功或失败；非法参数也有失败记录。未登录、无权限和 CSRF 失败发生在动作执行前，不计为已受理管理员操作。

审计不保存请求表单原文、密码、session、CSRF、密钥、完整响应或异常正文。模拟操作在原 `summary` 中保存服务器确认的游戏 ID、名称、已验证场景/目标价格及结果计数，失败时也保留已确认部分；无法确认的 ID 或场景只记录固定错误说明，不回显恶意输入。本轮不需要改审计表或重建历史。不能在网页编辑或删除审计。审计意图无法保存时不启动同步；同步完成后结果记录失败则保留未完成意图，提示核对数据。本轮没有后台恢复任务。

网页没有 `init-db`、删除数据库、执行任意 SQL、编辑用户角色、文件操作或通用命令输入框。管理员创建/提升、数据库升级和明确的重置仍在本地终端进行。网页不使用 `os.system`、`subprocess` 或 shell 执行路径，也不接收任意命令字符串。

### 浏览器验收和面试演示

1. 匿名打开控制台应跳转登录；普通用户登录后直接打开应为 403。管理员登录后可进入，导航有控制台入口。删除 POST 表单的 CSRF 令牌应为 400，业务数据与审计都不新增。
2. 下拉框应只有四款现有模拟游戏。可分别选择 ID 1、2、3、4，用新 ID 提交整数美元报价，核对反馈与审计里的游戏名称、ID、场景，以及共享状态表中仅目标游戏的历史数增加。比如选择空洞骑士提交 `price-29`，应保存为 2900 美分，其他三款不变。
3. 让普通用户追踪选中的某款模拟游戏（例如空洞骑士 ID 2）并设目标价 100。管理员每一步都选同一游戏，用本轮未使用的 ID 依次提交 `price-128 / show01-base`、`price-100 / show01-100`、`price-88 / show01-88`、`price-120 / show01-120`、`price-90 / show01-90`。该用户只在 100、90 两次新增通知；仅追踪其他游戏或没有追踪该游戏的人不收到这些通知。
4. 保持同一游戏再提交 `price-90 / show01-90`，应新增历史、通知均为 0；刷新结果页连审计也不新增。换价格或换游戏复用该 ID 应失败。非法/真实游戏 ID、负数、小数、科学计数法、超界及旧兼容场景都应拒绝。
5. 需要新一轮上涨再下降时，使用新 ID，例如 `price-120 / show02-up`、`price-90 / show02-down`。更换表单 ID 代表新观测，不能把新观测误当成重放。
6. 如需联网人工验收，先让至少一位用户追踪真实游戏，再点击真实同步；报价可能延迟、缺失或限流。检查旧成功时间在失败时保留，普通详情仍可浏览。联网结果不作为离线测试依据。
7. 检查两种来源统计、UTC 时间、成功/失败摘要、最近操作和手机宽度下的表格滚动。同步是同步请求，会占用一个 Web 请求处理单元；没有定时任务、邮件、WebSocket 或进度推送。

离线测试命令仍为 `python -m unittest discover -s tests -v`。覆盖四款选项、逐游戏更新、独立单调时间、跨游戏事件冲突、通知隔离、审计目标信息、旧 CLI/固定场景回归，以及原权限、CSRF、真实源 mock、并发、审计失败和无 shell 路径。数据库记录可在本地只读检查：

```sql
SELECT id,user_id,action_type,source,outcome,summary,created_at,finished_at
FROM admin_action_log ORDER BY id DESC LIMIT 20;
SELECT source_name,last_attempt_at,last_success_at,last_error FROM sync_status;
```

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

1. 连续执行两次升级，检查数据库中原详情和目标价保留；新账号只看自己的订阅，匿名仍能浏览价格，修改操作会要求登录。
2. 注册普通用户，重复使用不同大小写的同一用户名应被拒绝；错误密码不能登录，正确密码登录后导航显示用户名。
3. 用退出按钮退出，身份清除；刷新后再修改应要求登录。删除/篡改表单 CSRF 令牌后提交应报 400，数据不变。
4. 用隐藏密码 CLI 创建独立管理员，再登录查看角色标记与同步控制台；普通用户直接访问控制台应为 403。
5. 打开 `/login?next=https://example.com` 并登录，不能跳到外站。手机宽度下检查注册、登录和导航布局。

限制：没有邮箱验证、验证码、找回密码、OAuth、头像或封禁功能；也没有登录限流、MFA、服务端会话撤销/设备管理和完整审计。退出清除当前浏览器 session，不代表能撤销已泄漏的其他会话副本。角色提升改密后不会自动撤销所有旧会话。追踪、目标价和通知已按用户隔离；直接访问数据库文件的开发者不受网页权限限制，不是生产级完整身份系统。

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
3. 若本地已有该 CheapShark ID 的已验证 Steam/USD 档案，只为当前用户插入关联，沿用已有价格和历史，不请求验证接口或重采价格。详情页也可提交已存档 Steam AppID 加入自己的列表。缺少有效 AppID 的本地档案仍拒绝加入。
4. 对本地尚无匹配档案的 CheapShark ID，调用官方 `games?id=...`，以 `info.title`、`info.steamAppID` 和 `deals` 中 `storeID=1` 的有效报价验证。新档案与本人关联在同一事务保存，然后自动同步各用户追踪列表的游戏并集。若通过 Steam AppID 匹配到已有旧档案则补齐外部标识，不创建副本。首次采集只建立基线，跨商店最低价不写入 Steam 历史。
5. 搜索标记由本人的关联决定，原四款只有 Steam AppID 时也能识别。点击“已追踪”以 POST 删除自己的关联；未追踪显示“加入追踪”。取消不请求外部 API，返回搜索结果时会重新查询原关键词。已有档案重新加入只恢复自己的关联，不补发旧事件或复制历史。外部 ID 冲突仍会拒绝。

取消追踪只删除 `user_game_tracking` 中本人的关联；`games.is_tracked` 已废弃。档案、价格历史、目标价、旧通知和通知重试全部保留。取消后跳转本人剩余列表的第一款或空状态；直接访问旧详情仍允许公开浏览，按钮显示“加入我的追踪”。重复取消幂等，不改变他人列表。

也可从真实详情页取消，按钮以 POST 提交 Steam AppID，所有权来自 `g.user`；模拟详情有单独的加入/取消模拟追踪按钮，不改变真实副本。每个状态修改都要求登录并验证 CSRF，匿名只能浏览和搜索。

加入和价格同步是两个阶段：验证不通过则不创建档案；已验证并入库后自动同步失败，保留已追踪档案并在详情页提示“追踪已保存，但自动同步失败”，旧价格和上次成功时间保留，可稍后手动重试。若同步成功但该游戏无报价，则提示缺失，不把加入验证时的报价或跨商店最低价当成历史写入。

`sync-prices --source cheapshark` 读取至少有一位用户追踪、来源为 CheapShark、平台/币种为 Steam/USD 且 AppID 有效的去重档案。一个游戏被 100 人追踪也只请求和保存一份价格；最后一位用户取消后才停止采集。写入前重新检查是否仍有追踪者，通知与重试则逐用户核对关联；没有可同步游戏时不发送外部请求。

升级在 `games` 中增加 `cheapshark_game_id`、`steam_app_id` 和可空的 `demo_game_id`。真实来源下，CheapShark ID 和 Steam AppID 各有一个数据库唯一索引，事务写入防止重复请求创建两个档案。原四款真实游戏保留原内部 ID 与数据，仅补齐代码中已知的 Steam AppID、显式模拟关联；原先未知的 CheapShark gameID 保持 NULL，在“加入追踪”服务端验证后补齐，不联网猜测或按标题配对。

新游戏简介和类型显示“外部数据未提供”，配色使用固定界面默认值。详情页根据存储的关联显示模拟切换；没有模拟副本的新游戏不生成对应链接，不再使用内部 ID 加减 100。

### 手动验收

- 连续运行两次升级，确认原四款真实详情、目标价、价格历史和通知仍在。
- 搜索并加入不在原四款中的 Steam 游戏，检查标题来自 API、两个外部 ID、Steam/USD 来源；自动同步成功后应直接看到当前价和首次历史。后续同步缺价则提示并保留旧价。
- 返回搜索结果，确认按钮变成“已追踪”；点击后取消，按钮恢复“加入追踪”。再次加入进入同一个详情地址，数据库仍只有一条档案。
- 在真实详情取消后，确认跳到本人下一款或空状态，不影响另一账号。旧详情地址可以继续公开浏览，按钮改为加入；旧目标价、通知、历史与重试均保留。模拟按钮只影响本人的模拟关系。
- 取消前后对比旧历史、目标价与通知数量，确认未删除。重复取消不会反向开启；升级再次执行也不会重开。
- 从不同分类来回切换，确认第一栏三项顺序固定，第二栏只显示对应游戏。全部取消后真实分类有清晰空状态。
- 加入已有的星露谷等原游戏，确认进入原 101～104 档案，不生成副本。
- 没有 Steam AppID 的候选不显示加入按钮；没有 Steam 报价的游戏在后端拒绝。
- 断网后加入尚无本地档案的游戏应提示错误；已有本地档案仍可直接加入或取消个人关系，不依赖外部 API。搜索本身仍依赖网络。浏览器检查手机宽度和提示。
- 搜索价格卡片先显示 Steam 报价，再显示次要跨商店参考。Steam 报价接口失败时不得用参考价顶替。
- 加入成功但自动同步失败时，详情页应保留有效档案，给出可重试提示；重复加入后同步相同快照不增加重复历史。

### 直接检查数据库

数据库仍为 `instance/price_tracker_usd.sqlite3`。用 SQLite 工具执行：

```sql
-- 检查真实档案及外部 ID
SELECT id, title, data_source, platform, currency,
       cheapshark_game_id, steam_app_id, demo_game_id
FROM games WHERE data_source='cheapshark' ORDER BY id;

-- 追踪查询关联表，不读取旧 is_tracked；取消后档案仍存在
SELECT user_id,game_id,created_at FROM user_game_tracking
WHERE user_id=你的用户ID ORDER BY game_id;

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

不安装 SQLite 工具也可用 `python scripts/inspect_db.py --user-id 你的用户ID --game-id 新内部ID` 检查该游戏空历史；详情页 URL 最后的数字是内部 ID，不是 CheapShark gameID。

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

省略 `--game-id` 时仍使用模拟游戏 1。可以选择其他固定模拟游戏，校验与网页一致：

```powershell
python -m flask --app app sync-prices --game-id 2 --scenario price-29 --event-id hollow-demo-29
# 同一游戏、同一金额、同一 ID 重放：新增 0 条
python -m flask --app app sync-prices --game-id 2 --scenario price-29 --event-id hollow-demo-29
```

事件 ID 全局唯一，不能把上面的 `hollow-demo-29` 改用于游戏 3，或改成另一价格。真实同步不接受 `--game-id`，只能由外部 API 取得真实报价，不能手工改真实价格。

保留兼容场景：`price-drop`（默认固定 88 美元事件）、`late`（固定旧观测）、`failure`（模拟失败）和 `sequence`（固定 100、88、120、90 序列）。这些固定场景仍只服务游戏 1，ID 和观测时间不变；为游戏 2～4 必须明确使用 `--scenario price-整数`。较旧固定时间仍按原规则受旧事件保护，不会为演示清空数据库或改写原记录。

## 通知验证：128 → 100 → 88 → 120 → 90

无需清库。先按上面的多用户步骤建立本次 128 美元模拟基线，再登录个人账号为游戏 1 保存 100 美元目标价。在同一 PowerShell 中继续：

```powershell
python -m flask --app app sync-prices --scenario price-100 --event-id "$demoRun-100"
python -m flask --app app sync-prices --scenario price-88 --event-id "$demoRun-88"
python -m flask --app app sync-prices --scenario price-120 --event-id "$demoRun-120"
python -m flask --app app sync-prices --scenario price-90 --event-id "$demoRun-90"
python -m flask --app app sync-prices --scenario price-90 --event-id "$demoRun-90"
```

该账号本次新增通知应为 `1、0、0、1、0`，触发价格是 10000 和 9000 美分；全站 CLI 总数可能包含其他账号。基线之后历史新增 4 条，重放的 90 美元事件只有一条。

目标价必须大于零、最多两位小数。使用服务端当前登录用户，不读取表单传入的用户 ID。保存目标价只更新本人的订阅。后续同步中，上一条有效价格高于目标价且新价格小于等于目标价才通知；持续低价不重复，涨回以上后可再次触发。首次真实采集没有上一条价格，只建立基线，不通知。

价格、成功通知、通知失败快照和成功同步状态在 `BEGIN IMMEDIATE` 事务中一起提交；单用户失败隔离并留待重试，数据库整体写入失败则回滚。通知具有 `UNIQUE(user_id, game_id, source_event_id)` 约束，并保存触发价格与当时目标价快照。修改订阅不会改写旧通知。

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

1. `services/price_sync.py` 中 `load_sync_games` 通过 `EXISTS(user_game_tracking)` 读取各用户追踪游戏的并集，生成不可变的 `PriceSyncGame(game_id, steam_app_id, title)`。一款只出现一次；无效或缺少 AppID、非 Steam/USD、无人追踪的真实档案不参与同步。
2. CLI 和网页加入后的自动同步均调用 `sync_real_prices`，将对象列表传给 `CheapSharkSource`；适配器不接收数据库连接，不再内置 `STEAM_GAMES` 映射。历史升级中保留原四款的元数据补齐规则，它不决定运行时同步范围。
3. 根据 [官方文档](https://apidocs.cheapshark.com/)，`deals` 支持逗号分隔的 Steam AppID 查询，结果受 `pageSize` 限制，单页最多 60 条。本实现每批最多查询 60 个 AppID，`pageSize=60`，超过 60 款依次分批。例如 65 款分成 60 + 5 两批。对同一 Steam AppID 只接受一条 Steam 报价；若响应提示额外分页或重复报价，按不完整/歧义响应使整轮失败，不会只取第一页却声称完整成功。
4. 每条响应核对 `storeID=1`、AppID 必须属于当前批、有效价格及整数源更新时间。映射按 AppID 查找对应内部 ID，不依赖响应顺序。任何一批 HTTP、网络、JSON、分页或字段校验失败，均不返回部分价格事件。
5. 仅在所有外部批次全部获取、校验成功后，服务进入一个 SQLite 写事务，一起保存整轮价格、成功通知、通知失败快照和成功同步状态；单用户通知约束失败由保存点隔离，数据库整体失败则整轮回滚。外部批次失败只写来源的失败状态，不写新价格或通知，不覆盖 `last_success_at`。
6. 缺失报价不算批次失败，报告标题与 Steam AppID 并保留旧价；同一稳定快照重放仍新增 0 条，旧源快照不覆盖当前价或触发通知。首次采集只建立基线，新游戏随后跨过目标价即可按原规则触发通知。
7. 网络请求期间不持有写事务。进入 `BEGIN IMMEDIATE` 后，逐游戏再查是否存在任何追踪关系；最后一位用户的取消若已在取价期间提交，本轮即使收到报价也不写入该游戏的新历史或通知。若 B 仍在追踪则价格照常保存，仅匹配 B 的订阅。若同步先获得写锁，取消将等到该事务结束，已经提交的历史通知不会撤销。此边界由两个独立 SQLite 连接的离线测试验证。

成功时 CLI 分别显示“用户追踪关系总数（真实）”“去重后待同步游戏数”、收到报价数、新增历史数、缺失数和通知数，并报告跳过的去重档案数。例如 100 人追踪同一游戏是 100 条关系、1 款待同步游戏，不能把关系数称为游戏数。失败时报告整轮新增历史与通知为 0，不把失败误报成缺失。取价期间最后一人取消时可能收到报价但新增历史为 0；来源成功时间仍表示请求成功，不保证每款写入。缺失时保留旧价和采集时间。

详情页显示当前采集价、仅从真实记录计算的已记录最低价、历史和来源级别上次成功同步时间；后者表示整轮来源请求成功，不意味着每款都有新报价。字段 `last_attempt_at` 是开始时间，`last_success_at` 是全部批次获取并校验成功后的时间；失败时保留原成功时间。

### 离线验证与可选真实演示

```powershell
# 全套离线测试，HTTP 使用 mock，不依赖网络
python -m unittest discover -s tests -v

# 新档案加入会自动同步；已有档案直接沿用价格，手动命令用于更新
python -m flask --app app sync-prices --source cheapshark
# 报价快照不变时，再次同步应新增 0 条
python -m flask --app app sync-prices --source cheapshark
# 使用详情地址末尾的内部 ID 检查该游戏的真实历史
python scripts/inspect_db.py --user-id 你的用户ID --game-id 新游戏内部ID
```

动态同步不依赖固定四款映射。新增个人追踪表和迁移标记表，通过 `upgrade-db` 升级，不需要 `init-db`。离线测试覆盖个人列表隔离、档案复用、100 个追踪者仅请求一次、最后取消停止采集、按收件人暂停通知与重试、迁移回滚和并发取消边界，并保留原动态五款、65 款分批、旧快照与价格通知回归。联网操作仅为手工验收，不作为离线测试依据。

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
# 查看指定用户的目标价和通知；所有金额为整数美分
python scripts/inspect_db.py --user-id 你的用户ID
# 查看真实游戏 101
python scripts/inspect_db.py --user-id 你的用户ID --game-id 101
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
WHERE n.user_id=你的用户ID ORDER BY n.observed_at;

SELECT source_event_id,COUNT(*) FROM price_observations
GROUP BY source_event_id HAVING COUNT(*) > 1; -- 应无结果
```

## 代码入口

- `app.py`：路由、目标价表单、命令、演示初始化。
- `adapters/contracts.py`：统一价格事件格式。
- `adapters/demo_price_source.py`：任意模拟金额与固定场景。
- `adapters/cheapshark.py`：HTTP 请求、字段校验、真实快照标识。
- `services/price_sync.py`：事务、幂等、旧事件防护、跨阈值通知。
- `services/game_tracking.py`：服务端验证后的游戏入库、外部 ID 去重。
- `migrations.py`：可重复执行的外部 ID、账号及通知重试升级。
- `schema.sql`、`notifications.sql` 与 `notification_retries.sql`：表结构和唯一约束。
- `templates/`：详情页和站内通知页。
