# 交付验证记录（2026-11-06）

[项目首页](../README.md) · [运维](operations.md) · [测试方法](testing.md)

## 范围与环境

- 原 README 578 行，按 [章节清单](documentation-map.md) 完整迁移 35 个章节；首页调整为 180 行。
- Windows CPython 3.14.3（64 位）；最低 3.10 基于源码类型注解、AST 检查和固定依赖的 Requires-Python，下限版本没有完整实机矩阵测试。
- `requirements.txt` 固定 8 个运行时包；可选浏览器检查依赖独立固定在 `requirements-dev.txt`。
- 本轮不请求真实 CheapShark，不修改业务服务、账号、权限、同步规则或迁移；只增加静态 favicon 路由、模板引用和交付工具。

## 实际安装与完整离线验证

已在全新临时虚拟环境通过固定依赖安装、`pip check`、Flask/项目导入和两次临时数据库升级。默认 PyPI 在当前环境超时，HTTPS 镜像可用，安装过程未关闭证书校验。

验证脚本使用 `system_site_packages=False` 创建新的虚拟环境，全部从固定 requirements 安装；没有复用全局 Flask 或项目旧虚拟环境。完整测试时仅安装运行时依赖，不需要 Playwright。

| 检查 | 实际结果 |
|---|---|
| 8 个固定运行时依赖安装 | 成功，HTTPS 镜像下载或使用其对应 wheel 缓存 |
| `pip check` | No broken requirements found |
| Flask 与项目导入 | 成功，解释器来自临时新虚拟环境 |
| 临时项目 `upgrade-db` 连续两次 | 均成功，新库建立后可重复升级 |
| 完整离线测试 | **202 项，全部通过**；最终复验耗时 20.978 秒 |
| 本轮新增静态资源回归 | 2 项；既有时间顺序修复与其测试保留 |
| Python 编译 | 应用、适配器、服务、脚本、测试通过 |
| Python 3.10 语法检查 | 全部项目 Python 文件通过 AST 检查 |
| 相对链接、锚点与命令 | `scripts/check_docs.py` 通过，无外部请求 |
| 空白与补丁格式 | `git diff --check` 通过 |

可选浏览器依赖也实际安装并验证：Playwright 1.64.1、pyee 13.0.0、greenlet 3.5.6、typing_extensions 4.16.0，均精确固定。选择 pyee 13 而非要求 Python 3.12 的 14，以保持可选工具与项目最低 3.10 一致；实际端到端测试仍是在推荐的 3.14.3 上运行，不冒称实测过 3.10。

## 浏览器记录

实际使用本机 Edge 无界面渲染，桌面和手机共 12 个页面/尺寸组合；覆盖详情、模拟管理、空搜索、通知/管理员登录跳转和注册。修复前默认图标路径显式探测为 404；修复后 SVG 资源和默认路径均为 200。

[浏览器 JSON](browser-check-2026-11-06.json)和[首页截图](screenshots/game-detail.png)使用独立临时数据库，实际浏览器为 Edge 156.0.4314.58，桌面 1440×920、手机 390×844。浏览器原生图标加载与显式默认路径探测均通过；无界面浏览器未隐式请求旧默认图标时，使用后者确认，未将它伪称为观察到的自动请求。

| 项目 | 结果 |
|---|---|
| 详情、模拟管理、空搜索、两个登录跳转、注册 | 两种尺寸共 12 项通过 |
| Console 错误 / 页面异常 | 0 / 0 |
| 失败请求 / HTTP ≥400 | 0 / 0 |
| `/static/favicon.svg` 与 `/favicon.ico` | 均为 200 |
| 整页水平溢出 | 无；宽图表与表格仍在各自容器内滚动 |
| 外部浏览器请求 / CheapShark 请求 | 0 / 0 |
| 浏览前后临时数据库转储 | 完全一致 |

## 数据保护与改动范围

本轮开始与结束对现有 SQLite 文件做 SHA-256 核对，美元库、人民币库及已有验证库均未变。架构、服务、适配器、迁移、SQL 和上一轮回归测试逐文件比对未变；`app.py` 除新增 favicon 函数外的语法树与本轮开始完全一致。

没有执行清库、真实 API、Git 提交、推送或历史修改。工作区原有的时间顺序修复、测试和独立规划文档保留。实际账号、追踪、同步、价格、通知和管理员业务规则均未修改，数据库结构与迁移也未修改。

本轮修改/新增文件：

- 首页与依赖：[README](../README.md)、[requirements.txt](../requirements.txt)、[requirements-dev.txt](../requirements-dev.txt)、[.gitignore](../.gitignore)。
- 分主题文档：[architecture.md](architecture.md)、[operations.md](operations.md)、[testing.md](testing.md)、[cheapshark.md](cheapshark.md)、[price-chart.md](price-chart.md)、[documentation-map.md](documentation-map.md)、本文。
- 浏览器记录：[JSON](browser-check-2026-11-06.json)、[页面截图](screenshots/game-detail.png)。
- 本地图标：[favicon.svg](../static/favicon.svg)、[base.html](../templates/base.html)、[app.py](../app.py) 中仅本轮 favicon 路由。
- 交付工具：[verify_delivery.py](../scripts/verify_delivery.py)、[check_docs.py](../scripts/check_docs.py)、[browser_smoke.py](../scripts/browser_smoke.py)、[capture_price_chart.py](../scripts/capture_price_chart.py) 的本地 favicon 路径适配。
- 回归：[test_static_resources.py](../tests/test_static_resources.py)。

人工边界仍包括实际 HTTPS/代理环境、真实 API 可用性、屏幕阅读器与真实触屏使用；这些没有被离线测试或浏览器模拟替代，详见[测试文档](testing.md)。

## 复现

从项目虚拟环境启动独立安装与全套测试：

```powershell
.\.venv\Scripts\python.exe scripts/verify_delivery.py --keep --index-url https://pypi.tuna.tsinghua.edu.cn/simple
.\.venv\Scripts\python.exe scripts/check_docs.py
git diff --check
```

浏览器检查需要可选开发依赖及已有 Edge/Chrome，详见[测试方法](testing.md)。运行脚本不会复制现有 `instance`、虚拟环境、Git 或密钥；输出路径只用于本机临时检查，不是可提交配置。
