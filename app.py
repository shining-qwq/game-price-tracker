from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3

import click
from flask import Flask, abort, flash, g, redirect, render_template, request, url_for


DEMO_USER_ID = 1

app = Flask(__name__)
app.config.update(
    DATABASE=Path(app.instance_path) / "price_tracker.sqlite3",
    SECRET_KEY="dev-only-demo-key",
)
Path(app.instance_path).mkdir(parents=True, exist_ok=True)


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


def seed_demo_data(db):
    games = [
        (1, "星露谷物语", "把一小片荒地，慢慢过成自己的生活。", "模拟经营", "Steam", "中国大陆", "CNY", "#4f7b63"),
        (2, "空洞骑士", "深入圣巢，在废墟与回声之间寻找答案。", "动作冒险", "Steam", "中国大陆", "CNY", "#5066a6"),
        (3, "双人成行", "一场只能由两个人共同完成的奇妙旅程。", "合作冒险", "Steam", "中国大陆", "CNY", "#d76f4b"),
        (4, "潜水员戴夫", "白天潜水，晚上经营寿司店。", "冒险经营", "Steam", "中国大陆", "CNY", "#16869b"),
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

    observations = {
        1: [(2400, "2026-08-08 10:00:00"), (1800, "2026-08-29 10:00:00"), (2400, "2026-09-19 10:00:00"), (1200, "2026-10-10 10:00:00"), (1800, "2026-11-03 10:00:00")],
        2: [(5800, "2026-08-08 10:05:00"), (2900, "2026-08-22 10:05:00"), (5800, "2026-09-12 10:05:00"), (2300, "2026-10-17 10:05:00"), (2900, "2026-11-03 10:05:00")],
        3: [(19800, "2026-08-08 10:10:00"), (7900, "2026-08-29 10:10:00"), (19800, "2026-09-26 10:10:00"), (9900, "2026-10-24 10:10:00"), (7900, "2026-11-03 10:10:00")],
        4: [(8000, "2026-08-08 10:15:00"), (6400, "2026-08-29 10:15:00"), (8000, "2026-09-19 10:15:00"), (4800, "2026-10-10 10:15:00"), (6400, "2026-11-03 10:15:00")],
    }
    for game_id, history in observations.items():
        db.executemany(
            "INSERT INTO price_observations (game_id, price_cents, observed_at) VALUES (?, ?, ?)",
            [(game_id, price, observed_at) for price, observed_at in history],
        )

    # One target starts in the triggered state, so the reminder can be seen immediately.
    db.execute(
        "INSERT INTO price_targets (user_id, game_id, target_cents) VALUES (?, ?, ?)",
        (DEMO_USER_ID, 1, 2000),
    )


@app.cli.command("init-db")
def init_db_command():
    """Recreate the database and load deterministic demo records."""
    db = get_db()
    schema_path = Path(app.root_path) / "schema.sql"
    db.executescript(schema_path.read_text(encoding="utf-8"))
    seed_demo_data(db)
    db.commit()
    click.echo("演示数据库初始化完成：4 款游戏，20 条价格记录。")


@app.template_filter("money")
def format_money(cents):
    if cents is None:
        return "—"
    return f"¥{cents / 100:,.2f}"


@app.route("/")
def index():
    return redirect(url_for("game_detail", game_id=1))


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

    history = db.execute(
        """
        SELECT price_cents, observed_at
        FROM price_observations
        WHERE game_id = ?
        ORDER BY observed_at DESC, id DESC
        """,
        (game_id,),
    ).fetchall()
    games = db.execute("SELECT id, title FROM games ORDER BY id").fetchall()
    reminder_active = (
        game["target_cents"] is not None
        and game["current_price_cents"] is not None
        and game["current_price_cents"] <= game["target_cents"]
    )
    return render_template(
        "game_detail.html",
        game=game,
        games=games,
        history=history,
        reminder_active=reminder_active,
    )


@app.post("/games/<int:game_id>/target")
def set_target(game_id):
    db = get_db()
    game_exists = db.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone()
    if game_exists is None:
        abort(404)

    raw_value = request.form.get("target_price", "").strip()
    try:
        yuan = Decimal(raw_value)
        cents_decimal = yuan * 100
        if yuan < 0 or cents_decimal != cents_decimal.to_integral_value():
            raise InvalidOperation
        target_cents = int(cents_decimal)
    except (InvalidOperation, ValueError):
        flash("请输入不小于 0、且最多两位小数的目标价。", "error")
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


if __name__ == "__main__":
    app.run(debug=True)
