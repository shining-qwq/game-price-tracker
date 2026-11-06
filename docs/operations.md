# 运维、环境和故障处理

[项目首页](../README.md) · [架构](architecture.md) · [运维](operations.md) · [测试](testing.md) · [数据源](cheapshark.md) · [图表](price-chart.md)

本文保留并按主题整理原 README 的设计说明、操作示例和验收记录。沿用的“本轮/新增”等措辞属于相应功能阶段；当前交付范围以 [验证记录](delivery-check.md) 为准。所有命令从仓库根目录执行，联网命令仅供可选人工操作；本次整理未请求真实 CheapShark API。

## Python 和可复现依赖

- 最低 Python 3.10：项目运行时求值的 `str | None`、`int | None` 等联合注解需要 Python 3.10；固定的 Click 8.4.1 的 `Requires-Python` 为 `>=3.10`。`removeprefix`、内置泛型等其他用法不提高此下限。源码可通过 3.10 语法检查，不等于在每个版本都跑过完整测试。
- 本轮干净环境实测：Windows 64 位 CPython 3.14.3，推荐使用同一版本复现。Flask 3.0.0、Werkzeug 3.0.1、Jinja2 3.1.6、MarkupSafe 3.0.3、itsdangerous 2.2.0、Click 8.4.1、Blinker 1.9.0、Windows Colorama 0.4.6 全部精确固定。保留既有版本，未通过依赖升级改变业务行为。
- `requirements.txt` 包含直接和间接运行依赖，避免只固定 Flask 却解析出不同 Werkzeug 等版本。运行安装和离线业务测试无需浏览器包。
- `requirements-dev.txt` 仅为实际使用的 `scripts/browser_smoke.py` 增加可选 Playwright 和它的固定间接依赖。调用已有 Edge/Chrome，不需要 `playwright install`。`scripts/capture_price_chart.py` 是标准库驱动本机浏览器截图，仍不依赖 Playwright。
- 完整交付实测和结果见 [delivery-check.md](delivery-check.md)。未把联网 API 成功当成依赖安装或离线测试成功的依据。

## 确认解释器与安装故障

```powershell
# 只检查 PATH，运行项目仍用下面的虚拟环境解释器
Get-Command python -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable); print(sys.version); print(sys.prefix != sys.base_prefix)"
.\.venv\Scripts\python.exe -m pip --version
.\.venv\Scripts\python.exe -m pip check
```

`sys.executable` 应指向当前仓库的 `.venv\Scripts\python.exe`，最后一项应为 `True`。不要因 PATH 中存在 MSYS2 Python、旧 Python 或另一个项目的解释器就混用它们。首次创建 `.venv` 必须明确指定标准 CPython；Windows 安装路径不是默认位置时，仅替换创建环境的第一条命令。

遇到 `ModuleNotFoundError: flask`，用同一个解释器安装并重新确认，不要运行裸 `pip`：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --timeout 20 --retries 1
.\.venv\Scripts\python.exe -c "import flask; from importlib.metadata import version; print(version('Flask'))"
```

若默认 PyPI 超时，可使用本轮验证过的 HTTPS 镜像，保留 TLS 校验：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple --timeout 20 --retries 1
```

此镜像只用于获取 Python 依赖，不代理 CheapShark 报价；本轮官方索引超时、镜像安装成功，不能将前者解释成 Flask 版本不存在。不通过 `--trusted-host` 或关闭证书验证掩盖问题。

## 损坏虚拟环境的安全重建

先停止开发服务器，在仓库根目录确认 `app.py` 和 `requirements.txt` 存在。下面只将 `.venv` 改名备份，不删除目录，不触及 `instance/`；确认新环境正常之前保留备份。

```powershell
if (!(Test-Path -LiteralPath .\app.py) -or !(Test-Path -LiteralPath .\requirements.txt)) { throw '请先进入项目根目录' }
if (Test-Path -LiteralPath .\.venv) {
    Rename-Item -LiteralPath .\.venv -NewName ('.venv.saved-' + (Get-Date -Format 'yyyyMMddHHmmssfff'))
}
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

`.venv.saved-*` 与 `.venv` 一样已加入忽略规则。若安装路径不同，改用已确认的 CPython 绝对路径。不要删除账号或重置数据库来修复解释器问题。

## 代理与 TLS 排错边界

```powershell
.\.venv\Scripts\python.exe -c "import sys,ssl,os; print(sys.executable); print(ssl.OPENSSL_VERSION); print({name: bool(os.environ.get(name)) for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')})"
.\.venv\Scripts\python.exe -m unittest tests.test_dynamic_sync tests.test_game_search -q
```

只输出代理是否配置，不输出可能带用户名/密码的完整代理 URL。代理端口、证书链、系统时间、代理软件的 HTTPS 转发都可能导致 TLS EOF、超时或连接失败。先确认同一解释器、代理是否仍运行及管理员提供的证书设置；需要企业 CA 时使用其正规证书配置，不能关掉 TLS 验证。

离线测试使用 mock，即使通过也只证明代码分支和事务规则，不证明代理、证书或真实 CheapShark 当前可用。外部 TLS 失败也不能直接归因于业务代码；此时旧价格和成功时间按原规则保留。确需联网诊断时才手动运行数据源探测脚本；本轮没有执行真实 API 请求。

## 临时全新环境验收

```powershell
.\.venv\Scripts\python.exe scripts/verify_delivery.py --keep --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

脚本在系统临时目录创建新虚拟环境与项目副本，不复制 `instance/`、`.git`、原虚拟环境、缓存或密钥；执行固定依赖安装、`pip check`、项目导入、两次升级、全套离线测试、编译和 3.10 语法检查。新库属于临时副本；不会对你的现有美元库或人民币库运行初始化。

默认结束后只移除本次新建的临时目录；`--keep` 保留它供检查，输出其路径。`--prepare-only` 仅安装、导入和升级，不能将它的成功宣称为完整测试通过。整个脚本由用户选择的 Python 创建新环境，因此先确保当前 `.venv` 来自推荐的标准 CPython。

## 安装、升级与启动（Windows PowerShell）

```powershell
cd E:\black_box
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app app upgrade-db
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

所有 PowerShell 命令均从仓库根目录运行并使用 `.\.venv\Scripts\python.exe`；只有首次创建虚拟环境使用已明确选择的标准 CPython，不以系统 PATH 中的 python 作为运行替代。

`upgrade-db` 在美元数据库不存在时创建数据，已存在则在同一事务中升级外部 ID、账号、通知重试、个人追踪、管理员审计和通知已读字段，可以重复执行；不会清空已有美元数据，也不会恢复已取消的个人追踪。升级失败整轮回滚并报错，请检查冲突，不要用 `init-db` 绕过。`init-db` 会清空并重建美元数据库，包括已有账号、追踪关系、真实价格、订阅、通知和管理员审计，只有明确需要重置时才在本地终端执行。

- 模拟详情：<http://127.0.0.1:5000/games/1>（游戏 ID 1～4）
- 真实详情：<http://127.0.0.1:5000/games/101>（游戏 ID 101～104）
- 通知页：<http://127.0.0.1:5000/notifications>
- 游戏搜索：<http://127.0.0.1:5000/games/search>
- 注册：<http://127.0.0.1:5000/register>
- 登录：<http://127.0.0.1:5000/login>
- 管理员同步控制台：<http://127.0.0.1:5000/admin/sync>（需管理员身份）

详情页顶部可以切换模拟/真实来源。模拟启动和测试不需要联网或密钥。

第一栏在详情页、搜索页和通知页始终按“模拟数据 / 真实报价 / 搜索并加入游戏”排列，只切换高亮，不改变顺序。“模拟数据”始终进入 `/games?source=demo` 的完整管理表，不跳到第一款详情。第二栏只显示当前用户在该分类的个人追踪游戏；空提示按分类区分。匿名可以浏览共享详情和模拟管理表，但没有个人列表。

## 创建或提升管理员

```powershell
.\.venv\Scripts\python.exe -m flask --app app create-admin --username my_admin
```

终端会隐藏输入密码并要求确认；不要把密码写在命令行中。命令没有 `--password` 参数，密码不会进入 shell 命令历史。用户名不存在时创建管理员，已存在时提升为管理员并设置本次输入的新密码，保留原内部 ID 与创建时间。建议使用独立管理员用户名；不要随意把保留演示账号启用为管理员。

网页注册始终固定创建 `user`，即使伪造 `role=admin`、内部 ID 等字段也无效。数据库角色只允许 `user` 或 `admin`。`security.py` 提供 `login_required` 和 `admin_required`；后者对匿名用户跳到登录、普通用户返回 403。权限每次从数据库加载，不信任浏览器提交或 session 中的角色字段。管理员登录后，导航出现“同步控制台”；普通页面的追踪、目标价和通知仍只属于本人。

## 管理员手动同步控制台

已有数据库先升级，不清库：

```powershell
.\.venv\Scripts\python.exe -m flask --app app upgrade-db
.\.venv\Scripts\python.exe -m flask --app app create-admin --username my_admin
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

创建管理员时在终端隐藏输入密码并确认；若管理员已存在且不需要改密，不必再次运行 `create-admin`。先通过网页注册普通账号再用终端提升也可以。用户名和密码规则沿用账号系统，密钥仍由环境变量或本地受忽略的随机密钥文件提供。

登录管理员后打开 <http://127.0.0.1:5000/admin/sync>。页面显示模拟和 CheapShark 来源的最近尝试、最近成功、错误分类摘要、全站去重后的已追踪游戏数和历史记录数，以及最近 20 次管理员操作。这里只展示全站汇总，不开放按用户浏览个人目标价或通知的接口。

## 连续操作与页面位置

模拟表单成功后仍使用 POST/Redirect/GET，例如返回 `/admin/sync?demo_game_id=3&scenario=price-90#demo-sync-panel`。目标游戏和合法场景会保留，页面定位到模拟演示区域，操作结果在该区域内显示；成功后自动生成新的随机事件 ID，不复用刚成功的事件。需要演示幂等重放时，可手动填回同一游戏、价格和原事件 ID。

失败时尽量保留独立通过验证的游戏、场景和事件 ID；合法事件 ID 通过 `retry_event_id` 参数恢复，非法 ID、控制字符或超长值不放入重定向地址。非法场景回退到 `price-128`，非法目标回退到现有第一款模拟游戏，失败提示仍保留；请确认并修正后再提交。真实同步按钮携带当前游戏和场景作为显示状态，返回 `#real-sync-panel`，不执行模拟价格修改。

这些查询参数只决定表单默认显示值；GET 会重新验证四款模拟游戏 ID、整数美元场景和事件 ID 的格式与长度，不调用同步、不新增价格或审计记录。页面选择不存入全局变量或 session，两个标签页可使用不同地址保留各自选择。操作仍必须登录、管理员权限、POST 和 CSRF。锚点使用原生浏览器定位，不增加动画、前端框架或实时请求。

## 固定功能和复用路径

- 真实同步按钮：POST `/admin/sync/cheapshark`，由 `load_sync_games` 读取所有用户的追踪并集，再构造 `CheapSharkSource`，直接调用 `sync_prices`。不调用 Flask CLI，也不启动子进程。Steam/USD、超时、User-Agent、分批、限流、缺价和旧事件保护全部沿用原适配器和服务。
- 模拟同步表单：POST `/admin/sync/demo`，通过数据库生成的下拉框选择四款模拟游戏中的任意一款（ID 1～4），不能输入任意标题。后端重新验证 `game_id` 是规范正整数、属于固定四款、在库中存在且 `data_source='demo'`，不信任表单的标题、来源、用户 ID 或金额单位。
- 模拟场景只接受 `price-整数`，整数美元范围 0～2147483647；事件 ID 必填，1～80 位字母、数字、下划线或短横线。`services/sync_inputs.py` 的 `validate_demo_request`、`resolve_demo_game` 和 `prepare_demo_sync` 供 CLI 与网页共用，沿用 `validate_scenario`；网页不开放 `late`、`sequence`、`failure`、`price-drop` 兼容场景。金额乘以 100 后以整数美分保存，不使用浮点数存储。
- `DemoPriceSource(game_id=...)` 将整数场景生成到选中的游戏；`prepare_demo_sync` 只查询该游戏自己的最新观测时间，保证时间递增，不借用另一游戏的时间。未传目标 ID 时仍默认游戏 1，旧固定场景的 ID、时间和含义不变；指定其他游戏时不能调用这些旧场景。
- 所有操作复用 `admin_required` 和全局 CSRF，只有固定 POST 路由可以执行。GET 页面只读；POST 完成后 303 跳回 GET，刷新结果页不会重放操作。每次只更新选中模拟游戏的共享价格历史，不修改另外三款或任何真实 API 历史。用户追踪、目标价和通知仍按本人隔离，通知完全复用 `sync_prices`；无人追踪也允许管理员更新模拟历史，但不产生通知。
- 四款共享状态表以数据库聚合展示当前模拟价、已记录最低价、历史数量和追踪人数，不逐游戏循环查询。成功反馈和审计使用服务端确认的名称及 ID，不采用浏览器提交的元数据。

选定游戏的整数演示还会把原服务的通知重试范围限定到该游戏，避免修改游戏 2 却补发游戏 3 的旧通知。其他游戏的失败快照原样保留，可在对应游戏后续同步或真实同步中重试；真实同步和旧兼容场景保持原来的重试范围。通知阈值判断仍全部由 `sync_prices` 处理，没有在路由中重写。
- 无有效真实追踪游戏时不发外部请求，记录一次成功的“无需同步”审计；来源的尝试时间不更新，因为没有调用报价源。参数校验失败同样只记录失败审计，不改变来源同步状态。

## 双击、并发与失败

模拟事件 ID 在整个数据库中全局唯一，继续沿用 `demo-manual-事件ID` 格式，不按游戏另建命名空间。同一 ID、同一游戏、同一金额重放时新增历史和通知为 0；同一 ID 换金额或换游戏均失败，事务不写入价格。表单预填随机新 ID，双击同一表单会带上相同 ID；结果页会生成下一个建议 ID，要演示重放请重新选择原游戏并填回原 ID 和金额。不同 ID 表示新观测。

真实来源继续用稳定快照 ID 去重。重复或并发点击可能发出两次外部请求，但相同快照只保存一次；若数据源期间确实产生新快照，它属于新观测。网络阶段不占用 SQLite 写事务，写入由原 `BEGIN IMMEDIATE` 串行处理，仍会检查最后一位用户是否已取消及各接收者的追踪关系。数据库繁忙会给出稍后重试提示，不增加后台队列或线程。

API 失败返回错误分类摘要，不显示完整响应；旧价格和 `last_success_at` 保留，普通用户仍能读取原详情。通知的单用户失败继续保留重试快照，结果会明确显示失败次数和待重试数；“成功”表示本轮报价同步完成，不等于所有通知都已送达。

## SECRET_KEY 与 Cookie

本地不设置环境变量时，首次启动生成随机密钥并保存在被 Git 忽略的 `instance/session_secret.key`，以后复用，避免每次重启退出登录。也支持从 `SECRET_KEY` 环境变量读取，至少 32 个字符，优先于本地文件。换密钥会使已有 session 失效。不要打印、截图、提交该文件或真实环境配置；`.env` 文件也已排除。

本地可在当前 PowerShell 临时生成环境密钥（赋值捕获输出，不会直接显示密钥，也不把密钥字面值写进命令历史）：

```powershell
$env:SECRET_KEY = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

生产配置用 `APP_ENV=production`，必须通过部署环境注入同一份强随机 `SECRET_KEY`，缺失时拒绝启动；不能依赖本地随机文件。此配置只用于 HTTPS 部署，开启 Secure Cookie。本地 HTTP 使用默认 development 配置，Secure 关闭；所有环境均启用 HttpOnly 和 SameSite=Lax。不要在生产使用 `--debug` 或 Flask 开发服务器；本轮不提供生产部署方案。

## 人民币旧库与美元新库

旧库 `instance/price_tracker.sqlite3` 原样保留，包含原 CNY 价格、目标价和通知。新版本使用 `instance/price_tracker_usd.sqlite3`，没有对旧 CNY 数据直接换符号或做汇率换算。新的模拟美元金额是虚构演示数据，需要真实订阅时请在真实详情页重新设置美元目标价。

同一个美元库中，模拟游戏 1～4 与真实游戏 101～104 是独立档案，历史、已记录最低价、目标价和触发通知均按游戏 ID 隔离；同步状态也按来源隔离。通知列表显示各自来源。所有本地数据库都在 Git 忽略规则内。

## 任意整数模拟价格

```powershell
# price-N 的 N 表示整数美元，不是美分
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-123
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-0
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-2147483647
```

N 的范围是 `0～2147483647`（C++ 常见的有符号 32 位 int 上限）。小数、负数和超界值会被拒绝。例如 `price-123` 保存为 12300 美分。金额乘以 100 后使用 SQLite 64 位 INTEGER 存储，不在 32 位整数中相乘。

每次任意整数命令默认创建一个新的模拟事件，观测时间递增。因此 `price-90 → price-128 → price-90` 可以模拟第二次达标。显式重放同一事件需复用事件 ID：

```powershell
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-90 --event-id drop-001
.\.venv\Scripts\python.exe -m flask --app app sync-prices --scenario price-90 --event-id drop-001
```

第二次新增 0 条。不同观测应使用不同 ID；同一 ID 搭配不同金额会报错并回滚。事件 ID 支持 1～80 个字母、数字、下划线或短横线。

省略 `--game-id` 时仍使用模拟游戏 1。可以选择其他固定模拟游戏，校验与网页一致：

```powershell
.\.venv\Scripts\python.exe -m flask --app app sync-prices --game-id 2 --scenario price-29 --event-id hollow-demo-29
# 同一游戏、同一金额、同一 ID 重放：新增 0 条
.\.venv\Scripts\python.exe -m flask --app app sync-prices --game-id 2 --scenario price-29 --event-id hollow-demo-29
```

事件 ID 全局唯一，不能把上面的 `hollow-demo-29` 改用于游戏 3，或改成另一价格。真实同步不接受 `--game-id`，只能由外部 API 取得真实报价，不能手工改真实价格。

保留兼容场景：`price-drop`（默认固定 88 美元事件）、`late`（固定旧观测）、`failure`（模拟失败）和 `sequence`（固定 100、88、120、90 序列）。这些固定场景仍只服务游戏 1，ID 和观测时间不变；为游戏 2～4 必须明确使用 `--scenario price-整数`。较旧固定时间仍按原规则受旧事件保护，不会为演示清空数据库或改写原记录。
