from __future__ import annotations

import asyncio
import math
import re
import random
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Page, async_playwright

from .db import connect, upsert_game
from .parser import parse_history_api_row

HISTORY_URL = "https://boardgamearena.com/gamestats?player={player_id}&game_id=1741"
ProgressCallback = Callable[[str, int, int, str], None]


async def _collect_history_rows(
    page: Page,
    player_id: int,
    workers: int = 8,
    progress_callback: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    await page.wait_for_selector("#gamelist_inner", timeout=90_000)
    body_text = await page.locator("body").inner_text()
    total_match = re.search(r"(?:已找到|found)\s*([\d,]+)\s*(?:场游戏|game)", body_text, re.I)
    if not total_match:
        raise RuntimeError("无法从 BGA 页面读取对局总数")
    expected = int(total_match.group(1).replace(",", ""))
    token = await page.evaluate("() => bgaConfig.requestToken")
    headers = {
        "x-request-token": token,
        "x-requested-with": "XMLHttpRequest",
        "referer": page.url,
    }
    semaphore = asyncio.Semaphore(max(1, min(workers, 12)))
    page_total = math.ceil(expected / 10)
    fetched_pages = 0
    if progress_callback:
        progress_callback("history", 0, page_total, f"准备读取 {expected} 条历史记录")

    async def fetch_page(page_no: int) -> list[dict[str, Any]]:
        nonlocal fetched_pages
        url = (
            "https://boardgamearena.com/gamestats/gamestats/getGames.html"
            f"?player={player_id}&opponent_id=0&game_id=1741&finished=0&page={page_no}&updateStats=0"
        )
        async with semaphore:
            response = await page.context.request.get(url, headers=headers, timeout=60_000)
        payload = await response.json()
        if response.status != 200 or payload.get("status") != 1:
            raise RuntimeError(f"历史分页 {page_no} 获取失败: HTTP {response.status} / {payload.get('error')}")
        fetched_pages += 1
        if progress_callback:
            progress_callback("history", fetched_pages, page_total, f"历史分页 {fetched_pages}/{page_total}")
        return payload.get("data", {}).get("tables", [])

    pages = await asyncio.gather(*(fetch_page(n) for n in range(1, page_total + 1)))
    rows = [row for batch in pages for row in batch]
    unique = {int(row["table_id"]): row for row in rows}
    if len(unique) != expected:
        difference = expected - len(unique)
        detail = f"缺少 {difference} 局" if difference > 0 else f"多出 {-difference} 局"
        message = f"BGA 报告 {expected} 局，本次取得 {len(unique)} 个唯一对局；{detail}，已忽略差异并继续"
        print(f"警告：{message}")
        if progress_callback:
            progress_callback("warning", len(unique), expected, message)
    return list(unique.values())


async def _collect_table_stats(context, table_id: int, player_id: int, token: str, referer: str) -> dict[str, Any]:
    url = f"https://boardgamearena.com/table/table/tableinfos.html?id={table_id}"
    response = await context.request.get(
        url,
        headers={"x-request-token": token, "x-requested-with": "XMLHttpRequest", "referer": referer},
        timeout=60_000,
    )
    payload = await response.json()
    if response.status != 200 or payload.get("status") != 1:
        raise RuntimeError(f"HTTP {response.status} / {payload.get('error')}")
    data = payload.get("data") or {}
    result = data.get("result") or {}
    stats = ((result.get("stats") or {}).get("player") or {})
    pid = str(player_id)

    def value(stat_name: str, *, label: bool = False):
        stat = stats.get(stat_name) or {}
        source = stat.get("valuelabels") if label else stat.get("values")
        return source.get(pid) if isinstance(source, dict) else None

    def integer(stat_name: str) -> int | None:
        raw = value(stat_name)
        try:
            return int(float(raw)) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None

    players = result.get("player") or []
    opponent = next((p for p in players if str(p.get("player_id")) != pid), None)
    opponent_elo = None
    if opponent:
        try:
            opponent_elo = float(opponent["rank_after_game"]) - float(opponent.get("point_win") or 0)
        except (KeyError, TypeError, ValueError):
            pass
    return {
        "table_id": table_id,
        "opponent_elo": round(opponent_elo, 2) if opponent_elo is not None else None,
        "map_name": value("map", label=True),
        "moves": integer("turns"),
        "action_animals": integer("upgradedActionAnimals"),
        "action_build": integer("upgradedActionBuild"),
        "action_cards": integer("upgradedActionCards"),
        "action_association": integer("upgradedActionAssociation"),
        "action_sponsors": integer("upgradedActionSponsors"),
        "replay_url": url,
        "replay_parsed": 1,
        "diagnostics": {
            "source": "tableinfos",
            "has_stats": bool(stats),
            "game_mode": ((data.get("options") or {}).get("201") or {}).get("value"),
        },
    }


async def scrape(
    root: Path,
    player_id: int,
    player_name: str = "",
    history_only: bool = False,
    limit: int | None = None,
    delay: float = 0.2,
    workers: int = 5,
    refresh: bool = False,
    db_path_override: Path | None = None,
    headless: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    data_dir = root / "data"
    db_path = db_path_override or (data_dir / "arknova.sqlite3")
    profile = data_dir / "browser-profile"
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(profile, headless=headless, viewport={"width": 1440, "height": 1000})
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(HISTORY_URL.format(player_id=player_id), wait_until="domcontentloaded", timeout=90_000)
        if "/account" in page.url:
            if headless:
                await context.close()
                raise RuntimeError("BGA 登录会话已失效；请先运行 .\\scrape.ps1 并在浏览器中登录一次")
            print("请在打开的浏览器中登录 BGA；检测到战绩页后会自动继续。")
            await page.wait_for_url("**/gamestats**", timeout=300_000)
        rows = await _collect_history_rows(page, player_id, workers=max(workers, 8), progress_callback=progress_callback)
        if not player_name:
            for row in rows:
                ids = [int(x) for x in str(row.get("players") or "").split(",") if x]
                names = str(row.get("player_names") or "").split(",")
                if player_id in ids and ids.index(player_id) < len(names):
                    player_name = names[ids.index(player_id)]
                    break
        if not player_name:
            player_name = str(player_id)
        parsed = []
        for row in rows:
            try:
                parsed.append(parse_history_api_row(row, player_id, player_name))
            except ValueError as exc:
                print(f"跳过无法识别的行: {exc}")
        with connect(db_path) as con:
            for game in parsed:
                upsert_game(con, game)
        print(f"已保存 {len(parsed)} 条历史记录。")
        if history_only:
            await context.close()
            return db_path
        with connect(db_path) as con:
            sql = "SELECT table_id FROM games WHERE is_arena=1"
            if not refresh:
                sql += " AND replay_parsed=0"
            sql += " ORDER BY played_at DESC"
            ids = [r[0] for r in con.execute(sql).fetchall()]
        if limit:
            ids = ids[:limit]
        if progress_callback:
            progress_callback("stats", 0, len(ids), f"需要读取 {len(ids)} 桌统计资料")
        token = await page.evaluate("() => bgaConfig.requestToken")
        referer = page.url
        queue: asyncio.Queue[int] = asyncio.Queue()
        for table_id in ids:
            queue.put_nowait(table_id)
        write_lock = asyncio.Lock()
        progress_lock = asyncio.Lock()
        completed = 0

        async def worker(worker_no: int) -> None:
            nonlocal completed
            while True:
                try:
                    table_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                status = "完成"
                try:
                    features = await _collect_table_stats(context, table_id, player_id, token, referer)
                    async with write_lock:
                        with connect(db_path) as con:
                            upsert_game(con, features)
                except Exception as exc:
                    status = f"{type(exc).__name__}: {exc}"
                finally:
                    queue.task_done()
                async with progress_lock:
                    completed += 1
                    print(f"[{completed}/{len(ids)}] W{worker_no} #{table_id}: {status}")
                    if progress_callback:
                        progress_callback("stats", completed, len(ids), f"统计资料 {completed}/{len(ids)} · #{table_id}")
                if delay > 0:
                    await asyncio.sleep(delay + random.uniform(0, delay))

        await asyncio.gather(*(worker(i + 1) for i in range(max(1, min(workers, 10)))))
        if progress_callback and not ids:
            progress_callback("stats", 0, 0, "没有需要补抓的统计资料")
        await context.close()
    return db_path

