from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import re

import click
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

from adapters.demo_price_source import DemoPriceSource, validate_scenario
from adapters.cheapshark import CheapSharkSource, MAX_SEARCH_LENGTH, validate_external_id
from adapters.contracts import PriceSourceError
from services.price_sync import sync_prices, sync_real_prices
from services.game_tracking import track_verified_game, stop_tracking
from services.accounts import normalize_username, hash_password
from werkzeug.security import check_password_hash
from migrations import upgrade_game_identifiers, upgrade_accounts
from security import configure_security, init_security, csrf_token, login_required, admin_required, safe_next


DEMO_USER_ID = 1

app = Flask(__name__)
app.config.update(
    DATABASE=Path(app.instance_path) / "price_tracker_usd.sqlite3",
)
Path(app.instance_path).mkdir(parents=True, exist_ok=True)
configure_security(app)


def get_db():
    """Open one SQLite connection per request."""
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


init_security(app, get_db)


def seed_demo_data(db):
    games = [
        (1, "星露谷物语", "把一小片荒地，慢慢过成自己的生活。", "模拟经营", "Steam", "地区未由源提供", "USD", "#4f7b63"),
        (2, "空洞骑士", "深入圣巢，在废墟与回声之间寻找答案。", "动作冒险", "Steam", "地区未由源提供", "USD", "#5066a6"),
        (3, "双人成行", "一场只能由两个人共同完成的奇妙旅程。", "合作冒险", "Steam", "地区未由源提供", "USD", "#d76f4b"),
        (4, "潜水员戴夫", "白天潜水，晚上经营寿司店。", "冒险经营", "Steam", "地区未由源提供", "USD", "#16869b"),
    ]
    db.execute("INSERT INTO users (id, display_name) VALUES (?, ?)", (DEMO_USER_ID, "演示用户"))
    db.executemany(
        """
        INSERT INTO games
            (id, title, subtitle, genre, platform, region, currency, accent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        games,
    )
    db.executemany(
        """INSERT INTO games
           (id, title, subtitle, genre, platform, region, currency, accent, data_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'cheapshark')""",
        [(game[0] + 100, *game[1:]) for game in games],
    )

    observations = {
        1: [(15800, "2026-08-08 10:00:00"), (14800, "2026-08-29 10:00:00"), (13800, "2026-09-19 10:00:00"), (12800, "2026-10-10 10:00:00"), (12800, "2026-11-03 10:00:00")],
        2: [(5800, "2026-08-08 10:05:00"), (2900, "2026-08-22 10:05:00"), (5800, "2026-09-12 10:05:00"), (2300, "2026-10-17 10:05:00"), (2900, "2026-11-03 10:05:00")],
        3: [(19800, "2026-08-08 10:10:00"), (7900, "2026-08-29 10:10:00"), (19800, "2026-09-26 10:10:00"), (9900, "2026-10-24 10:10:00"), (7900, "2026-11-03 10:10:00")],
        4: [(8000, "2026-08-08 10:15:00"), (6400, "2026-08-29 10:15:00"), (8000, "2026-09-19 10:15:00"), (4800, "2026-10-10 10:15:00"), (6400, "2026-11-03 10:15:00")],
    }
    for game_id, history in observations.items():
        db.executemany(
            """
            INSERT INTO price_observations
                (source_event_id, game_id, price_cents, observed_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                (f"seed-game-{game_id}-observation-{index}", game_id, price, observed_at)
                for index, (price, observed_at) in enumerate(history, start=1)
            ],
        )

    # Fictional USD demo amounts; not a conversion of historical CNY data.
    db.execute(
        "INSERT INTO price_targets (user_id, game_id, target_cents) VALUES (?, ?, ?)",
        (DEMO_USER_ID, 1, 10000),
    )


def initialize_database(db):
    schema_path = Path(app.root_path) / "schema.sql"
    db.executescript(schema_path.read_text(encoding="utf-8"))
    db.executescript((Path(app.root_path) / "notifications.sql").read_text(encoding="utf-8"))
    seed_demo_data(db)
    db.commit()
    upgrade_game_identifiers(db)
    upgrade_accounts(db)


@app.cli.command("init-db")
def init_db_command():
    """Reset the USD database; legacy CNY database is never touched."""
    initialize_database(get_db())
    click.echo("美元数据库初始化完成：4 款游戏的独立模拟/真实档案，20 条模拟记录；真实历史为空。")


@app.cli.command("upgrade-db")
def upgrade_db_command():
    """Safely add external identifiers and unique indexes to the USD database."""
    db = get_db()
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='games'").fetchone() is None:
        initialize_database(db)
    else:
        try:
            upgrade_game_identifiers(db)
            upgrade_accounts(db)
        except sqlite3.DatabaseError:
            raise click.ClickException("升级失败，失败事务已回滚；请检查用户名或外部标识冲突，不要清空数据库。") from None
    click.echo("数据库升级完成；演示用户、价格、订阅和通知已保留，旧人民币数据库未修改。")


@app.cli.command("create-admin")
@click.option("--username", required=True, help="创建或提升的管理员用户名。")
def create_admin_command(username):
    """Create/promote an admin and set a new password via hidden input only."""
    try:
        username, normalized = normalize_username(username)
        password = click.prompt("管理员密码", hide_input=True, confirmation_prompt="再次输入管理员密码")
        password_hash = hash_password(password)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    db = get_db()
    with db:
        db.execute("""
            INSERT INTO users(display_name,username,username_normalized,password_hash,role,created_at)
            VALUES (?,?,?,?,'admin',CURRENT_TIMESTAMP)
            ON CONFLICT(username_normalized COLLATE NOCASE) DO UPDATE SET
                password_hash=excluded.password_hash, role='admin'
        """, (username, username, normalized, password_hash))
    click.echo(f"管理员 {username} 已就绪，密码已通过隐藏输入设置。")


@app.cli.command("sync-prices")
@click.option("--source", type=click.Choice(["demo", "cheapshark"]), default="demo", show_default=True)
@click.option("--event-id", default=None, help="模拟事件标识；重放同一事件时复用，不同观测使用不同标识。")
@click.option(
    "--scenario",
    default=None,
    help="模拟场景 price-N，N 为 0～2147483647 的整数美元；默认 price-drop。",
)
def sync_prices_command(source, event_id, scenario):
    """Manually synchronize isolated demo or Steam/USD CheapShark quotes."""
    if source == "cheapshark":
        if scenario is not None or event_id is not None:
            raise click.UsageError("真实同步不能指定模拟场景或事件 ID。")
    else:
        scenario = scenario or "price-drop"
        try:
            validate_scenario(scenario)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--scenario") from None
        if event_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", event_id):
            raise click.BadParameter("只支持 1～80 个字母、数字、下划线或短横线。", param_hint="--event-id")
        if event_id is not None and not re.fullmatch(r"price-[0-9]+", scenario):
            raise click.UsageError("--event-id 仅可与 price-整数 一起使用。")
        latest = get_db().execute(
            "SELECT MAX(observed_at) FROM price_observations WHERE game_id=1"
        ).fetchone()[0]
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
        if latest:
            timestamp = max(timestamp, datetime.fromisoformat(latest) + timedelta(microseconds=1))
        adapter = DemoPriceSource(event_id, timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"))
    try:
        if source == "cheapshark":
            report = sync_real_prices(get_db())
            total_tracked, sync_games = report.total_tracked, report.games
            if report.result is None:
                click.echo(f"总追踪数 {total_tracked}，可同步数 0，收到报价数 0，新增历史数 0，缺失数 0，通知数 0。本次不请求外部报价。")
                return
            result = report.result
        else:
            result = sync_prices(get_db(), adapter, scenario=scenario)
    except (sqlite3.DatabaseError, PriceSourceError, ValueError) as exc:
        raise click.ClickException(f"同步未完成：{exc}") from None
    if not result.success:
        if source == "cheapshark":
            click.echo(f"总追踪数 {total_tracked}，可同步数 {len(sync_games)}。整轮失败：新增历史数 0，通知数 0；收到报价数与缺失数未统计。")
        raise click.ClickException(
            f"同步失败：{result.error}。旧价格与上次成功时间已保留。"
        )

    if source == "cheapshark":
        click.echo(
            f"同步成功：总追踪数 {total_tracked}，可同步数 {len(sync_games)}，"
            f"收到报价数 {result.received}，新增历史数 {result.inserted}，"
            f"缺失数 {len(report.missing_games)}，通知数 {result.notifications}；"
            f"重复或旧事件等未新增 {result.received - result.inserted} 条。"
        )
        if total_tracked != len(sync_games):
            click.echo(f"跳过非 Steam/USD 或无有效 Steam AppID 的档案 {total_tracked - len(sync_games)} 款。")
        for game in report.missing_games:
            click.echo(f"缺失报价：{game.title}（Steam AppID {game.steam_app_id}），保留旧价格，未当成免费。")
    else:
        click.echo(
            f"同步成功：收到 {result.received} 条，新增 {result.inserted} 条，"
            f"忽略重复或旧事件 {result.received - result.inserted} 条，生成通知 {result.notifications} 条。"
        )


@app.template_filter("money")
def format_money(cents):
    if cents is None:
        return "—"
    dollars, remainder = divmod(cents, 100)
    return f"${dollars:,}.{remainder:02d}"


@app.route("/")
def index():
    return redirect(url_for("game_detail", game_id=1))


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    username = request.form.get("username", "") if request.method == "POST" else ""
    if request.method == "POST":
        try:
            username, normalized = normalize_username(username)
            password_hash = hash_password(request.form.get("password", ""))
            with get_db() as db:
                db.execute("""
                    INSERT INTO users(display_name,username,username_normalized,password_hash,role,created_at)
                    VALUES (?,?,?,?,'user',CURRENT_TIMESTAMP)
                """, (username, username, normalized, password_hash))
        except ValueError as exc:
            error = str(exc)
        except sqlite3.IntegrityError:
            error = "该用户名已被使用（不区分大小写）。"
        else:
            flash("注册成功，请登录。", "success")
            return redirect(url_for("login"), code=303)
    return render_template("register.html", error=error, username=username, **navigation_context(None))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    destination = safe_next(request.form.get("next") if request.method == "POST" else request.args.get("next"))
    if request.method == "POST":
        try:
            _, normalized = normalize_username(request.form.get("username", ""))
        except ValueError:
            normalized = ""
        user = get_db().execute("SELECT id,password_hash FROM users WHERE username_normalized=?", (normalized,)).fetchone()
        password = request.form.get("password", "")
        if (user is None or not user["password_hash"] or not 8 <= len(password) <= 128
                or not check_password_hash(user["password_hash"], password)):
            error = "用户名或密码错误。"
        else:
            session.clear()
            session["user_id"] = user["id"]
            csrf_token()  # Rotate the token at the authentication boundary.
            return redirect(destination or url_for("index"), code=303)
    return render_template("login.html", error=error, next_path=destination or "", **navigation_context(None))


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("index"), code=303)


def navigation_context(section, game=None):
    """Keep category order stable and put only game links in the second bar."""
    db = get_db()
    demo = db.execute("SELECT id, title FROM games WHERE data_source='demo' ORDER BY id").fetchall()
    real = db.execute("SELECT id, title FROM games WHERE data_source='cheapshark' AND is_tracked=1 ORDER BY id").fetchall()
    demo_id = demo[0]["id"] if demo else None
    real_id = real[0]["id"] if real else None
    if game is not None:
        if game["data_source"] == "demo":
            demo_id = game["id"]
            partner = db.execute(
                "SELECT id FROM games WHERE demo_game_id=? AND data_source='cheapshark' AND is_tracked=1 ORDER BY id LIMIT 1",
                (game["id"],),
            ).fetchone()
            if partner:
                real_id = partner["id"]
        else:
            if game["is_tracked"]:
                real_id = game["id"]
            partner = db.execute("SELECT id FROM games WHERE id=? AND data_source='demo'", (game["demo_game_id"],)).fetchone()
            if partner:
                demo_id = partner["id"]
    return {
        "navigation_section": section,
        "navigation_demo_url": url_for("game_detail", game_id=demo_id) if demo_id else url_for("game_category", source="demo"),
        "navigation_real_url": url_for("game_detail", game_id=real_id) if real_id else url_for("game_category", source="cheapshark"),
        "navigation_games": demo if section == "demo" else real if section == "cheapshark" else [],
        "navigation_game_id": game["id"] if game is not None else None,
    }


@app.get("/games")
def game_category():
    source = request.args.get("source", "demo")
    if source not in ("demo", "cheapshark"):
        abort(400)
    first = get_db().execute(
        "SELECT id FROM games WHERE data_source=? AND is_tracked=1 ORDER BY id LIMIT 1", (source,)
    ).fetchone()
    if first:
        return redirect(url_for("game_detail", game_id=first["id"]))
    return render_template(
        "game_category.html", **navigation_context(source),
        source_label="CheapShark · Steam · USD" if source == "cheapshark" else "模拟数据 · USD",
    )


@app.get("/games/search")
def game_search():
    submitted = "q" in request.args
    query = request.args.get("q", "").strip()
    results = []
    search_error = None

    if submitted and query:
        if len(query) > MAX_SEARCH_LENGTH:
            search_error = f"搜索关键词不能超过 {MAX_SEARCH_LENGTH} 个字符。"
        else:
            try:
                results = CheapSharkSource().search_by_title(query)
            except (PriceSourceError, ValueError) as exc:
                search_error = str(exc)
    if submitted and len(query) <= MAX_SEARCH_LENGTH:
        session["last_game_search"] = query
    tracked = get_db().execute(
        "SELECT cheapshark_game_id, steam_app_id FROM games WHERE data_source='cheapshark' AND is_tracked=1"
    ).fetchall()

    return render_template(
        "game_search.html",
        query=query,
        submitted=submitted,
        results=results,
        search_error=search_error,
        max_search_length=MAX_SEARCH_LENGTH,
        source_label="CheapShark 游戏搜索 · USD",
        tracked_by_cheapshark={row["cheapshark_game_id"]: row for row in tracked if row["cheapshark_game_id"]},
        tracked_by_steam={row["steam_app_id"]: row for row in tracked if row["steam_app_id"]},
        **navigation_context("search"),
    )


@app.post("/games/track")
@login_required
def track_game():
    try:
        # Only the external identifier is read; all other form fields are ignored.
        verified = CheapSharkSource().get_verified_steam_game(
            request.form.get("cheapshark_game_id", "")
        )
        game_id, created = track_verified_game(get_db(), verified)
    except (PriceSourceError, ValueError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("game_search"), code=303)
    except sqlite3.DatabaseError:
        flash("未能保存追踪档案，未加入半成品记录。请确认已运行 upgrade-db 后重试。", "error")
        return redirect(url_for("game_search"), code=303)
    flash("已加入追踪。" if created else "已开启追踪，沿用已有档案。", "success")
    try:
        report = sync_real_prices(get_db())
        if report.result is None:
            flash("没有可同步的 Steam 游戏，尚未获取报价。", "error")
        elif not report.result.success:
            flash(f"追踪已保存，但自动同步失败：{report.result.error} 旧价格和上次成功同步时间已保留，请稍后手动重试。", "error")
        elif any(item.game_id == game_id for item in report.missing_games):
            flash("已自动同步，但该游戏本次没有 Steam 报价；保留旧价格，缺失不代表免费。", "error")
        else:
            flash(f"已自动同步追踪列表：收到 {report.result.received} 条 Steam 报价，新增 {report.result.inserted} 条历史。", "success")
    except (sqlite3.DatabaseError, PriceSourceError, ValueError):
        flash("追踪已保存，但自动同步未完成。旧价格和成功时间未被本次写入覆盖，请稍后手动重试。", "error")
    return redirect(url_for("game_detail", game_id=game_id), code=303)


@app.post("/games/untrack")
@login_required
def untrack_game():
    game_id = None
    try:
        steam_id = validate_external_id(request.form.get("steam_app_id", ""))
        game_id = stop_tracking(get_db(), steam_id)
        flash("已取消追踪；历史价格、目标价和旧通知均已保留。", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except sqlite3.DatabaseError:
        flash("取消追踪未完成，请确认已运行 upgrade-db 后重试。", "error")
    # Use the category's existing ordering/empty state, never a user-supplied URL.
    if request.args.get("return_to") == "detail" and game_id is not None:
        return redirect(url_for("game_category", source="cheapshark"), code=303)
    query = session.get("last_game_search", "")
    return redirect(url_for("game_search", q=query) if query else url_for("game_search"), code=303)


@app.route("/games/<int:game_id>")
def game_detail(game_id):
    db = get_db()
    game = db.execute(
        """
        SELECT
            g.*,
            latest.price_cents AS current_price_cents,
            latest.observed_at AS current_observed_at,
            (SELECT MIN(po.price_cents)
             FROM price_observations po
             WHERE po.game_id = g.id) AS lowest_price_cents,
            pt.target_cents
        FROM games g
        LEFT JOIN price_observations latest ON latest.id = (
            SELECT po2.id
            FROM price_observations po2
            WHERE po2.game_id = g.id
            ORDER BY po2.observed_at DESC, po2.id DESC
            LIMIT 1
        )
        LEFT JOIN price_targets pt
            ON pt.game_id = g.id AND pt.user_id = ?
        WHERE g.id = ?
        """,
        (DEMO_USER_ID, game_id),
    ).fetchone()
    if game is None:
        abort(404)
    if game["data_source"] == "cheapshark" and not game["is_tracked"]:
        return redirect(url_for("game_category", source="cheapshark"))

    history = db.execute(
        """
        SELECT source_event_id, price_cents, observed_at
        FROM price_observations
        WHERE game_id = ?
        ORDER BY observed_at DESC, id DESC
        """,
        (game_id,),
    ).fetchall()
    sync_status = db.execute(
        """
        SELECT last_success_at, last_attempt_at, last_error
        FROM sync_status
        WHERE source_name = ?
        """,
        (CheapSharkSource.name if game["data_source"] == "cheapshark" else DemoPriceSource.name,),
    ).fetchone()
    notification_count = db.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND game_id = ?",
        (DEMO_USER_ID, game_id),
    ).fetchone()[0]
    if game["data_source"] == "cheapshark":
        counterpart = db.execute(
            "SELECT id FROM games WHERE id=? AND data_source='demo'",
            (game["demo_game_id"],),
        ).fetchone()
    else:
        counterpart = db.execute(
            "SELECT id FROM games WHERE demo_game_id=? AND data_source='cheapshark' ORDER BY id LIMIT 1",
            (game_id,),
        ).fetchone()
    return render_template(
        "game_detail.html",
        game=game,
        history=history,
        notification_count=notification_count,
        sync_status=sync_status,
        source_label="CheapShark · Steam · USD" if game["data_source"] == "cheapshark" else "模拟数据 · USD",
        is_real=game["data_source"] == "cheapshark",
        counterpart_id=counterpart["id"] if counterpart else None,
        **navigation_context(game["data_source"], game),
    )


@app.post("/games/<int:game_id>/target")
@login_required
def set_target(game_id):
    db = get_db()
    game_exists = db.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone()
    if game_exists is None:
        abort(404)

    raw_value = request.form.get("target_price", "").strip()
    try:
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", raw_value):
            raise InvalidOperation
        dollars = Decimal(raw_value)
        cents_decimal = dollars * 100
        if dollars <= 0 or cents_decimal > 9223372036854775807:
            raise InvalidOperation
        target_cents = int(cents_decimal)
    except (InvalidOperation, ValueError):
        flash("请输入大于 0、最多两位小数且在可存储范围内的目标价。", "error")
        return redirect(url_for("game_detail", game_id=game_id))

    db.execute(
        """
        INSERT INTO price_targets (user_id, game_id, target_cents, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, game_id) DO UPDATE SET
            target_cents = excluded.target_cents,
            updated_at = CURRENT_TIMESTAMP
        """,
        (DEMO_USER_ID, game_id, target_cents),
    )
    db.commit()
    flash(f"目标价已保存为 {format_money(target_cents)}。", "success")
    return redirect(url_for("game_detail", game_id=game_id))


@app.get("/notifications")
def notifications():
    rows = get_db().execute(
        """
        SELECT n.*, g.title, g.data_source
        FROM notifications n JOIN games g ON g.id = n.game_id
        WHERE n.user_id = ?
        ORDER BY n.observed_at DESC, n.id DESC
        """,
        (DEMO_USER_ID,),
    ).fetchall()
    return render_template("notifications.html", notifications=rows, **navigation_context(None))


if __name__ == "__main__":
    app.run()
