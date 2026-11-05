"""Four confirmed demo targets, global event identity and shared notifications."""
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import unittest
from unittest.mock import patch

from flask import render_template

from app import app, get_db
from adapters.demo_price_source import DemoPriceSource
from services.sync_inputs import DemoInputError, prepare_demo_sync
from tests.helpers import TEST_HASH


class GameOptions(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.in_select = False
        self.current = None
        self.options = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select" and attrs.get("name") == "game_id":
            self.in_select = True
        if tag == "option" and self.in_select:
            self.current = {"id": attrs.get("value"), "title": ""}

    def handle_data(self, data):
        if self.current is not None:
            self.current["title"] += data

    def handle_endtag(self, tag):
        if tag == "option" and self.current is not None:
            self.options.append(self.current)
            self.current = None
        if tag == "select":
            self.in_select = False


class AdminDemoGamesTest(unittest.TestCase):
    def setUp(self):
        self.previous_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.runner = app.test_cli_runner()
        self.assertEqual(self.runner.invoke(args=["init-db"]).exit_code, 0)
        ids = []
        with get_db() as db:
            for name, role in [("Admin", "admin"), ("Alice", "user"), ("Bob", "user"), ("Carol", "user")]:
                ids.append(db.execute("""INSERT INTO users(display_name,username,username_normalized,password_hash,role)
                    VALUES (?,?,?,?,?)""", (name, name, name.lower(), TEST_HASH, role)).lastrowid)
        self.admin_id, self.alice_id, self.bob_id, self.carol_id = ids
        self.admin = self.client(self.admin_id)
        self.network = patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Demo changes are offline"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.previous_config)

    def client(self, user_id=None):
        client = app.test_client()
        if user_id is not None:
            with client.session_transaction() as state:
                state["user_id"] = user_id
        return client

    def token(self, client):
        client.get("/login")
        with client.session_transaction() as state:
            return state["_csrf_token"]

    def submit(self, game_id="2", scenario="price-29", event_id="selected-event", client=None, **extra):
        client = client or self.admin
        return client.post("/admin/sync/demo", data={"game_id": game_id, "scenario": scenario,
            "event_id": event_id, **extra, "csrf_token": self.token(client)})

    def histories(self):
        return {game_id: [tuple(row) for row in get_db().execute(
            "SELECT * FROM price_observations WHERE game_id=? ORDER BY id", (game_id,))]
                for game_id in [1, 2, 3, 4, 101, 102, 103, 104]}

    def summaries(self):
        return {game_id: tuple(get_db().execute("""SELECT
                (SELECT price_cents FROM price_observations WHERE game_id=? ORDER BY observed_at DESC,id DESC LIMIT 1),
                MIN(price_cents),COUNT(*) FROM price_observations WHERE game_id=?""", (game_id, game_id)).fetchone())
                for game_id in [1, 2, 3, 4]}

    def last_log(self):
        return get_db().execute("SELECT * FROM admin_action_log ORDER BY id DESC LIMIT 1").fetchone()

    def target(self, user_id, game_id, cents=10000, tracked=True):
        with get_db() as db:
            db.execute("INSERT INTO price_targets(user_id,game_id,target_cents) VALUES (?,?,?)", (user_id, game_id, cents))
            if tracked:
                db.execute("INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,?)", (user_id, game_id))

    def notice_count(self, user_id):
        return get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=?", (user_id,)).fetchone()[0]

    def test_select_contains_exactly_four_database_titles_and_new_random_event_ids(self):
        with get_db() as db:
            db.execute("UPDATE games SET title='数据库中的空洞骑士' WHERE id=2")
        first = self.admin.get("/admin/sync")
        options = GameOptions(first.get_data(as_text=True)).options
        expected = [{"id": str(row["id"]), "title": row["title"]} for row in get_db().execute(
            "SELECT id,title FROM games WHERE data_source='demo' ORDER BY id")]
        self.assertEqual(options, expected)
        self.assertEqual([item["id"] for item in options], ["1", "2", "3", "4"])
        self.assertIn("数据库中的空洞骑士", first.get_data(as_text=True))
        self.submit("2", "price-29", "database-title")
        self.assertIn("数据库中的空洞骑士", self.last_log()["summary"])
        with patch("admin_sync.render_template", wraps=render_template) as renderer:
            self.admin.get("/admin/sync")
            first_id = renderer.call_args.kwargs["default_event_id"]
            self.admin.get("/admin/sync")
            second_id = renderer.call_args.kwargs["default_event_id"]
        self.assertNotEqual(first_id, second_id)

    def test_each_selected_game_adds_one_record_and_leaves_other_games_unchanged(self):
        for game_id, dollars in [(1, 128), (2, 29), (3, 79), (4, 64)]:
            with self.subTest(game_id=game_id):
                before = self.histories()
                before_summary = self.summaries()
                response = self.submit(str(game_id), "price-" + str(dollars), "four-game-" + str(game_id))
                self.assertEqual(response.status_code, 303)
                after = self.histories()
                self.assertEqual(len(after[game_id]), len(before[game_id]) + 1)
                self.assertEqual(self.summaries()[game_id][0], dollars * 100)
                for other_id in before:
                    if other_id != game_id:
                        self.assertEqual(before[other_id], after[other_id])
                        if other_id in before_summary:
                            self.assertEqual(before_summary[other_id], self.summaries()[other_id])
                row = get_db().execute("SELECT game_id,price_cents,typeof(price_cents) FROM price_observations WHERE source_event_id=?",
                                       ("demo-manual-four-game-" + str(game_id),)).fetchone()
                self.assertEqual(tuple(row), (game_id, dollars * 100, "integer"))

    def test_selected_time_is_monotonic_using_only_its_own_latest_observation(self):
        latest = {}
        with get_db() as db:
            for game_id in [1, 2, 3, 4]:
                latest[game_id] = datetime(2100 - game_id, 1, 1)
                db.execute("""INSERT INTO price_observations(source_event_id,game_id,price_cents,observed_at)
                    VALUES (?,?,100,?)""", ("future-" + str(game_id), game_id, latest[game_id].isoformat(sep=" ")))
        for game_id in [1, 2, 3, 4]:
            source = prepare_demo_sync(get_db(), "price-29", "clock-" + str(game_id), game_id=game_id)
            self.assertEqual(datetime.fromisoformat(source.observed_at), latest[game_id] + timedelta(microseconds=1))
            self.assertEqual(source.fetch("price-29")[0].game_id, game_id)

    def test_other_game_future_time_does_not_shift_selected_game_clock(self):
        with get_db() as db:
            db.execute("""INSERT INTO price_observations(source_event_id,game_id,price_cents,observed_at)
                VALUES ('far-future-one',1,100,'2099-01-01 00:00:00')""")
        fixed_now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        with patch("services.sync_inputs.datetime", wraps=datetime) as clock:
            clock.now.return_value = fixed_now
            source = prepare_demo_sync(get_db(), "price-29", "clock-two", game_id=2)
        self.assertEqual(datetime.fromisoformat(source.observed_at), fixed_now.replace(tzinfo=None))

    def test_global_event_replay_conflicting_price_and_conflicting_game(self):
        self.submit("2", "price-29", "global-event")
        before = self.histories()
        status = [tuple(row) for row in get_db().execute("SELECT * FROM sync_status ORDER BY source_name")]
        self.submit("2", "price-29", "global-event")
        self.assertEqual(self.histories(), before)
        self.assertEqual(self.last_log()["outcome"], "success")
        self.assertIn("新增历史 0 条", self.last_log()["summary"])
        for game_id, scenario in [("2", "price-30"), ("3", "price-29")]:
            with self.subTest(game_id=game_id, scenario=scenario):
                self.submit(game_id, scenario, "global-event")
                self.assertEqual(self.last_log()["outcome"], "failure")
                self.assertEqual(self.histories(), before)
        self.assertEqual(status, [tuple(row) for row in get_db().execute("SELECT * FROM sync_status ORDER BY source_name")])
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE source_event_id='demo-manual-global-event'").fetchone()[0], 1)

    def test_invalid_and_real_ids_do_not_write_prices_or_log_untrusted_id(self):
        before = self.histories()
        invalid_ids = ["", "0", "-1", "1.0", "1e0", "+1", "01", " 1", "1 ", "１", "5", "9999", "101",
                       "9223372036854775808", "9" * 5000, "1 OR 1=1"]
        for invalid_id in invalid_ids:
            with self.subTest(game_id=invalid_id[:40]):
                self.submit(invalid_id, event_id="invalid-target")
                self.assertEqual(self.last_log()["outcome"], "failure")
                self.assertEqual(self.histories(), before)
        response = self.admin.post("/admin/sync/demo", data={"scenario": "price-29", "event_id": "missing-game",
                                                            "csrf_token": self.token(self.admin)})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.last_log()["outcome"], "failure")
        self.assertNotIn("1 OR 1=1", str([dict(row) for row in get_db().execute("SELECT * FROM admin_action_log")]))

    def test_target_is_rechecked_against_database_demo_source(self):
        with get_db() as db:
            db.execute("UPDATE games SET data_source='cheapshark' WHERE id=4")
        before = self.histories()
        self.submit("4", event_id="source-mismatch")
        self.assertEqual(self.last_log()["outcome"], "failure")
        self.assertEqual(self.histories(), before)
        with self.assertRaises(DemoInputError):
            prepare_demo_sync(get_db(), "price-29", "bad-target", game_id=4)

    def test_nondefault_game_uses_shared_integer_scenario_limits(self):
        before = self.histories()
        for scenario in ["price--1", "price-1.5", "price-1e2", "price-2147483648", "price-drop", "late", "sequence", "failure"]:
            with self.subTest(scenario=scenario):
                self.submit("2", scenario, "invalid-scenario")
                self.assertEqual(self.last_log()["outcome"], "failure")
                self.assertEqual(before, self.histories())
        for amount in [0, 2147483647]:
            self.submit("2", "price-" + str(amount), "selected-boundary-" + str(amount))
            row = get_db().execute("SELECT price_cents,typeof(price_cents) FROM price_observations WHERE source_event_id=?",
                                   ("demo-manual-selected-boundary-" + str(amount),)).fetchone()
            self.assertEqual(tuple(row), (amount * 100, "integer"))

    def test_forged_metadata_and_credentials_never_control_target_or_audit(self):
        secret = "SECRET_UNTRUSTED_METADATA"
        token = self.token(self.admin)
        response = self.admin.post("/admin/sync/demo", data={"game_id": "2", "scenario": "price-29", "event_id": "verified-title",
            "title": secret, "data_source": "cheapshark", "user_id": str(self.bob_id), "price_unit": "CNY",
            "price": "0.01", "password": secret, "session": secret, "csrf_token": token})
        log = self.last_log()
        self.assertEqual(log["user_id"], self.admin_id)
        self.assertEqual(log["source"], "demo")
        self.assertIn("空洞骑士", log["summary"])
        self.assertIn("模拟游戏 ID 2", log["summary"])
        self.assertIn("price-29", log["summary"])
        self.assertIn("$29.00", log["summary"])
        self.assertNotIn(secret, log["summary"])
        self.assertNotIn(token, log["summary"])
        self.assertNotIn(TEST_HASH, log["summary"])
        page = self.admin.get(response.location).get_data(as_text=True)
        self.assertIn("已为“空洞骑士”（模拟游戏 ID 2）记录 $29.00", page)
        self.assertEqual(self.summaries()[2][0], 2900)
        self.assertEqual(len(self.histories()[102]), 0)

    def test_failure_audit_contains_only_confirmed_game_and_validated_scenario(self):
        self.submit("2", "price-29", "conflict")
        self.submit("3", "price-30", "conflict")
        log = self.last_log()
        self.assertEqual(log["outcome"], "failure")
        for text in ["模拟游戏 ID 3", "双人成行", "price-30", "$30.00"]:
            self.assertIn(text, log["summary"])
        self.submit("2", "SECRET_INVALID_SCENARIO", "valid-id")
        log = self.last_log()
        self.assertIn("模拟游戏 ID 2", log["summary"])
        self.assertIn("场景尚未通过校验", log["summary"])
        self.assertNotIn("SECRET_INVALID_SCENARIO", log["summary"])

    def test_only_selected_game_active_subscribers_receive_crossing_notifications(self):
        self.target(self.alice_id, 2)
        self.target(self.bob_id, 1)
        self.target(self.carol_id, 2, tracked=False)
        for price, expected in [(128, 0), (100, 1), (88, 1), (120, 1), (90, 2)]:
            self.submit("2", "price-" + str(price), "selected-notification-" + str(price))
            self.assertEqual(self.notice_count(self.alice_id), expected)
            self.assertEqual(self.notice_count(self.bob_id), 0)
            self.assertEqual(self.notice_count(self.carol_id), 0)
        self.submit("2", "price-90", "selected-notification-90")
        self.assertEqual(self.notice_count(self.alice_id), 2)
        self.assertEqual([row[0] for row in get_db().execute("SELECT DISTINCT game_id FROM notifications WHERE user_id=?", (self.alice_id,))], [2])

    def test_no_followers_still_allows_shared_price_without_notifications(self):
        with get_db() as db:
            db.execute("DELETE FROM user_game_tracking WHERE game_id=3")
        self.target(self.carol_id, 3, tracked=False)
        before = get_db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        self.submit("3", "price-0", "untracked-shared")
        self.assertEqual(self.summaries()[3][0], 0)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0], before)

    def test_selected_game_with_no_history_only_establishes_first_baseline(self):
        # Only this isolated in-memory fixture starts without game 4 history.
        with get_db() as db:
            db.execute("DELETE FROM price_observations WHERE game_id=4")
        self.target(self.alice_id, 4)
        self.submit("4", "price-90", "first-baseline")
        self.assertEqual(self.notice_count(self.alice_id), 0)
        self.submit("4", "price-120", "baseline-rise")
        self.submit("4", "price-90", "baseline-cross")
        self.assertEqual(self.notice_count(self.alice_id), 1)

    def test_selected_action_does_not_deliver_other_games_pending_retries(self):
        self.target(self.bob_id, 3)
        self.submit("3", "price-128", "other-retry-high")
        get_db().execute("""CREATE TRIGGER reject_other_game BEFORE INSERT ON notifications
            WHEN NEW.game_id=3 BEGIN SELECT RAISE(ABORT,'recipient test'); END""")
        self.submit("3", "price-90", "other-retry-low")
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notification_retries WHERE game_id=3").fetchone()[0], 1)
        get_db().execute("DROP TRIGGER reject_other_game")
        with get_db() as db:
            db.execute("DELETE FROM user_game_tracking WHERE game_id=2")
        pending = [tuple(row) for row in get_db().execute("SELECT * FROM notification_retries")]
        self.submit("2", "price-0", "selected-without-followers")
        self.assertEqual(self.notice_count(self.bob_id), 0)
        self.assertEqual(pending, [tuple(row) for row in get_db().execute("SELECT * FROM notification_retries")])
        self.assertIn("新增通知 0 条", self.last_log()["summary"])
        self.submit("3", "price-90", "other-retry-low")
        self.assertEqual(self.notice_count(self.bob_id), 1)

    def test_selected_game_permissions_and_csrf_are_still_required(self):
        before = self.histories()
        logs_before = get_db().execute("SELECT COUNT(*) FROM admin_action_log").fetchone()[0]
        self.assertEqual(self.submit(client=self.client(self.alice_id)).status_code, 403)
        self.assertEqual(self.submit(client=self.client()).status_code, 302)
        for data in [{"game_id": "2", "scenario": "price-29", "event_id": "csrf"},
                     {"game_id": "2", "scenario": "price-29", "event_id": "csrf", "csrf_token": "bad"}]:
            self.assertEqual(self.admin.post("/admin/sync/demo", data=data).status_code, 400)
        self.assertEqual(before, self.histories())
        self.assertEqual(logs_before, get_db().execute("SELECT COUNT(*) FROM admin_action_log").fetchone()[0])

    def test_cli_can_select_game_and_rejects_invalid_targets_and_legacy_mismatch(self):
        before = self.histories()
        result = self.runner.invoke(args=["sync-prices", "--game-id", "2", "--scenario", "price-29", "--event-id", "cli-selected"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(self.histories()[2]), len(before[2]) + 1)
        self.assertEqual(self.histories()[1], before[1])
        after = self.histories()
        for game_id in ["101", "9999", "-1", "2.0", "02", "999999999999999999999"]:
            self.assertEqual(self.runner.invoke(args=["sync-prices", "--game-id", game_id, "--scenario", "price-29"]).exit_code, 2)
        for scenario in ["price-drop", "late", "sequence", "failure"]:
            self.assertEqual(self.runner.invoke(args=["sync-prices", "--game-id", "2", "--scenario", scenario]).exit_code, 2)
        self.assertEqual(self.runner.invoke(args=["sync-prices", "--source", "cheapshark", "--game-id", "2"]).exit_code, 2)
        self.assertEqual(after, self.histories())

    def test_default_cli_and_legacy_events_keep_original_game_ids_and_times(self):
        source = DemoPriceSource()
        drop = source.fetch("price-drop")[0]
        self.assertEqual((drop.event_id, drop.game_id, drop.price_cents, drop.observed_at),
                         ("demo-stardew-drop-20261104-8800", 1, 8800, "2026-11-04 09:30:00"))
        late = source.fetch("late")[0]
        self.assertEqual((late.event_id, late.game_id, late.observed_at),
                         ("demo-stardew-late-20261015-8800", 1, "2026-10-15 09:30:00"))
        sequence = source.fetch("sequence")
        self.assertEqual([(e.event_id,e.game_id,e.price_cents,e.observed_at) for e in sequence], [
            ("demo-sequence-price-100",1,10000,"2026-11-05 10:00:00"),
            ("demo-sequence-price-88",1,8800,"2026-11-05 11:00:00"),
            ("demo-sequence-price-120",1,12000,"2026-11-05 12:00:00"),
            ("demo-sequence-price-90",1,9000,"2026-11-05 13:00:00")])
        self.assertEqual(self.runner.invoke(args=["sync-prices", "--scenario", "sequence"]).exit_code, 0)
        for scenario in ["price-drop", "late", "sequence", "failure"]:
            with self.assertRaises(ValueError):
                DemoPriceSource(game_id=2).fetch(scenario)

    def test_status_table_uses_one_aggregation_and_correct_follower_counts(self):
        with get_db() as db:
            db.execute("INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,2)", (self.alice_id,))
            db.execute("INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,2)", (self.bob_id,))
        self.submit("2", "price-29", "aggregate")
        statements = []
        get_db().set_trace_callback(statements.append)
        try:
            with patch("admin_sync.render_template", wraps=render_template) as renderer:
                response = self.admin.get("/admin/sync")
        finally:
            get_db().set_trace_callback(None)
        rows = {row["id"]: row for row in renderer.call_args.kwargs["demo_statistics"]}
        self.assertEqual(list(rows), [1, 2, 3, 4])
        self.assertEqual((rows[2]["current_price_cents"], rows[2]["lowest_price_cents"], rows[2]["history_count"], rows[2]["follower_count"]), (2900, 2300, 6, 3))
        self.assertEqual(len([sql for sql in statements if "lowest_price_cents" in sql and "follower_count" in sql]), 1)
        self.assertIn("模拟游戏共享状态", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
