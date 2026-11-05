"""Personal lists, shared prices, recipient checks and atomic legacy upgrades."""
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app import app, get_db
from adapters.cheapshark import GameSearchResult, VerifiedSteamGame
from adapters.contracts import PriceObservationEvent
from services.price_sync import load_sync_games, sync_prices, sync_real_prices
from services.game_tracking import track_verified_game
from tests.test_game_tracking import NavigationParser
from tests.helpers import TEST_HASH


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())
        self.headers = {}


class PersonalTrackingTest(unittest.TestCase):
    def setUp(self):
        self.previous_config = dict(app.config)
        self.temp = tempfile.TemporaryDirectory(dir=Path(app.instance_path))
        self.database = Path(self.temp.name) / "test.sqlite3"
        app.config.update(TESTING=True, DATABASE=self.database)
        self.ctx = app.app_context()
        self.ctx.push()
        self.runner = app.test_cli_runner()
        self.assertEqual(self.runner.invoke(args=["init-db"]).exit_code, 0)
        with get_db() as db:
            db.execute("UPDATE games SET cheapshark_game_id=CAST(150000+id AS TEXT) WHERE data_source='cheapshark'")
        self.alice, self.alice_id = self.new_user("Alice")
        self.bob, self.bob_id = self.new_user("Bob")
        self.network = patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Offline only"))
        self.opener = self.network.start()
        self.addCleanup(self.network.stop)

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.previous_config)
        self.temp.cleanup()

    def token(self, client):
        client.get("/login")
        with client.session_transaction() as state:
            return state["_csrf_token"]

    def post(self, client, path, data):
        return client.post(path, data={**data, "csrf_token": self.token(client)})

    def new_user(self, username):
        client = app.test_client()
        data = {"username": username, "password": "Offline123!"}
        self.assertEqual(self.post(client, "/register", data).status_code, 303)
        self.assertEqual(self.post(client, "/login", data).status_code, 303)
        with client.session_transaction() as state:
            return client, state["user_id"]

    def track(self, client, steam_id="413150", **extra):
        return self.post(client, "/games/track", {"steam_app_id": steam_id, **extra})

    def cancel(self, client, **extra):
        return self.post(client, "/games/untrack?return_to=detail", {"steam_app_id": "413150", **extra})

    def target(self, client, amount="100.00"):
        return self.post(client, "/games/101/target", {"target_price": amount})

    def followed(self, user_id, game_id=101):
        return get_db().execute("SELECT 1 FROM user_game_tracking WHERE user_id=? AND game_id=?", (user_id, game_id)).fetchone() is not None

    def event(self, key, price, hour):
        return PriceObservationEvent(key, 101, price, f"2026-11-07 {hour:02d}:00:00")

    def sync(self, events):
        return sync_prices(get_db(), SimpleNamespace(name="personal-test", fetch=lambda scenario: events))

    def notice_count(self, user_id):
        return get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=?", (user_id,)).fetchone()[0]

    def records(self):
        return {table: [tuple(row) for row in get_db().execute(f"SELECT * FROM {table} ORDER BY 1,2")]
                for table in ["users", "games", "price_observations", "price_targets", "notifications", "notification_retries", "sync_status"]}

    def search(self, client):
        with patch("app.CheapSharkSource.search_by_title", return_value=[GameSearchResult("150101", "Shared game", "413150", 100)]):
            return client.get("/games/search?q=Shared").get_data(as_text=True)

    def test_first_add_and_search_status_are_personal(self):
        self.assertFalse(self.followed(self.alice_id))
        self.assertFalse(self.followed(self.bob_id))
        before = self.records()
        result = self.track(self.alice)
        self.assertEqual(result.status_code, 303)
        self.assertTrue(result.location.endswith("/games/101"))
        self.assertTrue(self.followed(self.alice_id))
        self.assertFalse(self.followed(self.bob_id))
        self.assertIn('title="点击取消追踪">已追踪', self.search(self.alice))
        self.assertIn(">加入追踪</button>", self.search(self.bob))
        self.assertNotIn('class="tracking-active"', self.search(self.bob))
        self.assertEqual(before, self.records())
        self.opener.assert_not_called()

    def test_second_user_and_repeated_posts_reuse_archive_history_and_relationship(self):
        self.track(self.alice)
        self.sync([self.event("shared-baseline", 12800, 10)])
        before = self.records()
        for _ in range(3):
            self.post(self.bob, "/games/track", {"cheapshark_game_id": "150101", "title": "Fake", "price": "0", "game_id": "104"})
        self.assertEqual(before, self.records())
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM user_game_tracking WHERE game_id=101").fetchone()[0], 2)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM games WHERE steam_app_id='413150'").fetchone()[0], 1)

    def test_verified_requests_racing_to_create_share_one_archive(self):
        verified = VerifiedSteamGame("555", "9999", "Verified game", 100)
        first, created = track_verified_game(get_db(), verified, self.alice_id)
        second, created_again = track_verified_game(get_db(), verified, self.bob_id)
        third, _ = track_verified_game(get_db(), verified, self.alice_id)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual((first, second), (second, third))
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM games WHERE cheapshark_game_id='555'").fetchone()[0], 1)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM user_game_tracking WHERE game_id=?", (first,)).fetchone()[0], 2)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE game_id=?", (first,)).fetchone()[0], 0)

    def test_relationship_failure_rolls_back_new_archive_as_well(self):
        before = self.records()
        with self.assertRaises(sqlite3.IntegrityError):
            track_verified_game(get_db(), VerifiedSteamGame("555", "9999", "Verified game", 100), 999999)
        self.assertEqual(before, self.records())

    def test_cancelling_one_owner_preserves_other_owner_and_all_existing_data(self):
        for client in [self.alice, self.bob]:
            self.track(client)
            self.target(client)
        self.sync([self.event("high", 12800, 10), self.event("low", 10000, 11)])
        before = self.records()
        for _ in range(2):
            self.cancel(self.alice)
        self.assertFalse(self.followed(self.alice_id))
        self.assertTrue(self.followed(self.bob_id))
        self.assertEqual(before, self.records())
        self.assertEqual([game.game_id for game in load_sync_games(get_db())], [101])
        self.assertIn("加入我的追踪", self.alice.get("/games/101").get_data(as_text=True))
        self.assertIn('class="cancel-tracking"', self.bob.get("/games/101").get_data(as_text=True))

    def test_union_requests_shared_game_once_and_cli_distinguishes_relationships(self):
        self.track(self.alice)
        self.track(self.bob)
        with get_db() as db:
            for number in range(98):
                user_id = db.execute("""INSERT INTO users(display_name,username,username_normalized,password_hash)
                    VALUES (?,?,?,?)""", (f"extra{number}", f"extra{number}", f"extra{number}", TEST_HASH)).lastrowid
                db.execute("INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,101)", (user_id,))
        payload = [{"storeID": "1", "steamAppID": "413150", "salePrice": "12.80", "lastChange": 100}]
        with patch("adapters.cheapshark.urlopen", side_effect=lambda *args, **kwargs: Response(payload)) as opener:
            result = self.runner.invoke(args=["sync-prices", "--source", "cheapshark"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(opener.call_count, 1)
        query = parse_qs(urlparse(opener.call_args.args[0].full_url).query)
        self.assertEqual(query["steamAppID"], ["413150"])
        self.assertIn("用户追踪关系总数（真实）100", result.output)
        self.assertIn("去重后待同步游戏数 1", result.output)
        self.assertIn("新增历史数 1", result.output)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE game_id=101").fetchone()[0], 1)
        self.cancel(self.alice)
        self.assertEqual(len(load_sync_games(get_db())), 1)

    def test_cancellation_suspends_only_owner_notifications_and_rejoin_never_catches_up(self):
        for client in [self.alice, self.bob]:
            self.track(client)
            self.target(client)
        high, first_low = self.event("high", 12800, 10), self.event("first-low", 10000, 11)
        self.sync([high, first_low])
        self.cancel(self.alice)
        up, low = self.event("up", 12000, 12), self.event("low", 9000, 13)
        self.assertEqual(self.sync([up, low]).notifications, 1)
        self.assertEqual((self.notice_count(self.alice_id), self.notice_count(self.bob_id)), (1, 2))
        before = self.records()
        self.track(self.alice)
        self.assertEqual(before, self.records())
        self.assertIn('value="100.00"', self.alice.get("/games/101").get_data(as_text=True))
        self.assertEqual(self.sync([high, first_low, up, low]).notifications, 0)
        self.assertEqual((self.notice_count(self.alice_id), self.notice_count(self.bob_id)), (1, 2))
        self.assertEqual(self.sync([self.event("next-up", 12000, 14), self.event("next-low", 9000, 15)]).notifications, 2)

    def test_last_cancel_stops_real_fetch_and_rejoin_keeps_same_archive(self):
        self.track(self.alice)
        self.sync([self.event("high", 12800, 10)])
        self.cancel(self.alice)
        before = self.records()
        self.assertEqual(load_sync_games(get_db()), [])
        with patch("adapters.cheapshark.urlopen") as opener:
            result = sync_real_prices(get_db())
        opener.assert_not_called()
        self.assertIsNone(result.result)
        self.assertEqual(self.alice.get("/games/101").status_code, 200)
        self.track(self.alice)
        self.assertEqual(before, self.records())
        self.assertEqual([game.game_id for game in load_sync_games(get_db())], [101])

    def test_targets_alone_never_follow_or_receive_new_notifications(self):
        self.target(self.alice)
        self.track(self.bob)
        self.target(self.bob)
        self.assertFalse(self.followed(self.alice_id))
        self.sync([self.event("high", 12800, 10), self.event("low", 9000, 11)])
        self.assertEqual((self.notice_count(self.alice_id), self.notice_count(self.bob_id)), (0, 1))

    def test_pending_retries_check_the_recipient_not_any_follower(self):
        for client in [self.alice, self.bob]:
            self.track(client)
            self.target(client)
        get_db().execute("""CREATE TRIGGER reject_notices BEFORE INSERT ON notifications
                            BEGIN SELECT RAISE(ABORT,'recipient test'); END""")
        low = self.event("low", 9000, 11)
        result = self.sync([self.event("high", 12800, 10), low])
        self.assertEqual(result.pending_notifications, 2)
        self.cancel(self.alice)
        get_db().execute("DROP TRIGGER reject_notices")
        result = self.sync([low])
        self.assertEqual((result.notifications, result.pending_notifications), (1, 1))
        pending = get_db().execute("SELECT user_id,attempts FROM notification_retries").fetchone()
        self.assertEqual(tuple(pending), (self.alice_id, 1))
        self.assertEqual((self.notice_count(self.alice_id), self.notice_count(self.bob_id)), (0, 1))
        self.track(self.alice)
        self.assertEqual(self.notice_count(self.alice_id), 0)
        self.assertEqual(self.sync([low]).notifications, 1)
        self.assertEqual(self.sync([low]).notifications, 0)

    def test_forged_form_query_and_json_cannot_choose_owner(self):
        self.track(self.bob)
        self.post(self.alice, f"/games/track?user_id={self.bob_id}",
                  {"cheapshark_game_id": "150101", "user_id": str(self.bob_id), "role": "admin"})
        self.assertTrue(self.followed(self.alice_id))
        self.post(self.alice, f"/games/untrack?user_id={self.bob_id}",
                  {"steam_app_id": "413150", "user_id": str(self.bob_id)})
        self.assertFalse(self.followed(self.alice_id))
        self.assertTrue(self.followed(self.bob_id))
        for path in ["/games/track", "/games/untrack"]:
            response = self.alice.post(path, json={"steam_app_id": "413150", "user_id": self.bob_id},
                                       headers={"X-CSRF-Token": self.token(self.alice)})
            self.assertEqual(response.status_code, 303)
        self.assertFalse(self.followed(self.alice_id))
        self.assertTrue(self.followed(self.bob_id))

    def test_anonymous_get_and_bad_csrf_cannot_change_relationships(self):
        anonymous = app.test_client()
        before = [tuple(row) for row in get_db().execute("SELECT * FROM user_game_tracking")]
        for path in ["/games/track", "/games/untrack"]:
            self.assertEqual(anonymous.get(path).status_code, 405)
            self.assertEqual(self.post(anonymous, path, {"steam_app_id": "413150"}).status_code, 302)
            self.assertEqual(self.alice.post(path, data={"steam_app_id": "413150", "csrf_token": "bad"}).status_code, 400)
        self.assertEqual(before, [tuple(row) for row in get_db().execute("SELECT * FROM user_game_tracking")])
        self.assertNotIn('action="/games/track"', self.search(anonymous))

    def test_navigation_categories_and_admin_only_use_own_relationships(self):
        self.track(self.alice)
        self.track(self.bob, "367520")
        for client, expected in [(self.alice, ["星露谷物语"]), (self.bob, ["空洞骑士"])]:
            response = client.get("/games?source=cheapshark", follow_redirects=True)
            self.assertEqual([item["text"] for item in NavigationParser(response.data).bars["游戏列表"]], expected)
        admin, admin_id = self.new_user("AdminTest")
        with get_db() as db:
            db.execute("UPDATE users SET role='admin' WHERE id=?", (admin_id,))
        page = admin.get("/games?source=cheapshark")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(NavigationParser(page.data).bars["游戏列表"], [])
        self.assertIn("管理员", page.get_data(as_text=True))
        self.assertIn("加入我的追踪", admin.get("/games/101").get_data(as_text=True))
        self.assertEqual(app.test_client().get("/games/101").status_code, 200)

    def test_demo_tracking_is_personal_and_separate_from_real_archive(self):
        self.post(self.alice, "/games/1/demo-tracking", {"action": "add", "user_id": str(self.bob_id)})
        self.assertTrue(self.followed(self.alice_id, 1))
        self.assertFalse(self.followed(self.bob_id, 1))
        self.assertFalse(self.followed(self.alice_id, 101))
        self.post(self.bob, "/games/1/demo-tracking", {"action": "add"})
        self.post(self.alice, "/games/1/demo-tracking", {"action": "remove", "user_id": str(self.bob_id)})
        self.assertTrue(self.followed(self.bob_id, 1))
        self.assertFalse(self.followed(self.alice_id, 1))
        self.assertEqual(self.post(self.alice, "/games/101/demo-tracking", {"action": "add"}).status_code, 404)
        self.assertEqual(self.alice.post("/games/1/demo-tracking", data={"action": "add"}).status_code, 400)

    def cancel_from_other_connection(self, user_id):
        other = sqlite3.connect(self.database)
        try:
            other.execute("PRAGMA foreign_keys=ON")
            with other:
                other.execute("DELETE FROM user_game_tracking WHERE user_id=? AND game_id=101", (user_id,))
        finally:
            other.close()

    def test_last_cancellation_during_api_fetch_skips_writes_and_notifications(self):
        self.track(self.alice)
        self.target(self.alice)
        self.sync([self.event("high", 12800, 10)])
        selected = load_sync_games(get_db())
        self.assertEqual([game.game_id for game in selected], [101])
        before = self.records()
        def respond(request, **kwargs):
            self.assertFalse(get_db().in_transaction)
            self.cancel_from_other_connection(self.alice_id)
            return Response([{"storeID": "1", "steamAppID": "413150", "salePrice": "90.00", "lastChange": 200}])
        with patch("adapters.cheapshark.urlopen", side_effect=respond):
            result = sync_real_prices(get_db()).result
        self.assertEqual((result.received, result.inserted, result.notifications), (1, 0, 0))
        after = self.records()
        for table in before:
            if table != "sync_status":
                self.assertEqual(before[table], after[table])
        self.assertEqual(load_sync_games(get_db()), [])

    def test_one_cancellation_during_fetch_still_writes_once_for_remaining_owner(self):
        for client in [self.alice, self.bob]:
            self.track(client)
            self.target(client)
        self.sync([self.event("high", 12800, 10)])
        def fetch(scenario):
            self.cancel_from_other_connection(self.alice_id)
            return [self.event("low", 9000, 11)]
        result = sync_prices(get_db(), SimpleNamespace(name="personal-test", fetch=fetch))
        self.assertEqual((result.inserted, result.notifications), (1, 1))
        self.assertEqual((self.notice_count(self.alice_id), self.notice_count(self.bob_id)), (0, 1))

    def make_legacy_schema(self):
        db = get_db()
        db.execute("ALTER TABLE games ADD COLUMN is_tracked INTEGER NOT NULL DEFAULT 1")
        db.execute("UPDATE games SET is_tracked=0 WHERE id=102")
        db.execute("DROP TABLE user_game_tracking")
        db.execute("DELETE FROM schema_migrations WHERE version='user_game_tracking_v1'")
        db.commit()

    def test_legacy_migration_is_once_preserves_every_record_and_excludes_cancelled_games(self):
        for client in [self.alice, self.bob]:
            self.track(client)
            self.target(client)
        get_db().execute(f"""CREATE TRIGGER reject_alice BEFORE INSERT ON notifications
            WHEN NEW.user_id={self.alice_id} BEGIN SELECT RAISE(ABORT,'recipient test'); END""")
        self.sync([self.event("high", 12800, 10), self.event("low", 9000, 11)])
        get_db().execute("DROP TRIGGER reject_alice")
        self.make_legacy_schema()
        before = self.records()
        expected = {(row[0], row[1]) for row in get_db().execute("SELECT u.id,g.id FROM users u CROSS JOIN games g WHERE g.is_tracked=1")}
        self.assertEqual(self.runner.invoke(args=["upgrade-db"]).exit_code, 0)
        self.assertEqual(before, self.records())
        self.assertEqual(expected, {tuple(row) for row in get_db().execute("SELECT user_id,game_id FROM user_game_tracking")})
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM user_game_tracking WHERE game_id=102").fetchone()[0], 0)
        self.cancel(self.alice)
        new_client, new_id = self.new_user("AfterMigration")
        after_cancel = [tuple(row) for row in get_db().execute("SELECT * FROM user_game_tracking ORDER BY user_id,game_id")]
        for _ in range(2):
            self.assertEqual(self.runner.invoke(args=["upgrade-db"]).exit_code, 0)
            self.assertEqual(after_cancel, [tuple(row) for row in get_db().execute("SELECT * FROM user_game_tracking ORDER BY user_id,game_id")])
        self.assertFalse(self.followed(self.alice_id))
        self.assertTrue(self.followed(self.bob_id))
        self.assertFalse(self.followed(new_id))
        self.assertEqual(get_db().execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_upgrade_rolls_back_all_upgrade_steps_and_marker(self):
        self.make_legacy_schema()
        db = get_db()
        sql = (Path(app.root_path) / "schema.sql").read_text(encoding="utf-8")
        db.execute(re.search(r"CREATE TABLE user_game_tracking \(.*?\);", sql, re.S)[0])
        db.execute(f"""CREATE TRIGGER reject_backfill BEFORE INSERT ON user_game_tracking
            WHEN NEW.user_id={self.bob_id} BEGIN SELECT RAISE(ABORT,'migration test'); END""")
        db.execute("DROP TABLE notification_retries")
        db.execute("DROP INDEX uq_real_steam_app_id")
        db.commit()
        old_games = [tuple(row) for row in db.execute("SELECT * FROM games ORDER BY id")]
        result = self.runner.invoke(args=["upgrade-db"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("已回滚", result.output)
        self.assertEqual(old_games, [tuple(row) for row in db.execute("SELECT * FROM games ORDER BY id")])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM user_game_tracking").fetchone()[0], 0)
        self.assertIsNone(db.execute("SELECT 1 FROM schema_migrations WHERE version='user_game_tracking_v1'").fetchone())
        self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='notification_retries'").fetchone())
        self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='uq_real_steam_app_id'").fetchone())

    def test_runtime_ignores_deprecated_flag_and_new_schema_has_none(self):
        self.assertNotIn("is_tracked", {row[1] for row in get_db().execute("PRAGMA table_info(games)")})
        self.track(self.alice)
        get_db().execute("ALTER TABLE games ADD COLUMN is_tracked INTEGER NOT NULL DEFAULT 1")
        get_db().execute("UPDATE games SET is_tracked=0 WHERE id=101")
        get_db().commit()
        statements = []
        get_db().set_trace_callback(statements.append)
        try:
            self.assertEqual([game.game_id for game in load_sync_games(get_db())], [101])
            self.assertIn('class="cancel-tracking"', self.alice.get("/games/101").get_data(as_text=True))
            self.assertIn("加入我的追踪", self.bob.get("/games/101").get_data(as_text=True))
            self.cancel(self.alice)
            self.assertEqual(load_sync_games(get_db()), [])
        finally:
            get_db().set_trace_callback(None)
        self.assertFalse(any("is_tracked" in statement for statement in statements))

    def test_relationship_primary_key_and_foreign_keys_are_enforced(self):
        self.track(self.alice)
        with self.assertRaises(sqlite3.IntegrityError):
            with get_db() as db:
                db.execute("INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,101)", (self.alice_id,))
        for user_id, game_id in [(999999, 101), (self.alice_id, 999999)]:
            with self.assertRaises(sqlite3.IntegrityError):
                with get_db() as db:
                    db.execute("INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,?)", (user_id, game_id))
        index = get_db().execute("PRAGMA index_info(idx_tracking_game_user)").fetchall()
        self.assertEqual([row[2] for row in index], ["game_id", "user_id"])


if __name__ == "__main__":
    unittest.main()
