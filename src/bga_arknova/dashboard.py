from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path
import math

import pandas as pd
import plotly.express as px
import streamlit as st

from .db import connect
from .parser import performance_rating
from .scraper import scrape

ACTIONS = {
    "action_animals": "动物",
    "action_build": "建造",
    "action_cards": "卡牌",
    "action_association": "协会",
    "action_sponsors": "赞助商",
}


def _rolling(df: pd.DataFrame, col: str, window: int) -> pd.Series:
    return df[col].rolling(window, min_periods=max(3, window // 4)).mean()


def _valid_map(value) -> str:
    if pd.isna(value):
        return "未识别"
    text = str(value).strip()
    if not text or text.lower() in {"bonus", "map", "none", "null"} or text.startswith(("{", "[")):
        return "未识别"
    return text


def _filter_matchups(
    games: pd.DataFrame,
    *,
    opponent_query: str = "",
    opponent_rating_col: str | None = None,
    opponent_rating_range: tuple[int, int] | None = None,
    moves_range: tuple[int, int] | None = None,
    results: set[int] | None = None,
    ending: str = "全部",
    action_filters: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Apply the advanced matchup-search filters used by the dashboard."""
    result = games.copy()
    query = opponent_query.strip()
    if query:
        result = result[result["opponent_name"].fillna("").str.contains(query, case=False, regex=False)]
    if opponent_rating_col and opponent_rating_range:
        rating = pd.to_numeric(result[opponent_rating_col], errors="coerce")
        result = result[rating.between(*opponent_rating_range)]
    if moves_range:
        moves = pd.to_numeric(result["moves"], errors="coerce")
        result = result[moves.between(*moves_range)]
    if results is not None:
        result = result[pd.to_numeric(result["won"], errors="coerce").isin(results)]
    if ending == "正常完成":
        result = result[(result["surrendered"] == 0) & (result["unfinished"] == 0)]
    elif ending == "有认输":
        result = result[result["surrendered"] == 1]
    elif ending == "未完成":
        result = result[result["unfinished"] == 1]
    for column, state in (action_filters or {}).items():
        values = pd.to_numeric(result[column], errors="coerce")
        if state == "已升级":
            result = result[values == 1]
        elif state == "未升级":
            result = result[values == 0]
        elif state == "数据缺失":
            result = result[values.isna()]
    return result


def _database_identity(path: Path) -> tuple[int, str] | None:
    try:
        with sqlite3.connect(path) as con:
            row = con.execute(
                "SELECT player_id, player_name FROM games WHERE player_id IS NOT NULL "
                "ORDER BY played_at DESC LIMIT 1"
            ).fetchone()
        if row and row[0] is not None:
            return int(row[0]), str(row[1] or row[0])
    except (sqlite3.Error, TypeError, ValueError):
        pass
    return None


def _db_for_player(root: Path, player_id: int) -> Path:
    legacy = root / "data" / "arknova.sqlite3"
    identity = _database_identity(legacy) if legacy.exists() else None
    if identity and identity[0] == player_id:
        return legacy
    return root / "data" / "players" / f"player_{player_id}.sqlite3"


def _stored_players(root: Path) -> list[dict]:
    candidates: list[tuple[int, Path]] = []
    legacy = root / "data" / "arknova.sqlite3"
    if legacy.exists():
        identity = _database_identity(legacy)
        if identity:
            candidates.append((identity[0], legacy))
    players_dir = root / "data" / "players"
    if players_dir.exists():
        for path in players_dir.glob("player_*.sqlite3"):
            try:
                candidates.append((int(path.stem.removeprefix("player_")), path))
            except ValueError:
                pass
    players = []
    for player_id, path in sorted(dict(candidates).items()):
        name = str(player_id)
        try:
            with sqlite3.connect(path) as con:
                row = con.execute(
                    "SELECT player_name FROM games WHERE player_name IS NOT NULL AND player_name <> '' "
                    "ORDER BY played_at DESC LIMIT 1"
                ).fetchone()
                if row and row[0]:
                    name = str(row[0])
        except sqlite3.Error:
            pass
        players.append({"id": player_id, "name": name, "label": f"{name}（{player_id}）", "path": path})
    return players


def _read_games(db: Path) -> pd.DataFrame:
    with connect(db):
        pass
    with sqlite3.connect(db) as con:
        return pd.read_sql_query("SELECT * FROM games WHERE is_arena=1", con)


def run(root: Path) -> None:
    st.set_page_config(page_title="BGA 方舟动物园 JJC", page_icon="🦁", layout="wide")
    st.title("方舟动物园 · JJC 数据面板")
    stored_players = _stored_players(root)
    stored_ids = [p["id"] for p in stored_players]
    stored_by_id = {p["id"]: p for p in stored_players}
    with st.sidebar:
        st.header("数据源")
        if stored_players:
            entry_mode = st.radio("读取方式", ["选择已保存玩家", "输入新的玩家 ID"], horizontal=True)
        else:
            entry_mode = "输入新的玩家 ID"
        if entry_mode == "选择已保存玩家":
            active_id = st.session_state.get("active_player_id")
            default_index = stored_ids.index(active_id) if active_id in stored_ids else 0
            player_id = st.selectbox(
                "已保存玩家",
                stored_ids,
                index=default_index,
                format_func=lambda pid: stored_by_id[pid]["label"],
            )
        else:
            default_id = str(st.session_state.get("active_player_id", stored_ids[0] if stored_ids else ""))
            player_text = st.text_input("BGA 玩家 ID", value=default_id, help="输入个人资料 URL 中的数字 ID")
            try:
                player_id = int(player_text.strip())
                if player_id <= 0:
                    raise ValueError
            except ValueError:
                st.error("玩家 ID 必须是正整数。")
                return
        persist_data = st.toggle("将读取的数据保存到本机", value=True)
        refresh_clicked = st.button("刷新并读取最新数据", type="primary", use_container_width=True)
        if stored_players:
            st.caption(f"已保存 {len(stored_players)} 位玩家")

    volatile_key = f"volatile_player_{player_id}"
    if refresh_clicked:
        try:
            with st.spinner("正在读取 BGA 最新历史与每桌统计资料…"):
                progress = st.progress(0.0, text="正在连接 BGA…")
                refresh_warnings: list[str] = []

                def update_progress(phase: str, current: int, total: int, message: str) -> None:
                    if phase == "warning":
                        refresh_warnings.append(message)
                        progress.progress(0.2, text=message)
                        return
                    ratio = (current / total) if total else 1.0
                    overall = ratio * 0.2 if phase == "history" else 0.2 + ratio * 0.8
                    progress.progress(min(max(overall, 0.0), 1.0), text=message)

                if persist_data:
                    db = _db_for_player(root, player_id)
                    db.parent.mkdir(parents=True, exist_ok=True)
                    asyncio.run(scrape(root, player_id, workers=10, delay=0.05, db_path_override=db, headless=True,
                                       progress_callback=update_progress))
                    st.session_state.pop(volatile_key, None)
                else:
                    temp_parent = root / "data" / "session-temp"
                    temp_parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(dir=temp_parent) as temp_dir:
                        temp_db = Path(temp_dir) / "session.sqlite3"
                        asyncio.run(scrape(root, player_id, workers=10, delay=0.05, db_path_override=temp_db, headless=True,
                                           progress_callback=update_progress))
                        st.session_state[volatile_key] = _read_games(temp_db)
                st.session_state["active_player_id"] = player_id
                progress.progress(1.0, text="读取完成")
            st.success("最新数据读取完成。")
            for warning in refresh_warnings:
                st.warning(warning + "。后续刷新仍会尝试补齐。")
        except Exception as exc:
            st.error(f"刷新失败：{exc}")
            return

    if volatile_key in st.session_state:
        df = st.session_state[volatile_key].copy()
        source_label = "当前会话（未保存）"
    else:
        db = _db_for_player(root, player_id)
        if not db.exists():
            st.info("该玩家尚无数据。点击左侧“刷新并读取最新数据”。")
            return
        df = _read_games(db)
        source_label = str(db)
    if df.empty:
        st.warning("数据库中尚无竞技场对局。")
        return
    player_name = next((str(v) for v in df.player_name.dropna() if str(v)), str(player_id))
    st.caption(f"玩家：{player_name}（{player_id}） · 数据源：{source_label}")
    df["played_at"] = pd.to_datetime(df["played_at"], errors="coerce")
    for col in ("won", "player_score", "opponent_score", "player_elo_before", "player_elo_after", "opponent_elo",
                "arena_before", "arena_after", "arena_rating_before", "arena_rating_after", "moves"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["map_label"] = df["map_name"].map(_valid_map)
    df["bga_rating_before"] = df["player_elo_before"] - 1300
    df["bga_rating_after"] = df["player_elo_after"] - 1300
    df["opponent_bga_rating"] = df["opponent_elo"] - 1300
    df["arena_points_before"] = df["arena_rating_before"].map(
        lambda v: round((v - math.floor(v)) * 10000) if pd.notna(v) else pd.NA
    )
    df["arena_points_after"] = df["arena_rating_after"].map(
        lambda v: round((v - math.floor(v)) * 10000) if pd.notna(v) else pd.NA
    )
    df = df.sort_values("played_at").reset_index(drop=True)
    min_date, max_date = df.played_at.min().date(), df.played_at.max().date()
    with st.sidebar:
        st.header("筛选")
        dates = st.date_input("日期范围", (min_date, max_date), min_value=min_date, max_value=max_date)
        rating_mode = st.radio("本人等级筛选口径", ["BGA 显示分", "原始 Elo"], horizontal=True,
                               help="BGA 显示分 = 原始 Elo − 1300；竞技场分是另一套独立数值")
        rating_col = "bga_rating_before" if rating_mode == "BGA 显示分" else "player_elo_before"
        elo_valid = df[rating_col].dropna()
        if elo_valid.empty:
            elo_range = None
            st.caption("当前数据没有本人赛前 ELO")
        else:
            lo, hi = int(elo_valid.min()), int(elo_valid.max())
            elo_range = st.slider(f"本人赛前{rating_mode}", lo, hi, (lo, hi))
        maps = sorted(df.map_label.unique(), key=lambda x: (x == "未识别", x))
        selected_maps = st.multiselect("地图", maps, default=maps)
        clean_only = st.toggle("动数/效率图排除认输和未完成局", value=True)
        axis_mode = st.radio("趋势横轴", ["对局数量", "时间"], horizontal=True)
        window = st.slider("滚动窗口（局）", 5, 100, 30, 5)
    start, end = (dates if isinstance(dates, (tuple, list)) and len(dates) == 2 else (min_date, max_date))
    f = df[(df.played_at.dt.date >= start) & (df.played_at.dt.date <= end)].copy()
    if elo_range:
        f = f[f[rating_col].between(*elo_range) | f[rating_col].isna()]
    if selected_maps:
        f = f[f.map_label.isin(selected_maps)]
    if f.empty:
        st.warning("当前筛选条件下没有对局。")
        return
    f = f.sort_values("played_at").reset_index(drop=True)
    f["game_no"] = range(1, len(f) + 1)
    x = "game_no" if axis_mode == "对局数量" else "played_at"
    x_label = "对局序号" if x == "game_no" else "时间"
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("对局", f"{len(f):,}")
    c2.metric("胜率", f"{f.won.mean():.1%}")
    c3.metric("BGA 显示分", f"{f.bga_rating_after.dropna().iloc[-1]:.0f}" if f.bga_rating_after.notna().any() else "—",
              help="BGA 页面显示的等级分，等于原始 Elo − 1300")
    c4.metric("原始 Elo", f"{f.player_elo_after.dropna().iloc[-1]:.0f}" if f.player_elo_after.notna().any() else "—")
    c5.metric("竞技场分", f"{float(f.arena_points_after.dropna().iloc[-1]):.0f}" if f.arena_points_after.notna().any() else "—",
              help="独立于 Elo 的竞技场积分")
    c6.metric("平均对手 BGA分", f"{f.opponent_bga_rating.mean():.0f}" if f.opponent_bga_rating.notna().any() else "—",
              help="对手原始 Elo − 1300")
    f["rolling_win_rate"] = _rolling(f, "won", window)
    fig = px.line(f, x=x, y="rolling_win_rate", labels={x: x_label, "rolling_win_rate": "滚动胜率"}, title=f"胜率趋势（最近 {window} 局）")
    fig.update_yaxes(tickformat=".0%", range=[0, 1])
    st.plotly_chart(fig, use_container_width=True)
    st.subheader("地图胜率与表现分")
    map_rows = []
    for name, g in f[f.map_label != "未识别"].groupby("map_label"):
        opp = g.opponent_bga_rating.mean()
        wins, losses = int(g.won.sum()), int((1 - g.won).sum())
        map_rows.append({"地图": name, "对局": len(g), "胜率": wins / len(g), "平均对手BGA分": opp,
                         "表现分": performance_rating(wins, losses, opp) if pd.notna(opp) else None})
    map_df = pd.DataFrame(map_rows).sort_values("表现分", ascending=False) if map_rows else pd.DataFrame()
    if not map_df.empty:
        a, b = st.columns([1, 1.4])
        shown = map_df.copy()
        shown["胜率"] = shown["胜率"].map(lambda v: f"{v:.1%}")
        shown["平均对手BGA分"] = shown["平均对手BGA分"].map(lambda v: f"{v:.0f}" if pd.notna(v) else "—")
        shown["表现分"] = shown["表现分"].map(lambda v: f"{v:.0f}" if pd.notna(v) else "—")
        a.dataframe(shown, hide_index=True, use_container_width=True)
        if map_df["表现分"].notna().any():
            b.plotly_chart(px.bar(map_df, x="地图", y="表现分", color="胜率", color_continuous_scale="Viridis", hover_data=["对局", "平均对手BGA分"]), use_container_width=True)
        else:
            b.info("尚未取得对手历史 Elo；地图表现分会在回放元数据补齐后显示。")
        trend = f[(f.map_label != "未识别") & f.opponent_bga_rating.notna()].copy()
        trend["perf_proxy"] = trend.opponent_bga_rating + trend.won.map({1: 200, 0: -200})
        trend["地图滚动表现"] = trend.groupby("map_label")["perf_proxy"].transform(lambda s: s.rolling(window, min_periods=3).mean())
        st.plotly_chart(px.line(trend, x=x, y="地图滚动表现", color="map_label", labels={x: x_label, "map_label": "地图"}, title="各地图表现随时间变化（滚动近似）"), use_container_width=True)
    valid = f.copy()
    if clean_only:
        valid = valid[(valid.surrendered == 0) & (valid.unfinished == 0)]
    valid = valid[valid.moves.notna() & (valid.moves > 0)].copy()
    if not valid.empty:
        valid["平均动数"] = _rolling(valid, "moves", window)
        valid["score_per_move"] = valid.player_score / valid.moves
        valid["平均每动得分"] = _rolling(valid, "score_per_move", window)
        left, right = st.columns(2)
        left.plotly_chart(px.line(valid, x=x, y="平均动数", labels={x: x_label}, title="平均动数趋势"), use_container_width=True)
        right.plotly_chart(px.line(valid, x=x, y="平均每动得分", labels={x: x_label}, title="平均每动得分趋势"), use_container_width=True)
    st.subheader("行动卡升级率")
    action_long = []
    for col, label in ACTIONS.items():
        series = pd.to_numeric(valid[col], errors="coerce")
        rolling = series.rolling(window, min_periods=max(3, window // 4)).mean()
        for idx, value in rolling.items():
            action_long.append({x: valid.loc[idx, x], "行动卡": label, "升级率": value})
    action_df = pd.DataFrame(action_long).dropna()
    if not action_df.empty:
        af = px.line(action_df, x=x, y="升级率", color="行动卡", labels={x: x_label}, title=f"行动卡升级率（最近 {window} 局）")
        af.update_yaxes(tickformat=".0%", range=[0, 1])
        st.plotly_chart(af, use_container_width=True)
    st.subheader("对局检索与直达")
    st.caption("以下条件会叠加在左侧的日期、本人等级分和地图筛选之上。")
    search_left, search_mid, search_right = st.columns(3)
    with search_left:
        opponent_query = st.text_input("对手名称包含", placeholder="留空表示不限")
        result_labels = st.multiselect("对局结果", ["胜", "负"], default=["胜", "负"])
        ending = st.selectbox("结束方式", ["全部", "正常完成", "有认输", "未完成"])
    with search_mid:
        opponent_rating_mode = st.radio("对手等级分口径", ["BGA 显示分", "原始 Elo"], horizontal=True)
        opponent_rating_col = "opponent_bga_rating" if opponent_rating_mode == "BGA 显示分" else "opponent_elo"
        use_opponent_rating = st.toggle("启用对手等级分筛选")
        opponent_values = f[opponent_rating_col].dropna()
        opponent_range = None
        if use_opponent_rating and not opponent_values.empty:
            opponent_lo, opponent_hi = int(opponent_values.min()), int(opponent_values.max())
            opponent_range = st.slider(f"对手{opponent_rating_mode}", opponent_lo, opponent_hi,
                                       (opponent_lo, opponent_hi), key="matchup_opponent_rating")
        elif use_opponent_rating:
            st.caption("当前数据没有对手等级分。")
    with search_right:
        use_moves = st.toggle("启用动数筛选")
        move_values = f.moves.dropna()
        moves_range = None
        if use_moves and not move_values.empty:
            moves_lo, moves_hi = int(move_values.min()), int(move_values.max())
            moves_range = st.slider("动数", moves_lo, moves_hi, (moves_lo, moves_hi), key="matchup_moves")
        elif use_moves:
            st.caption("当前数据没有动数。")

    st.markdown("**行动卡终局状态**")
    action_filters: dict[str, str] = {}
    for container, (column, label) in zip(st.columns(len(ACTIONS)), ACTIONS.items()):
        with container:
            action_filters[column] = st.selectbox(label, ["不限", "已升级", "未升级", "数据缺失"],
                                                   key=f"matchup_{column}")
    result_values = {"胜": 1, "负": 0}
    games = _filter_matchups(
        f,
        opponent_query=opponent_query,
        opponent_rating_col=opponent_rating_col if use_opponent_rating and opponent_range else None,
        opponent_rating_range=opponent_range,
        moves_range=moves_range,
        results={result_values[label] for label in result_labels},
        ending=ending,
        action_filters=action_filters,
    ).sort_values("played_at", ascending=False).copy()
    st.caption(f"找到 {len(games):,} 局")
    if games.empty:
        st.info("没有符合这些检索条件的对局。")
    games["直达"] = games.table_id.map(lambda value: f"https://boardgamearena.com/table?table={int(value)}")
    games["日期"] = games.played_at.dt.strftime("%Y-%m-%d %H:%M")
    games["结果"] = games.won.map({1: "胜", 0: "负"})
    games["比分"] = games.apply(
        lambda r: f"{r.player_score:g}–{r.opponent_score:g}" if pd.notna(r.player_score) and pd.notna(r.opponent_score) else "—", axis=1
    )
    games["BGA显示分"] = games.apply(
        lambda r: f"{r.bga_rating_before:.0f} → {r.bga_rating_after:.0f}" if pd.notna(r.bga_rating_before) and pd.notna(r.bga_rating_after) else "—", axis=1
    )
    games["原始Elo"] = games.apply(
        lambda r: f"{r.player_elo_before:.0f} → {r.player_elo_after:.0f}" if pd.notna(r.player_elo_before) and pd.notna(r.player_elo_after) else "—", axis=1
    )
    games["竞技场分"] = games.apply(
        lambda r: f"{float(r.arena_points_before):.0f} → {float(r.arena_points_after):.0f}" if pd.notna(r.arena_points_before) and pd.notna(r.arena_points_after) else "—", axis=1
    )
    games["对手BGA分"] = games.opponent_bga_rating.map(lambda value: f"{value:.0f}" if pd.notna(value) else "—")
    games["动数"] = games.moves.map(lambda value: f"{value:.0f}" if pd.notna(value) else "—")
    for column, label in ACTIONS.items():
        games[f"{label}升级"] = pd.to_numeric(games[column], errors="coerce").map({1: "是", 0: "否"}).fillna("—")
    st.dataframe(
        games[["直达", "日期", "table_id", "opponent_name", "结果", "比分", "对手BGA分", "map_label", "动数",
               "动物升级", "建造升级", "卡牌升级", "协会升级", "赞助商升级", "BGA显示分", "竞技场分", "surrendered"]].rename(
            columns={"table_id": "桌号", "opponent_name": "对手", "map_label": "地图", "surrendered": "有认输"}
        ),
        column_config={"直达": st.column_config.LinkColumn("直达", display_text="打开对局")},
        hide_index=True,
        use_container_width=True,
        height=420,
    )
    coverage = {"地图": (f.map_label != "未识别").mean(), "动数": f.moves.notna().mean(), "对手ELO": f.opponent_elo.notna().mean(),
                "行动卡": f[list(ACTIONS)].notna().any(axis=1).mean()}
    st.caption("数据覆盖率：" + " · ".join(f"{k} {v:.0%}" for k, v in coverage.items()))

