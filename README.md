# 价格观察站（模拟数据演示）

一个用 Flask、SQLite、Jinja HTML 和 CSS 制作的游戏价格详情页练习项目。

当前范围：4 款演示游戏、价格详情、已记录最低价、按观测时间排列的历史表格、目标价、站内提醒，以及可重复执行的手动价格同步。所有数据均为模拟数据。

## Windows PowerShell 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m flask --app app init-db
python -m flask --app app run --debug
```

浏览器打开 <http://127.0.0.1:5000/games/1>。

## 复现手动同步

初始化后，游戏 1 当前价是 ¥128.00，目标价是 ¥100.00，因此还没有提醒。

另开一个 PowerShell，在项目目录执行：

```powershell
python -m flask --app app sync-prices
```

刷新详情页后，当前价变成 ¥88.00，并出现站内提醒。再次执行完全相同的命令会显示“新增 0 条、忽略重复 1 条”，历史记录不会增加。

还有两个用于验证边界情况的固定场景：

```powershell
# 导入一条时间更早的 ¥88 观测；它会进入历史，但不会替换当前价
python -m flask --app app sync-prices --scenario late

# 模拟数据源失败；旧价格与上次成功同步时间保持不变
python -m flask --app app sync-prices --scenario failure
```

`adapters/demo_price_source.py` 只负责把数据源内容转换成统一事件。以后接真实 API 时主要替换这里。`services/price_sync.py` 负责去重和写入 SQLite，不关心数据来自哪里。
