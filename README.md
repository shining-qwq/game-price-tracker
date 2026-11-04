# 价格观察站（模拟数据演示）

一个用 Flask、SQLite、Jinja HTML 和 CSS 制作的游戏价格详情页练习项目。

本轮范围：4 款演示游戏、价格详情、已记录最低价、按观测时间排列的历史表格、目标价和站内提醒。所有数据均为模拟数据。

## Windows PowerShell 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m flask --app app init-db
python -m flask --app app run --debug
```

浏览器打开 <http://127.0.0.1:5000/games/1>。
