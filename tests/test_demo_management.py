"""Fixed demo management, per-user states, local-only actions and safe returns."""
from html.parser import HTMLParser
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import app, get_db
from adapters.contracts import PriceObservationEvent
from adapters.demo_price_source import DemoPriceSource
from services.price_sync import sync_prices
from tests.helpers import TEST_HASH


class ManagementParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.form = None
        self.in_panel = False
        self.panel_text = []
        self.panel_links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and "panel" in attrs.get("class", "").split():
            self.in_panel = True
        if tag == "a" and self.in_panel:
            self.panel_links.append(attrs.get("href"))
        if tag == "tr" and "data-game-id" in attrs:
            self.row = {"id": int(attrs["data-game-id"]), "cells": [], "forms": [], "links": []}
        if self.row is None:
            return
        if tag == "td":
            self.cell = []
        if tag == "a":
            self.row["links"].append(attrs.get("href"))
        if tag == "form":
            self.form = {"action": attrs.get("action"), "method": attrs.get("method"), "inputs": {}}
            self.row["forms"].append(self.form)
        if tag == "input" and self.form is not None:
            self.form["inputs"][attrs.get("name")] = attrs.get("value")

    def handle_data(self, data):
        if self.in_panel:
            self.panel_text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "section":
            self.in_panel = False
        if tag == "td" and self.row is not None and self.cell is not None:
            self.row["cells"].append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "form":
            self.form = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


class DemoManagementTest(unittest.TestCase):
    def setUp(self):
        self.previous_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.assertEqual(app.test_cli_runner().invoke(args=["init-db"]).exit_code, 0)
        with get_db() as db:
            self.alice_id = db.execute("""INSERT INTO users(display_name,username,username_normalized,password_hash)
                VALUES ('Alice','Alice','alice',?)""", (TEST_HASH,)).lastrowid
            self.bob_id = db.execute("""INSERT INTO users(display_name,username,username_normalized,password_hash)
                VALUES ('Bob','Bob','bob',?)""", (TEST_HASH,)).lastrowid
        self.alice = self.client(self.alice_id)
        self.bob = self.client(self.bob_id)
        self.network = patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Demo management must stay offline"))
        self.opener = self.network.start()
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

    def change(self, client, game_id=1, action="add", **extra):
        return client.post(f"/games/{game_id}/demo-tracking", data={"action": action,
            "return_to": "management", **extra, "csrf_token": self.token(client)})

    def page(self, client):
        response = client.get("/games?source=demo")
        self.assertEqual(response.status_code, 200)
        return ManagementParser(response.get_data(as_text=True))

    def states(self, client):
        return [row["cells"][3] for row in self.page(client).rows]

    def business_records(self):
        return {name: [tuple(row) for row in get_db().execute(f"SELECT * FROM {name} ORDER BY 1,2")]
                for name in ["users", "games", "price_observations", "price_targets", "notifications",
                             "notification_retries", "sync_status", "schema_migrations", "admin_action_log"]}

    def relationships(self):
        return [tuple(row) for row in get_db().execute("SELECT * FROM user_game_tracking ORDER BY user_id,game_id")]

    def test_fixed_four_rows_and_public_current_simulated_prices(self):
        with get_db() as db:
            db.execute("""INSERT INTO price_observations(source_event_id,game_id,price_cents,observed_at)
                          VALUES ('real-price-must-not-appear',101,99999,'2026-11-07 12:00:00')""")
        before = self.business_records()
        for client in [self.alice, self.bob, self.client()]:
            page = self.page(client)
            self.assertEqual([row["id"] for row in page.rows], [1, 2, 3, 4])
            self.assertEqual([row["cells"][0] for row in page.rows], ["星露谷物语", "空洞骑士", "双人成行", "潜水员戴夫"])
            self.assertEqual([row["cells"][2] for row in page.rows], ["$128.00", "$29.00", "$79.00", "$64.00"])
            self.assertTrue(all(row["cells"][1] for row in page.rows))
            self.assertNotIn("$999.99", " ".join(page.panel_text))
            for row in page.rows:
                self.assertIn(f'/games/{row["id"]}', row["links"])
        self.assertEqual(before, self.business_records())

    def test_all_four_then_two_then_none_still_render_full_management_table(self):
        for game_id in [1, 2, 3, 4]:
            self.assertEqual(self.change(self.alice, game_id).status_code, 303)
        self.assertEqual(self.states(self.alice), ["已追踪"] * 4)
        for game_id in [2, 4]:
            self.change(self.alice, game_id, "remove")
        self.assertEqual(self.states(self.alice), ["已追踪", "未追踪", "已追踪", "未追踪"])
        for game_id in [1, 3]:
            self.change(self.alice, game_id, "remove")
        page = self.page(self.alice)
        self.assertEqual([row["id"] for row in page.rows], [1, 2, 3, 4])
        self.assertEqual([row["cells"][3] for row in page.rows], ["未追踪"] * 4)
        self.assertTrue(all(row["forms"][0]["inputs"]["action"] == "add" for row in page.rows))
        self.assertIn("你暂未追踪模拟游戏，可以在下表选择固定的演示游戏。", "".join(page.panel_text))

    def test_demo_empty_state_does_not_offer_search_as_adding_method(self):
        response = self.alice.get("/games?source=demo")
        page = ManagementParser(response.get_data(as_text=True))
        self.assertNotIn("/games/search", page.panel_links)
        self.assertNotIn("搜索并加入游戏", "".join(page.panel_text))
        self.assertIn("模拟游戏管理表", response.get_data(as_text=True))
        self.assertNotIn("暂无已追踪游戏，请到", response.get_data(as_text=True))

    def test_user_states_cancellation_and_admin_are_personal(self):
        self.change(self.alice)
        self.assertEqual(self.states(self.alice), ["已追踪", "未追踪", "未追踪", "未追踪"])
        self.assertEqual(self.states(self.bob), ["未追踪"] * 4)
        self.change(self.bob)
        self.change(self.alice, action="remove")
        self.assertEqual(self.states(self.alice), ["未追踪"] * 4)
        self.assertEqual(self.states(self.bob)[0], "已追踪")
        with get_db() as db:
            db.execute("UPDATE users SET role='admin' WHERE id=?", (self.alice_id,))
        self.assertEqual(self.states(self.alice), ["未追踪"] * 4)
        self.assertIn('href="/admin/sync"', self.alice.get("/games?source=demo").get_data(as_text=True))
        self.assertIsNotNone(get_db().execute("SELECT 1 FROM user_game_tracking WHERE user_id=? AND game_id=1", (self.bob_id,)).fetchone())

    def test_repeated_add_and_remove_are_idempotent_and_use_prg(self):
        for _ in range(2):
            result = self.change(self.alice)
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.location, "/games?source=demo#demo-game-1")
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM user_game_tracking WHERE user_id=? AND game_id=1", (self.alice_id,)).fetchone()[0], 1)
        rows = self.relationships()
        self.alice.get(result.location)
        self.assertEqual(rows, self.relationships())
        for _ in range(2):
            self.assertEqual(self.change(self.alice, action="remove").location, "/games?source=demo#demo-game-1")
        self.assertEqual(self.states(self.alice)[0], "未追踪")

    def test_management_and_actions_never_call_external_adapters(self):
        with patch("adapters.cheapshark.CheapSharkSource.search_by_title") as search, \
             patch("adapters.cheapshark.CheapSharkSource.get_verified_steam_game") as lookup, \
             patch("adapters.cheapshark.CheapSharkSource.fetch") as fetch:
            self.page(self.alice)
            self.change(self.alice)
            self.change(self.alice, action="remove")
            self.alice.get("/games/1")
        for mock in [search, lookup, fetch, self.opener]:
            mock.assert_not_called()

    def test_tracking_keeps_shared_history_target_and_old_notices_without_catchup(self):
        for client in [self.alice, self.bob]:
            self.change(client)
            client.post("/games/1/target", data={"target_price": "100.00", "csrf_token": self.token(client)})
        sync_prices(get_db(), DemoPriceSource(), scenario="price-drop")
        before = self.business_records()
        self.change(self.alice, action="remove")
        self.assertEqual(before, self.business_records())
        notices_before = get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=?", (self.alice_id,)).fetchone()[0]
        events = [PriceObservationEvent("management-up", 1, 12000, "2026-11-07 10:00:00"),
                  PriceObservationEvent("management-down", 1, 9000, "2026-11-07 11:00:00")]
        sync_prices(get_db(), SimpleNamespace(name="management-test", fetch=lambda scenario: events))
        paused = self.business_records()
        self.change(self.alice)
        self.assertEqual(paused, self.business_records())
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=?", (self.alice_id,)).fetchone()[0], notices_before)
        self.assertIn('value="100.00"', self.alice.get("/games/1").get_data(as_text=True))
        self.assertEqual(sync_prices(get_db(), SimpleNamespace(name="management-test", fetch=lambda scenario: events)).notifications, 0)

    def test_forged_user_ids_in_form_query_or_json_do_not_choose_owner(self):
        self.change(self.bob)
        self.change(self.alice, user_id=str(self.bob_id))
        response = self.alice.post(f"/games/1/demo-tracking?user_id={self.bob_id}", data={
            "action": "remove", "user_id": str(self.bob_id), "csrf_token": self.token(self.alice), "return_to": "management"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.states(self.bob)[0], "已追踪")
        self.assertEqual(self.states(self.alice)[0], "未追踪")
        before = self.relationships()
        response = self.alice.post("/games/1/demo-tracking", json={"action": "remove", "user_id": self.bob_id},
                                   headers={"X-CSRF-Token": self.token(self.alice)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, self.relationships())
        self.assertEqual(self.states(self.alice), [row["cells"][3] for row in ManagementParser(
            self.alice.get(f"/games?source=demo&user_id={self.bob_id}").get_data(as_text=True)).rows])

    def test_anonymous_get_is_public_without_action_buttons_and_post_requires_login(self):
        anonymous = self.client()
        page = self.page(anonymous)
        self.assertEqual([row["cells"][3] for row in page.rows], ["登录后管理个人追踪"] * 4)
        self.assertTrue(all(not row["forms"] for row in page.rows))
        self.assertTrue(any(link.startswith("/login?next=") for link in page.panel_links))
        before = self.relationships()
        for action in ["add", "remove"]:
            result = self.change(anonymous, action=action)
            self.assertEqual(result.status_code, 302)
            self.assertTrue(result.location.startswith("/login?next="))
        self.assertEqual(before, self.relationships())

    def test_csrf_invalid_action_and_real_ids_are_rejected(self):
        before = self.relationships()
        for data in [{"action": "add"}, {"action": "remove", "csrf_token": "bad"}]:
            self.assertEqual(self.alice.post("/games/1/demo-tracking", data=data).status_code, 400)
        self.assertEqual(self.change(self.alice, action="toggle").status_code, 400)
        for game_id in [0, 5, 101, 9999]:
            self.assertEqual(self.change(self.alice, game_id).status_code, 404)
        self.assertEqual(self.alice.get("/games/1/demo-tracking").status_code, 405)
        self.assertEqual(before, self.relationships())

    def test_detail_actions_and_fixed_return_destinations(self):
        token = self.token(self.alice)
        result = self.alice.post("/games/1/demo-tracking", data={"action": "add", "csrf_token": token})
        self.assertEqual(result.location, "/games/1")
        self.assertIn("取消模拟追踪", self.alice.get("/games/1").get_data(as_text=True))
        self.assertEqual(self.states(self.alice)[0], "已追踪")
        result = self.change(self.alice, action="remove", return_to="https://evil.invalid")
        self.assertEqual(result.location, "/games?source=demo#demo-game-1")
        result = self.change(self.alice, return_to="//evil.invalid")
        self.assertEqual(result.location, "/games/1")
        self.assertEqual(self.states(self.alice)[0], "已追踪")

    def test_continuous_management_actions_return_to_each_verified_row_with_visible_result(self):
        expected = ["未追踪"] * 4
        for action in ["add", "remove"]:
            for game_id in [1, 2, 3, 4]:
                with self.subTest(action=action, game_id=game_id):
                    result = self.change(self.alice, game_id, action)
                    self.assertEqual(result.status_code, 303)
                    self.assertEqual(result.location, f"/games?source=demo#demo-game-{game_id}")
                    html = self.alice.get(result.location).get_data(as_text=True)
                    self.assertIn('id="demo-game-management"', html)
                    page = ManagementParser(html)
                    self.assertEqual([row["id"] for row in page.rows], [1, 2, 3, 4])
                    expected[game_id - 1] = "已追踪" if action == "add" else "未追踪"
                    self.assertEqual([row["cells"][3] for row in page.rows], expected)
                    row = html.split(f'id="demo-game-{game_id}"', 1)[1].split('</tr>', 1)[0]
                    self.assertNotIn('role="status"', row)
                    feedback = html.split('class="demo-management-feedback"', 1)[1].split('</div>', 1)[0]
                    self.assertIn('role="status"', feedback)
                    self.assertIn("已加入你的模拟追踪" if action == "add" else "已取消你的模拟追踪", feedback)
        self.opener.assert_not_called()

    def test_feedback_does_not_change_table_cell_contents(self):
        for action in ["add", "remove", "add"]:
            result = self.change(self.alice, 2, action)
            html = self.alice.get(result.location).get_data(as_text=True)
            table = html.split('<table aria-label="固定四款模拟游戏">', 1)[1].split('</table>', 1)[0]
            self.assertNotIn('class="flash', table)
            page = ManagementParser(html)
            self.assertEqual(len(page.rows), 4)
            self.assertTrue(all(len(row["cells"]) == 6 for row in page.rows))
            self.assertEqual(page.rows[1]["cells"][5], "取消模拟追踪" if action == "add" else "加入模拟追踪")
    def test_explicit_detail_return_and_untrusted_urls_cannot_choose_destination(self):
        for action in ["add", "remove"]:
            result = self.change(self.alice, 2, action, return_to="detail", return_url="https://evil.invalid")
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.location, "/games/2")
        # A form's game_id cannot replace the already-validated route ID.
        result = self.alice.post("/games/2/demo-tracking", data={
            "game_id": "4", "action": "add", "return_to": "management", "return_url": "//evil.invalid",
            "csrf_token": self.token(self.alice)})
        self.assertEqual(result.location, "/games?source=demo#demo-game-2")
        for marker in ["https://evil.invalid", "//evil.invalid", "management#forged", "detail?next=https://evil.invalid"]:
            result = self.change(self.alice, 2, "remove", return_to=marker, return_url=marker)
            self.assertEqual(result.location, "/games?source=demo#demo-game-2")
        self.assertIn('name="return_to" value="detail"', self.alice.get("/games/2").get_data(as_text=True))

    def test_demo_category_link_is_stable_even_when_following_other_game(self):
        self.change(self.alice, 4)
        for path in ["/games/4", "/games/101", "/notifications", "/games?source=demo", "/games/search"]:
            response = self.alice.get(path)
            self.assertIn('href="/games?source=demo"', response.get_data(as_text=True))
        self.assertEqual(self.alice.get("/games?source=demo").status_code, 200)
        self.assertEqual(len(self.page(self.alice).rows), 4)

    def test_real_empty_category_keeps_search_guidance(self):
        response = self.alice.get("/games?source=cheapshark")
        self.assertEqual(response.status_code, 200)
        page = ManagementParser(response.get_data(as_text=True))
        self.assertEqual(page.rows, [])
        self.assertIn("你暂未追踪真实报价游戏，可以前往“搜索并加入游戏”。", "".join(page.panel_text))
        self.assertIn("/games/search", page.panel_links)

    def test_table_prices_follow_latest_shared_observation_not_user_ownership(self):
        events = [PriceObservationEvent("new-demo-price", 1, 0, "2026-11-07 10:00:00")]
        sync_prices(get_db(), SimpleNamespace(name="management-test", fetch=lambda scenario: events))
        self.change(self.alice)
        self.assertEqual(self.page(self.alice).rows[0]["cells"][2], "$0.00")
        self.assertEqual(self.page(self.bob).rows[0]["cells"][2], "$0.00")
        self.assertEqual(self.page(self.client()).rows[0]["cells"][2], "$0.00")


if __name__ == "__main__":
    unittest.main()
