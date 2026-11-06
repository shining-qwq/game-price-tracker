# 离线测试与手动验收

[项目首页](../README.md) · [架构](architecture.md) · [运维](operations.md) · [测试](testing.md) · [数据源](cheapshark.md) · [图表](price-chart.md)

本文保留并按主题整理原 README 的设计说明、操作示例和验收记录。沿用的“本轮/新增”等措辞属于相应功能阶段；当前交付范围以 [验证记录](delivery-check.md) 为准。所有命令从仓库根目录执行，联网命令仅供可选人工操作；本次整理未请求真实 CheapShark API。

## 离线验证入口

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m compileall -q app.py admin_sync.py security.py migrations.py price_chart.py adapters services scripts tests
.\.venv\Scripts\python.exe scripts/check_docs.py
git diff --check
```

使用 mock 的范围包括目录搜索、Steam 报价、字段校验、缺价与零价、429、TLS/网络失败、动态分批和整轮原子性。数据库测试使用独立内存库或测试专用临时文件；不会清空日常 `instance` 中的数据库。真实探测脚本是单独的可选联网操作，不属于离线套件。

重点回归包括账号/CSRF、普通用户与管理员权限、个人追踪与两用户隔离、来源时间保护、相同采集时间的阈值跨越、事件重放、单用户通知失败重试、并发取消、无损迁移、通知已读和 SVG 边界。最新实际数量见[交付验证记录](delivery-check.md)，不把旧轮次计数当作当前总数。

## 无界面浏览器检查

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
.\.venv\Scripts\python.exe scripts/browser_smoke.py --report instance/browser-check.json
# 可选：生成新的首页实际页面截图
.\.venv\Scripts\python.exe scripts/browser_smoke.py --report instance/browser-check.json --screenshots docs/screenshots
```

Playwright 使用已有 Edge/Chrome，无须下载浏览器。默认未找到时可指定 `--browser 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'`。脚本启用只读临时 Flask 服务和临时数据库，禁止外部浏览器请求，并把 CheapShark HTTP 边界设为失败 mock；浏览游戏详情、模拟管理、空搜索、通知/管理员登录跳转、注册页。

桌面 1440×920 和手机 390×844 分别检查状态、主要控件和整页宽度，监听 `console`、`pageerror`、请求失败与 HTTP 错误，不靠忽略错误通过。`/favicon.ico` 另有显式资源探测，兼容无界面浏览器不自动请求默认图标的情况。前后数据库转储必须一致；报告不包含账号密码、请求头或 session。

本轮修复前已确认 `/favicon.ico` 返回 404，其余受检 CSS/JS 为 200；修复后基础模板引用本地 SVG，显式引用与默认路径均检查为 200。[浏览器结果 JSON](browser-check-2026-11-06.json)记录实际验收，不代表访问了真实 CheapShark。新 favicon 也有离线模板与资源回归测试。

## 仍需人工确认的边界

自动化只读检查不替代真实键盘/屏幕阅读器体验、触屏操作、实际 HTTPS 部署与多人并发使用。下面保留完整的两用户、管理员、目标价到已读、SQL 核查和历史人工验收步骤。涉及真实搜索或同步的步骤会访问外部服务，仅在你明确选择联网时操作；本轮未执行这些联网步骤。

## 两个会话验收个人追踪

1. 执行两次 `.\.venv\Scripts\python.exe -m flask --app app upgrade-db` 并重启。使用普通窗口的 A 和无痕/另一浏览器的 B；两个普通标签页共享 session，不能用于这个验证。
2. 为避开旧列表迁移，选择升级后新注册的 A、B 或一款此前没有任何人追踪的真实游戏。A 搜索加入后 A 显示“已追踪”，B 搜索相同结果仍显示“加入追踪”；B 的第二栏不显示 A 的个人游戏。
3. B 加入同一游戏后，两人详情地址相同；已有档案不会再次请求验证接口，也不会复制历史或立即重采价格。执行 `.\.venv\Scripts\python.exe -m flask --app app sync-prices --source cheapshark` 时，该游戏只查询一次。明确的联网验收可能遇到限流或失败，不代表离线测试失败。
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

## 从目标价到已读的完整演示

1. 普通窗口登录用户 A，另一浏览器或无痕会话登录管理员；同一浏览器的两个普通标签页会共用账号。A 打开自己的通知页，若有旧未读记录，可明确点击“全部标为已读”，得到未读 0 的演示基线，记录不会删除。
2. A 从模拟管理表加入空洞骑士（模拟游戏 ID 2），在详情页保存目标价 `100.00`。保存目标价本身不通知。
3. 管理员在同步控制台选择同一款游戏，用本轮未使用的事件 ID 提交 `price-128 / read01-high`，再提交 `price-90 / read01-low`。新一轮演示请更换 ID 前缀，事件 ID 仍全局唯一。
4. A 刷新自己的页面，导航应新增 1 条未读。打开通知页后，记录显示触发价 `$90.00`、当时目标价 `$100.00`、来源、时间及“未读”，数量不会因浏览而减少。
5. A 点击该条“标记已读”，跳回列表后显示已读时间，导航恢复未读 0。刷新结果页状态不变。管理员自己的个人通知不因 A 的提醒而增加。
6. 管理员再用新 ID 提交 `price-120 / read02-high` 和 `price-90 / read02-low`；A 刷新后未读恢复为 1，可用“全部标为已读”归零。保留原已读记录；重复同一价格事件不会重新变成未读。
7. 可用另一个普通用户 B 验收：A 猜到 B 的通知 ID 后 POST 标记应为 404，B 的状态不变；匿名操作进入登录流程，缺少或伪造 CSRF 返回 400。手机布局、禁用按钮及跨会话刷新需要手动检查。

直接核对数据库时使用本人的内部 ID，避免输出密码哈希：

```sql
SELECT id,game_id,trigger_price_cents,target_cents,observed_at,read_at
FROM notifications WHERE user_id=你的用户ID ORDER BY observed_at DESC,id DESC;
SELECT COUNT(*) AS unread_count FROM notifications
WHERE user_id=你的用户ID AND read_at IS NULL;
```

`.\.venv\Scripts\python.exe scripts/inspect_db.py --user-id 你的用户ID --game-id 2` 也会显示已读时间和本人未读总数；该脚本是本地数据库所有者的开发工具，不是网站权限接口。

## 两个浏览器会话验收多用户隔离

1. 运行 `.\.venv\Scripts\python.exe -m flask --app app upgrade-db` 两次并重启。普通浏览器登录用户 A，无痕窗口（或另一浏览器）登录用户 B；同一浏览器的两个普通标签页会共享 Cookie，不算两个会话。
2. 在终端为模拟游戏 1 建立本次 128 美元基线，不清库：

   ```powershell
   $demoRun = Get-Date -Format 'yyyyMMddHHmmssfff'
   .\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-128 --event-id "$demoRun-base"
   ```

3. 两边打开 <http://127.0.0.1:5000/games/1>，各自点击“加入模拟追踪”，再由 A 保存 `100.00`、B 保存 `90.00`；各自刷新应保留自己的值，保存本身不发送通知。新注册账号不会继承旧演示目标价。
4. 在保留 `$demoRun` 的同一个 PowerShell 中执行：

   ```powershell
   .\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-100 --event-id "$demoRun-100"
   .\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-88 --event-id "$demoRun-88"
   .\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-120 --event-id "$demoRun-120"
   .\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-90 --event-id "$demoRun-90"
   .\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-90 --event-id "$demoRun-90"
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

也可以 `.\.venv\Scripts\python.exe scripts/inspect_db.py --user-id 你的用户ID --game-id 1`。这是拥有本地数据库文件的开发者工具，不是网站 API，不提供网页账号级文件访问控制；网站不会把请求里的用户 ID 传给这个脚本。

## 浏览器验收和面试演示

1. 匿名打开控制台应跳转登录；普通用户登录后直接打开应为 403。管理员登录后可进入，导航有控制台入口。删除 POST 表单的 CSRF 令牌应为 400，业务数据与审计都不新增。
2. 下拉框应只有四款现有模拟游戏。可分别选择 ID 1、2、3、4，用新 ID 提交整数美元报价，核对反馈与审计里的游戏名称、ID、场景，以及共享状态表中仅目标游戏的历史数增加。比如选择空洞骑士提交 `price-29`，应保存为 2900 美分，其他三款不变。
3. 让普通用户追踪选中的某款模拟游戏（例如空洞骑士 ID 2）并设目标价 100。管理员每一步都选同一游戏，用本轮未使用的 ID 依次提交 `price-128 / show01-base`、`price-100 / show01-100`、`price-88 / show01-88`、`price-120 / show01-120`、`price-90 / show01-90`。该用户只在 100、90 两次新增通知；仅追踪其他游戏或没有追踪该游戏的人不收到这些通知。
4. 保持同一游戏再提交 `price-90 / show01-90`，应新增历史、通知均为 0；刷新结果页连审计也不新增。换价格或换游戏复用该 ID 应失败。非法/真实游戏 ID、负数、小数、科学计数法、超界及旧兼容场景都应拒绝。
5. 需要新一轮上涨再下降时，使用新 ID，例如 `price-120 / show02-up`、`price-90 / show02-down`。更换表单 ID 代表新观测，不能把新观测误当成重放。
6. 如需联网人工验收，先让至少一位用户追踪真实游戏，再点击真实同步；报价可能延迟、缺失或限流。检查旧成功时间在失败时保留，普通详情仍可浏览。联网结果不作为离线测试依据。
7. 检查两种来源统计、UTC 时间、成功/失败摘要、最近操作和手机宽度下的表格滚动。同步是同步请求，会占用一个 Web 请求处理单元；没有定时任务、邮件、WebSocket 或进度推送。

离线测试命令仍为 `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。覆盖四款选项、逐游戏更新、独立单调时间、跨游戏事件冲突、通知隔离、审计目标信息、旧 CLI/固定场景回归，以及原权限、CSRF、真实源 mock、并发、审计失败和无 shell 路径。数据库记录可在本地只读检查：

```sql
SELECT id,user_id,action_type,source,outcome,summary,created_at,finished_at
FROM admin_action_log ORDER BY id DESC LIMIT 20;
SELECT source_name,last_attempt_at,last_success_at,last_error FROM sync_status;
```

## 浏览器验收及限制

1. 连续执行两次升级，检查数据库中原详情和目标价保留；新账号只看自己的订阅，匿名仍能浏览价格，修改操作会要求登录。
2. 注册普通用户，重复使用不同大小写的同一用户名应被拒绝；错误密码不能登录，正确密码登录后导航显示用户名。
3. 用退出按钮退出，身份清除；刷新后再修改应要求登录。删除/篡改表单 CSRF 令牌后提交应报 400，数据不变。
4. 用隐藏密码 CLI 创建独立管理员，再登录查看角色标记与同步控制台；普通用户直接访问控制台应为 403。
5. 打开 `/login?next=https://example.com` 并登录，不能跳到外站。手机宽度下检查注册、登录和导航布局。

限制：没有邮箱验证、验证码、找回密码、OAuth、头像或封禁功能；也没有登录限流、MFA、服务端会话撤销/设备管理和完整审计。退出清除当前浏览器 session，不代表能撤销已泄漏的其他会话副本。角色提升改密后不会自动撤销所有旧会话。追踪、目标价和通知已按用户隔离；直接访问数据库文件的开发者不受网页权限限制，不是生产级完整身份系统。

测试命令仍为 `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。业务回归测试使用专门的测试用户及真实 session CSRF 令牌，没有关闭防护；账号安全测试使用原始 Flask 客户端，覆盖注册、哈希、退出、管理员 CLI、CSRF、权限、Cookie、跳转和无损迁移。所有价格 API 测试继续使用离线 mock。

## 手动验收

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

## 直接检查数据库

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

不安装 SQLite 工具也可用 `.\.venv\Scripts\python.exe scripts/inspect_db.py --user-id 你的用户ID --game-id 新内部ID` 检查该游戏空历史；详情页 URL 最后的数字是内部 ID，不是 CheapShark gameID。

## 通知验证：128 → 100 → 88 → 120 → 90

无需清库。先按上面的多用户步骤建立本次 128 美元模拟基线，再登录个人账号为游戏 1 保存 100 美元目标价。在同一 PowerShell 中继续：

```powershell
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-100 --event-id "$demoRun-100"
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-88 --event-id "$demoRun-88"
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-120 --event-id "$demoRun-120"
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-90 --event-id "$demoRun-90"
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-90 --event-id "$demoRun-90"
```

该账号本次新增通知应为 `1、0、0、1、0`，触发价格是 10000 和 9000 美分；全站 CLI 总数可能包含其他账号。基线之后历史新增 4 条，重放的 90 美元事件只有一条。

目标价必须大于零、最多两位小数。使用服务端当前登录用户，不读取表单传入的用户 ID。保存目标价只更新本人的订阅。后续同步中，上一条有效价格高于目标价且新价格小于等于目标价才通知；持续低价不重复，涨回以上后可再次触发。首次真实采集没有上一条价格，只建立基线，不通知。

价格、成功通知、通知失败快照和成功同步状态在 `BEGIN IMMEDIATE` 事务中一起提交；单用户失败隔离并留待重试，数据库整体写入失败则回滚。通知具有 `UNIQUE(user_id, game_id, source_event_id)` 约束，并保存触发价格与当时目标价快照。修改订阅不会改写旧通知。

## 离线验证与可选真实演示

```powershell
# 全套离线测试，HTTP 使用 mock，不依赖网络
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 新档案加入会自动同步；已有档案直接沿用价格，手动命令用于更新
.\.venv\Scripts\python.exe -m flask --app app sync-prices --source cheapshark
# 报价快照不变时，再次同步应新增 0 条
.\.venv\Scripts\python.exe -m flask --app app sync-prices --source cheapshark
# 使用详情地址末尾的内部 ID 检查该游戏的真实历史
.\.venv\Scripts\python.exe scripts/inspect_db.py --user-id 你的用户ID --game-id 新游戏内部ID
```

动态同步不依赖固定四款映射。新增个人追踪表和迁移标记表，通过 `upgrade-db` 升级，不需要 `init-db`。离线测试覆盖个人列表隔离、档案复用、100 个追踪者仅请求一次、最后取消停止采集、按收件人暂停通知与重试、迁移回滚和并发取消边界，并保留原动态五款、65 款分批、旧快照与价格通知回归。联网操作仅为手工验收，不作为离线测试依据。

币种统一 USD；接口不提供明确国家/地区，因此页面标为“地区未由源提供”，不假装是美国区，也不请求或混入其他商店、币种。响应游戏标识或商店不匹配、价格非法、源时间缺失或网络失败时整批不写价格，保留旧价格和上次成功同步时间。

空列表或缺少报价：跳过该游戏并报告缺失，保留此前价格；如果从未采集则显示暂无报价。明确返回 `0.00` 才记录为 0，不把缺失当免费；零价不等于已确认永久免费。全空响应可表示请求成功但没有报价，成功同步时间表示数据源请求成功，并不保证所有游戏有价格。

源没有稳定价格事件 ID，所以用“来源 + 商店 + USD + Steam AppID + lastChange + 价格”的摘要构造稳定快照 ID。相同报价快照重放不会增加历史；若来源漏掉中间价格变动，无法恢复这些未采集的变化。`observed_at` 是本项目首次采集该快照的 UTC 时间，`source_updated_at` 保留源 lastChange；更旧的源快照不覆盖当前价、不触发通知。通知的时间显示为本项目观测时间。

真实报价的上一有效价格和详情页当前价优先按 `source_updated_at` 选择。当新旧记录都有来源时间时，只有更大的来源时间才是新有效报价；即使本机 `observed_at` 相同或倒退，也按原阈值跨越规则判断通知。同来源时间不是新版本，即使返回了不同价格也不追加或改变基线，以免对先后不明的报价错误提醒；更旧版本同样跳过，稳定快照重放仍新增 0 条。首次有效报价只建立基线，不立即通知。

模拟事件没有来源时间，仍按 `observed_at` 严格递增判断通知；迟到模拟事件可以保留在历史中，但不覆盖当前价或触发提醒。兼容旧记录时，如果比较双方任一缺少来源时间，也回退到原采集时间规则。历史表和折线图继续按采集时间展示，不把采集时间改写成来源时间。此修复不修改数据库结构或已有记录，不补发过去漏掉的通知。

`tests/test_dynamic_sync.py` 的原动态通知测试已固定适配器采集时钟：同一采集时间下，`lastChange` 递增、价格为 `128 → 100 → 88 → 120 → 90` 时，100 美元目标的累计通知必须为 `0、1、1、1、2`；外部 HTTP 全部使用 mock。运行 `.\.venv\Scripts\python.exe -m unittest tests.test_dynamic_sync tests.test_price_event_order tests.test_price_sync -q` 可检查真实/模拟时间语义、重复与旧快照、多用户、失败重试等回归。

真实历史初始为空，已记录最低价仅从首次真实采集开始计算，不使用源提供的完整历史低价或模拟历史。显示的是源缓存报价，不保证实时成交价。

## 数据库检查与测试

```powershell
# 查看指定用户的目标价和通知；所有金额为整数美分
.\.venv\Scripts\python.exe scripts/inspect_db.py --user-id 你的用户ID
# 查看真实游戏 101
.\.venv\Scripts\python.exe scripts/inspect_db.py --user-id 你的用户ID --game-id 101
# 重跑原始两款游戏的只读外部源探测（需联网）
.\.venv\Scripts\python.exe scripts/probe_cheapshark.py
# 全部离线测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
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
