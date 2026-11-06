"""Fixed administrator sync actions; no command execution or arbitrary actions."""
import sqlite3
from uuid import uuid4

from flask import flash, g, redirect, render_template, request, url_for

from adapters.cheapshark import CheapSharkSource
from adapters.contracts import PriceSourceError
from adapters.demo_price_source import DemoPriceSource, DEMO_GAME_IDS, INT_MAX
from security import admin_required
from services.price_sync import load_sync_games, sync_prices
from services.sync_inputs import (DemoInputError, load_demo_games, resolve_demo_game,
                                  validate_demo_request, prepare_demo_sync)


def source_error_summary(error):
    """Render a fixed category, never upstream response text or credentials."""
    if not error:
        return None
    if "429" in error or "频繁" in error or "限流" in error:
        return "数据源请求过于频繁（429），请稍后重试。"
    if "超时" in error or "timeout" in error.lower():
        return "数据源请求超时，请稍后重试。"
    if "网络" in error or "连接" in error:
        return "数据源网络请求失败，请检查网络后重试。"
    if any(word in error for word in ("格式", "JSON", "字段", "无效", "分页", "商店", "AppID")):
        return "数据源响应未通过校验，本轮报价未导入。"
    return "数据源暂不可用，本轮同步未完成。"


def result_summary(result):
    if not result.success:
        return False, (source_error_summary(result.error) or "本轮同步未完成。") + " 旧价格和上次成功同步时间已保留。"
    summary = (f"同步完成：收到报价 {result.received} 条，新增历史 {result.inserted} 条，"
               f"重复、旧事件或无人追踪等未新增 {result.received - result.inserted} 条，新增通知 {result.notifications} 条。")
    if result.notification_failures or result.pending_notifications:
        summary += f" 通知失败 {result.notification_failures} 次，待重试 {result.pending_notifications} 条；下次成功同步重试。"
    return True, summary


def demo_display_state(db, values, *, game_key="demo_game_id", keep_event=False):
    """Read-only, bounded defaults for one page; never prepare or run a sync."""
    games = load_demo_games(db)
    selected_id = games[0]["id"] if games else None
    try:
        selected_id = resolve_demo_game(db, values.get(game_key))["id"]
    except DemoInputError:
        pass
    scenario = "price-128"
    candidate = values.get("scenario", "")
    if len(candidate) <= 16:
        try:
            validate_demo_request(candidate, integer_only=True)
            scenario = candidate
        except DemoInputError:
            pass
    state = {"demo_game_id": selected_id, "scenario": scenario}
    if keep_event:
        event_id = values.get("event_id" if game_key == "game_id" else "retry_event_id")
        if event_id is not None and len(event_id) <= 80:
            try:
                validate_demo_request(scenario, event_id, integer_only=True, require_event_id=True)
                state["retry_event_id"] = event_id
            except DemoInputError:
                pass
    return state


def register_admin_sync(app, get_db, navigation_context):
    @app.get("/admin/sync")
    @admin_required
    def admin_sync_page():
        db = get_db()
        sources = []
        for source, name, label in [("demo", DemoPriceSource.name, "模拟数据 / USD"),
                                    ("cheapshark", CheapSharkSource.name, "CheapShark / Steam / USD")]:
            status = db.execute("SELECT * FROM sync_status WHERE source_name=?", (name,)).fetchone()
            games = db.execute("""SELECT COUNT(*) FROM games g WHERE g.data_source=?
                AND EXISTS (SELECT 1 FROM user_game_tracking ugt WHERE ugt.game_id=g.id)""", (source,)).fetchone()[0]
            history = db.execute("""SELECT COUNT(*) FROM price_observations po JOIN games g ON g.id=po.game_id
                WHERE g.data_source=?""", (source,)).fetchone()[0]
            sources.append({"label": label, "status": status, "tracked_games": games,
                            "history_count": history, "error": source_error_summary(status["last_error"]) if status else None})
        actions = db.execute("""SELECT a.id,a.user_id,u.username,a.action_type,a.source,a.outcome,
                                      a.summary,a.created_at,a.finished_at
            FROM admin_action_log a JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 20""").fetchall()
        demo_games = load_demo_games(db)
        display_state = demo_display_state(db, request.args, keep_event=True)
        demo_statistics = db.execute("""
            SELECT g.id,g.title,latest.price_cents AS current_price_cents,
                   history.lowest_price_cents,COALESCE(history.history_count,0) AS history_count,
                   COALESCE(followers.follower_count,0) AS follower_count
            FROM games g
            LEFT JOIN price_observations latest ON latest.id=(
                SELECT po.id FROM price_observations po WHERE po.game_id=g.id
                ORDER BY po.observed_at DESC,po.id DESC LIMIT 1)
            LEFT JOIN (
                SELECT po.game_id,MIN(po.price_cents) AS lowest_price_cents,COUNT(*) AS history_count
                FROM price_observations po JOIN games dg ON dg.id=po.game_id
                WHERE dg.data_source='demo' GROUP BY po.game_id
            ) history ON history.game_id=g.id
            LEFT JOIN (
                SELECT ugt.game_id,COUNT(*) AS follower_count
                FROM user_game_tracking ugt JOIN games dg ON dg.id=ugt.game_id
                WHERE dg.data_source='demo' GROUP BY ugt.game_id
            ) followers ON followers.game_id=g.id
            WHERE g.data_source='demo' AND g.id IN (?,?,?,?) ORDER BY g.id
        """, DEMO_GAME_IDS).fetchall()
        return render_template("admin_sync.html", sources=sources, actions=actions,
                               demo_games=demo_games, demo_statistics=demo_statistics,
                               selected_demo_game_id=display_state["demo_game_id"],
                               selected_demo_scenario=display_state["scenario"],
                               default_event_id=display_state.get("retry_event_id") or "web-" + uuid4().hex,
                               max_demo_dollars=INT_MAX,
                               **navigation_context(None))

    def run_action(source):
        db = get_db()
        # These values only choose the next GET's display. Business validation
        # below still checks the original POST and never uses fallback values.
        display_state = demo_display_state(db, request.form, game_key="game_id", keep_event=source == "demo")

        def action_redirect(*, keep_event=False):
            state = dict(display_state)
            if not keep_event:
                state.pop("retry_event_id", None)
            return redirect(url_for("admin_sync_page", **state,
                                    _anchor="demo-sync-panel" if source == "demo" else "real-sync-panel"), code=303)

        def action_flash(message, success=False):
            flash(message, source + ("-success" if success else "-error"))

        # Persist intent before doing work. If audit storage is unavailable, do
        # not start an operation that cannot be traced.
        try:
            with db:
                action_id = db.execute("""INSERT INTO admin_action_log
                    (user_id,action_type,source,outcome,summary)
                    VALUES (?,?,?,'running','同步请求已受理，结果尚未记录。')""",
                    (g.user["id"], "sync_" + source, source)).lastrowid
        except sqlite3.DatabaseError:
            action_flash("暂时无法记录审计，本次未启动同步。请确认已升级数据库，或等待数据库空闲后重试。")
            return action_redirect(keep_event=True)

        demo_game = None
        validated_scenario = None
        demo_result = None
        try:
            if source == "demo":
                demo_game = resolve_demo_game(db, request.form.get("game_id"))
                scenario = request.form.get("scenario", "")
                # Only this validated scenario may appear in an audit summary,
                # even if subsequent event-ID validation or writing fails.
                validate_demo_request(scenario, integer_only=True)
                validated_scenario = scenario
                adapter = prepare_demo_sync(db, scenario, request.form.get("event_id"),
                                            game_id=demo_game["id"],
                                            integer_only=True, require_event_id=True)
                demo_result = sync_prices(db, adapter, scenario=scenario, retry_game_id=demo_game["id"])
                success, summary = result_summary(demo_result)
            else:
                games = load_sync_games(db)
                if games:
                    adapter = CheapSharkSource(games)
                    success, summary = result_summary(sync_prices(db, adapter))
                    if success:
                        summary += f" 去重后待同步 {len(games)} 款，缺失报价 {len(adapter.missing_games)} 款。"
                    else:
                        summary += f" 去重后原计划同步 {len(games)} 款，失败轮次不统计缺失报价。"
                else:
                    success, summary = True, "没有可同步的 Steam/USD 追踪游戏；未发送外部请求，未新增历史或通知。"
        except DemoInputError as exc:
            success, summary = False, str(exc)
        except ValueError:
            success, summary = False, "同步数据校验未通过，请检查场景和事件 ID；旧价格及成功时间已保留。"
        except PriceSourceError:
            success, summary = False, "数据源暂不可用，请稍后重试；旧价格及成功时间已保留。"
        except sqlite3.DatabaseError:
            success, summary = False, "数据库忙或写入失败，本轮价格事务未完成；旧价格及成功时间已保留。"
        except Exception:
            # Do not leak exception text (which could contain response data).
            success, summary = False, "同步发生异常，请核对本地数据状态后重试。"
        if source == "demo":
            if demo_game is None:
                prefix = "目标模拟游戏尚未通过校验。"
            else:
                # Titles come only from the confirmed database archive; bound
                # their display length so audit summaries fit the existing table.
                title = demo_game["title"][:80]
                identity = f"“{title}”（模拟游戏 ID {demo_game['id']}）"
                if validated_scenario is None:
                    prefix = f"{identity}，场景尚未通过校验。"
                else:
                    amount = f"${int(validated_scenario.removeprefix('price-')):,}.00"
                    if success:
                        verb = "记录" if demo_result.inserted else "核对"
                        prefix = f"已为{identity}{verb} {amount}（场景 {validated_scenario}）。"
                    else:
                        prefix = f"{identity}，场景 {validated_scenario}，目标价格 {amount}："
            summary = prefix + summary
        try:
            with db:
                db.execute("""UPDATE admin_action_log SET outcome=?,summary=?,finished_at=CURRENT_TIMESTAMP
                              WHERE id=? AND user_id=?""",
                           ("success" if success else "failure", summary, action_id, g.user["id"]))
        except sqlite3.DatabaseError:
            action_flash("同步操作已尝试，但审计结果未能保存。请核对数据和未完成记录，勿直接换新事件 ID 重试。")
        else:
            action_flash(summary, success)
        return action_redirect(keep_event=not success)

    @app.post("/admin/sync/demo")
    @admin_required
    def admin_sync_demo():
        return run_action("demo")

    @app.post("/admin/sync/cheapshark")
    @admin_required
    def admin_sync_cheapshark():
        return run_action("cheapshark")
