# -*- coding: utf-8 -*-
"""
每日自动更新：将音乐库末尾歌曲提升到最新上架前面
核心逻辑：
1. 不再从网易云榜单抓取歌曲（大部分已在库中）
2. 按上架时间(created_at)降序排列，取排在最后面的歌曲（最早入库、曝光最少的）
3. 将它们的 created_at 更新为当前时间，让它们出现在"最新上架"最前面
4. 每次更新 10 首歌，每首歌必须是不同歌手
5. 不创建新记录，不改动 hot_score，绝对避免重复
6. 每日最多更新 3 次
7. 提升后歌排到前面去了，下次末尾自然就是下一批，不会重复
"""
import os
import sys
import logging
from datetime import datetime, timedelta, date

logger = logging.getLogger(__name__)

# 每次最多提升的歌曲数
MAX_DAILY_ADD = 10

# 每日最多更新次数
MAX_DAILY_UPDATES = 3


def daily_update_free_songs(app):
    """
    每日自动更新：将音乐库末尾歌曲提升到最新上架前面。

    逻辑：
    1. 查询今日已更新次数，>=3 则跳过
    2. 按上架时间(created_at)降序排列，从末尾取歌曲（每个歌手最多取一首）
    3. 将选中歌曲的 created_at 更新为当前时间，让它们出现在"最新上架"最前面
    4. 提升后的歌排到最前面，下次末尾自然就是下一批，不会重复

    返回: (updated_count, message)
    """
    from models.db import db, Song, UpdateLog

    # ========== 1. 检查今日更新次数 ==========
    today = date.today()
    today_updates = UpdateLog.query.filter_by(
        source="daily-free-update", status="success"
    ).all()
    today_count = sum(1 for u in today_updates if u.created_at.date() == today)

    logger.info(f"crawler.daily_update: 今日已更新过 {today_count} 次免费热榜歌曲")

    if today_count >= MAX_DAILY_UPDATES:
        logger.info(f"今日已更新 {today_count} 次，达到上限({MAX_DAILY_UPDATES})，跳过")
        return 0, f"今日已更新{today_count}次，达到上限"

    logger.info(f"=== 开始第 {today_count + 1} 次库内歌曲提升更新 ===")

    # ========== 2. 按上架时间降序排列，从末尾取歌 ==========
    # 按 created_at 降序（最新上架在前），我们取排在最后面的歌
    # 即最早入库、曝光最少的歌
    all_songs_by_new = Song.query.order_by(Song.created_at.desc()).all()

    if not all_songs_by_new:
        logger.info("音乐库为空，无需更新")
        return 0, "音乐库为空"

    # ========== 3. 从末尾开始，每个歌手只取一首，选出最多10首 ==========
    selected = []
    seen_artists = set()

    # 反向遍历：从列表末尾（最早入库的）开始取
    for song in reversed(all_songs_by_new):
        if len(selected) >= MAX_DAILY_ADD:
            break

        # 提取主歌手名（处理 "歌手1 / 歌手2" 合唱情况）
        artist_parts = song.artist.replace("、", "/").split("/")
        primary_artist = artist_parts[0].strip()

        # 跳过已选过的歌手（避免同一歌手多首歌）
        if primary_artist in seen_artists:
            continue

        seen_artists.add(primary_artist)
        selected.append(song)

    if not selected:
        logger.info("没有可提升的歌曲")
        return 0, "没有可提升的歌曲"

    # 按提升后的排名排序（最后遍历到的排在最前面 = 最新时间）
    selected.reverse()

    logger.info(f"选中 {len(selected)} 首末尾歌曲待提升:")
    for s in selected:
        logger.info(f"  - {s.artist} - {s.name} (created_at={s.created_at})")

    # ========== 4. 将选中歌曲的 created_at 更新为当前时间 ==========
    now = datetime.utcnow()

    # 从最晚到最早赋值，保持10首歌之间的相对排名
    updated_count = 0
    for i, song in enumerate(selected):
        # 每首歌间隔1秒，保持相对顺序
        new_created_at = now + timedelta(seconds=len(selected) - i)
        old_created_at = song.created_at
        song.created_at = new_created_at
        updated_count += 1
        logger.info(f"  ↑ {song.artist} - {song.name}: {old_created_at} → {new_created_at}")

    db.session.commit()

    # ========== 5. 记录更新日志 ==========
    song_names = ", ".join(f"{s.artist}-{s.name}" for s in selected)
    msg = f"第{today_count + 1}次库内提升: 提升{updated_count}首 ({song_names})"
    log = UpdateLog(
        source="daily-free-update",
        status="success",
        songs_added=0,
        songs_updated=updated_count,
        message=msg,
    )
    db.session.add(log)
    db.session.commit()

    logger.info(f"=== 库内提升完成: {msg} ===")
    return updated_count, msg
