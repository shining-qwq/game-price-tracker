"""Persist only server-verified game metadata; never accept form prices/titles."""
import re


def _link(db, user_id, game_id):
    return db.execute(
        "INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,?) ON CONFLICT(user_id,game_id) DO NOTHING",
        (user_id, game_id),
    ).rowcount == 1


def track_existing_game(db, user_id, *, cheapshark_game_id=None, steam_app_id=None):
    """Reuse a verified local archive; neither refetch nor copy global history."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT id,steam_app_id FROM games WHERE data_source='cheapshark'
               AND platform='Steam' AND currency='USD'
               AND ((? IS NOT NULL AND cheapshark_game_id=?)
                    OR (? IS NOT NULL AND steam_app_id=?))""",
            (cheapshark_game_id, cheapshark_game_id, steam_app_id, steam_app_id),
        ).fetchone()
        if row is None:
            return None
        if not isinstance(row["steam_app_id"], str) or not re.fullmatch(r"[1-9][0-9]{0,19}", row["steam_app_id"]):
            raise ValueError("该档案没有有效的 Steam AppID，暂不能加入追踪。")
        return row["id"], _link(db, user_id, row["id"])


def track_verified_game(db, verified, user_id):
    with db:
        db.execute("BEGIN IMMEDIATE")
        matches = db.execute(
            """
            SELECT id, cheapshark_game_id, steam_app_id FROM games
            WHERE data_source = 'cheapshark'
              AND (cheapshark_game_id = ? OR steam_app_id = ?)
            """,
            (verified.cheapshark_game_id, verified.steam_app_id),
        ).fetchall()
        if matches:
            if len(matches) != 1:
                raise ValueError("外部标识对应不同的已有档案，未修改记录，请检查数据。")
            row = matches[0]
            if (row["cheapshark_game_id"] not in (None, verified.cheapshark_game_id)
                    or row["steam_app_id"] not in (None, verified.steam_app_id)):
                raise ValueError("外部标识与已有档案不一致，未修改记录。")
            db.execute(
                "UPDATE games SET cheapshark_game_id=?, steam_app_id=? WHERE id=?",
                (verified.cheapshark_game_id, verified.steam_app_id, row["id"]),
            )
            _link(db, user_id, row["id"])
            return row["id"], False
        cursor = db.execute(
            """
            INSERT INTO games
                (title, subtitle, genre, platform, region, currency, data_source,
                 accent, cheapshark_game_id, steam_app_id)
            VALUES (?, '外部数据未提供', '外部数据未提供', 'Steam',
                    '地区未由源提供', 'USD', 'cheapshark', '#5066a6', ?, ?)
            """,
            (verified.title, verified.cheapshark_game_id, verified.steam_app_id),
        )
        game_id = cursor.lastrowid
        _link(db, user_id, game_id)
        return game_id, True


def stop_tracking(db, steam_app_id, user_id):
    """Delete only this owner's link; retain the archive and all business data."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id FROM games WHERE data_source='cheapshark' AND steam_app_id=?",
            (steam_app_id,),
        ).fetchone()
        if row is None:
            raise ValueError("该 Steam 游戏尚未加入本地追踪。")
        db.execute("DELETE FROM user_game_tracking WHERE user_id=? AND game_id=?", (user_id, row["id"]))
        return row["id"]
