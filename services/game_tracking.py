"""Persist only server-verified game metadata; never accept form prices/titles."""


def track_verified_game(db, verified):
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
                "UPDATE games SET cheapshark_game_id=?, steam_app_id=?, is_tracked=1 WHERE id=?",
                (verified.cheapshark_game_id, verified.steam_app_id, row["id"]),
            )
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
        return cursor.lastrowid, True


def stop_tracking(db, steam_app_id):
    """Idempotently archive tracking, retaining history, targets and notifications."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id FROM games WHERE data_source='cheapshark' AND steam_app_id=?",
            (steam_app_id,),
        ).fetchone()
        if row is None:
            raise ValueError("该 Steam 游戏尚未加入本地追踪。")
        db.execute("UPDATE games SET is_tracked=0 WHERE id=?", (row["id"],))
        return row["id"]
