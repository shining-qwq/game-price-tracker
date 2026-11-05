"""Shared CLI/web demo validation and monotonic observation time preparation."""
from datetime import datetime, timedelta, timezone
import re

from adapters.demo_price_source import DemoPriceSource, DEMO_GAME_IDS, LEGACY_SCENARIOS, validate_scenario


class DemoInputError(ValueError):
    """Validation messages contain only fixed rules, never submitted values."""


def load_demo_games(db):
    rows = db.execute("SELECT id,title FROM games WHERE data_source='demo' ORDER BY id").fetchall()
    return [row for row in rows if row["id"] in DEMO_GAME_IDS]


def resolve_demo_game(db, game_id):
    if type(game_id) is int:
        game_id = str(game_id)
    if not isinstance(game_id, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", game_id):
        raise DemoInputError("模拟游戏 ID 必须为规范的正整数，请从四款游戏中选择。")
    selected_id = int(game_id)
    if selected_id not in DEMO_GAME_IDS:
        raise DemoInputError("只能选择现有四款模拟游戏，不能修改真实报价或其他游戏。")
    game = db.execute("SELECT id,title FROM games WHERE id=? AND data_source='demo'", (selected_id,)).fetchone()
    if game is None:
        raise DemoInputError("所选模拟游戏不存在或不属于模拟数据。")
    return game


def validate_demo_request(scenario, event_id=None, *, integer_only=False, require_event_id=False):
    try:
        validate_scenario(scenario)
    except ValueError as exc:
        raise DemoInputError(str(exc)) from None
    if integer_only and scenario in LEGACY_SCENARIOS:
        raise DemoInputError("控制台仅支持 price-整数美元场景，不接受固定兼容场景。")
    if require_event_id and event_id is None:
        raise DemoInputError("请输入事件 ID；重复提交同一事件请使用相同 ID。")
    if event_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", event_id):
        raise DemoInputError("事件 ID 只支持 1～80 个字母、数字、下划线或短横线。")
    if event_id is not None and scenario in LEGACY_SCENARIOS:
        raise DemoInputError("事件 ID 仅可与 price-整数场景一起使用。")


def prepare_demo_sync(db, scenario, event_id=None, *, game_id=1, integer_only=False, require_event_id=False):
    game = resolve_demo_game(db, game_id)
    validate_demo_request(scenario, event_id, integer_only=integer_only, require_event_id=require_event_id)
    if scenario in LEGACY_SCENARIOS and game["id"] != 1:
        raise DemoInputError("固定兼容场景只支持模拟游戏 1，请为其他游戏使用 price-整数场景。")
    latest = db.execute("SELECT MAX(observed_at) FROM price_observations WHERE game_id=?", (game["id"],)).fetchone()[0]
    timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
    if latest:
        timestamp = max(timestamp, datetime.fromisoformat(latest) + timedelta(microseconds=1))
    return DemoPriceSource(event_id, timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"), game_id=game["id"])
