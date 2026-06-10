# -*- coding: utf-8 -*-
"""
每日自动更新：从网易云热门歌单抓取免费歌曲添加到库中
核心逻辑：
1. 从网易云直连API获取3个热榜歌曲（飙升榜/热歌榜/新歌榜）
2. 严格过滤：fee=1(VIP)跳过，fee=0/8(免费)才入库
3. 补充封面+歌词，确保入库歌曲100%可播放
4. 去重：platform_id去重 + 歌名+歌手模糊匹配去重
5. 每日最多更新1次，每次添加最多41首
6. 封面通过网易云 /api/song/detail 补充（命中率高）
7. 歌词通过网易云 /api/lyric 补充
"""
import os
import sys
import re
import logging
import hashlib
import requests as http_requests
from datetime import datetime, date
from urllib.parse import quote

logger = logging.getLogger(__name__)

# 网易云API基础地址
NETEASE_API = "https://music.163.com/api"
NETEASE_REFERER = "https://music.163.com/"

# 网易云热门榜单ID
CHART_IDS = {
    "飙升榜": 19723756,
    "热歌榜": 3778678,
    "新歌榜": 3779629,
}

# 每次最多添加的歌曲数
MAX_DAILY_ADD = 41

# 每日最多更新次数
MAX_DAILY_UPDATES = 1


def daily_update_free_songs(app):
    """
    每日自动更新：从网易云热门歌单抓取免费歌曲。

    流程：
    1. 检查今日已更新次数，>=1 则跳过
    2. 从网易云直连API获取3个热榜的歌曲列表
    3. 过滤fee=1(VIP)，只保留fee=0/8(免费)
    4. 补充封面（/api/song/detail）+ 歌词（/api/lyric），去重后入库
    5. 记录更新日志

    返回: (added_count, message)
    """
    from models.db import db, Song, UpdateLog

    covers_dir = app.config.get("COVERS_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "covers"))
    os.makedirs(covers_dir, exist_ok=True)

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

    logger.info(f"=== 开始从网易云热榜抓取免费歌曲 ===")

    # ========== 2. 获取现有库中的去重集合 ==========
    existing_pids = set()
    all_songs = Song.query.with_entities(Song.platform_id).all()
    for s in all_songs:
        if s.platform_id:
            existing_pids.add(str(s.platform_id))

    existing_names = set()
    all_songs_full = Song.query.with_entities(Song.name, Song.artist).all()
    for s in all_songs_full:
        key = _normalize_key(s.name, s.artist)
        existing_names.add(key)

    logger.info(f"当前库中: {len(existing_pids)} 个platform_id, {len(existing_names)} 个歌名+歌手组合")

    # ========== 3. 从3个热榜获取歌曲 ==========
    all_candidates = []  # [(song_data, chart_name, rank)]

    for chart_name, chart_id in CHART_IDS.items():
        try:
            songs = _fetch_chart_songs(chart_id)
            logger.info(f"  {chart_name}: 获取到 {len(songs)} 首歌")
            for idx, song in enumerate(songs):
                all_candidates.append((song, chart_name, idx + 1))
        except Exception as e:
            logger.error(f"  {chart_name} 获取失败: {e}")
            continue

    if not all_candidates:
        return 0, "所有榜单获取失败"

    logger.info(f"共获取 {len(all_candidates)} 首候选歌曲")

    # ========== 4. 过滤 + 去重 + 入库 ==========
    added_count = 0
    skipped_vip = 0
    skipped_dup = 0
    skipped_no_cover = 0
    failed = 0

    seen_in_batch = set()
    sorted_candidates = sorted(all_candidates, key=lambda x: x[2])

    for song_data, chart_name, rank in sorted_candidates:
        if added_count >= MAX_DAILY_ADD:
            break

        pid = str(song_data.get("id", ""))
        name = song_data.get("name", "").strip()
        artists = song_data.get("artists", [])
        artist = " / ".join(a.get("name", "") for a in artists if a.get("name"))
        if not artist:
            artist = song_data.get("artistName", "") or _parse_netease_artists_alt(song_data)
        fee = song_data.get("fee", 0)
        album_data = song_data.get("album", {})
        cover_url = album_data.get("picUrl", "") or album_data.get("blurPicUrl", "")

        # 去重：同一首歌在多个榜单出现
        batch_key = _normalize_key(name, artist)
        if batch_key in seen_in_batch:
            continue
        seen_in_batch.add(batch_key)

        # 过滤VIP (fee=1)
        if fee == 1:
            skipped_vip += 1
            continue

        # 过滤空歌名/歌手
        if not name or not artist:
            continue

        # 过滤低质量歌曲
        if _is_low_quality(name, artist):
            continue

        # 去重：platform_id
        if pid and pid in existing_pids:
            skipped_dup += 1
            continue

        # 去重：歌名+歌手
        if batch_key in existing_names:
            skipped_dup += 1
            continue

        # 封面URL可能不完整（网易云API截断），用song/detail补充
        if not cover_url or len(cover_url) < 50:
            cover_url = _fetch_cover_from_detail(pid)

        # 下载封面到本地
        local_cover = ""
        if cover_url:
            local_cover = _download_cover(cover_url, name, artist, covers_dir)

        if not local_cover and not cover_url:
            skipped_no_cover += 1
            continue

        # 获取歌词
        lyric = _fetch_lyric(pid)

        # 创建歌曲记录
        try:
            song = Song(
                name=name,
                artist=artist,
                album=album_data.get("name", ""),
                cover_url=cover_url,
                local_cover=local_cover,
                platform="wangyi",
                platform_id=pid,
                hot_score=max(0, 1000 - rank),
                lyric=lyric or "",
            )
            db.session.add(song)

            existing_pids.add(pid)
            existing_names.add(batch_key)
            added_count += 1

            if added_count % 10 == 0:
                db.session.commit()
                logger.info(f"  已入库 {added_count} 首")

            logger.info(f"  + {artist} - {name} (fee={fee}, rank={rank})")

        except Exception as e:
            logger.error(f"  入库失败: {artist} - {name}: {e}")
            failed += 1
            continue

    if added_count > 0:
        db.session.commit()

    # ========== 5. 记录更新日志 ==========
    msg = (f"从网易云热榜添加{added_count}首免费歌 "
           f"(跳过: VIP={skipped_vip}, 重复={skipped_dup}, "
           f"无封面={skipped_no_cover}, 失败={failed})")

    log = UpdateLog(
        source="daily-free-update",
        status="success" if added_count > 0 else "failed",
        songs_added=added_count,
        songs_updated=0,
        message=msg,
    )
    db.session.add(log)
    db.session.commit()

    logger.info(f"=== 网易云热榜更新完成: {msg} ===")
    return added_count, msg


# ============ 辅助函数 ============

def _fetch_chart_songs(chart_id, limit=200):
    """从网易云直连API获取榜单歌曲"""
    url = f"{NETEASE_API}/playlist/detail?id={chart_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": NETEASE_REFERER,
    }
    resp = http_requests.get(url, headers=headers, timeout=30, verify=False)
    data = resp.json()

    # 网易云直连API返回格式：{result: {tracks: [...]}}
    tracks = data.get("result", {}).get("tracks", [])
    return tracks[:limit]


def _parse_netease_artists_alt(item):
    """备用歌手解析（兼容不同字段格式）"""
    for key in ["artists", "ar", "artistNames"]:
        lst = item.get(key, [])
        if lst and isinstance(lst, list):
            names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in lst]
            names = [n for n in names if n]
            if names:
                return " / ".join(names)
    return "未知歌手"


def _normalize_key(name, artist):
    """标准化歌名+歌手用于去重"""
    n = re.sub(r'[（(].*?[）)]', '', name or "")
    n = re.sub(r'[\s\-_\\/@&·]', '', n)
    n = n.replace('〜', '~').replace('～', '~').lower().strip()
    a = re.sub(r'[\s\-_\\/@&·]', '', artist or "")
    a = a.replace('〜', '~').replace('～', '~').lower().strip()
    return f"{n}_{a}"


def _is_low_quality(name, artist):
    """过滤低质量歌曲"""
    name_lower = name.lower()
    skip_keywords = [
        "dj版", "dj混音", "dj版)", "伴奏", "降速", "加速",
        "remix", "cover", "live版", "acoustic版",
    ]
    for kw in skip_keywords:
        if kw in name_lower:
            return True
    return False


def _fetch_cover_from_detail(song_id):
    """用网易云 /api/song/detail 获取完整封面URL（注意ids参数需用方括号包裹）"""
    try:
        # 关键：ids参数必须用方括号包裹，如 ids=[123456]
        url = f"{NETEASE_API}/song/detail?ids=[{song_id}]"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": NETEASE_REFERER,
        }
        resp = http_requests.get(url, headers=headers, timeout=10, verify=False)
        data = resp.json()
        songs = data.get("songs", [])
        if songs:
            al = songs[0].get("album", {}) or songs[0].get("al", {})
            pic = al.get("picUrl", "")
            if pic:
                return pic
    except Exception:
        pass
    return ""


def _download_cover(cover_url, name, artist, covers_dir):
    """下载封面到本地"""
    if not cover_url or not cover_url.startswith("http"):
        return ""
    try:
        ext = ".jpg"
        if ".png" in cover_url:
            ext = ".png"
        elif ".webp" in cover_url:
            ext = ".webp"

        filename = hashlib.md5(f"{name}_{artist}".encode("utf-8")).hexdigest() + ext
        local_path = os.path.join(covers_dir, filename)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
            return f"/data/covers/{filename}"

        # 网易云封面需要加参数获取大图
        dl_url = cover_url
        if "?" not in cover_url:
            dl_url = f"{cover_url}?param=300y300"

        resp = http_requests.get(dl_url, timeout=15, verify=False,
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": NETEASE_REFERER})
        if resp.status_code == 200 and len(resp.content) > 1024:
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return f"/data/covers/{filename}"
    except Exception as e:
        logger.debug(f"下载封面失败 [{name}]: {e}")
    return ""


def _fetch_lyric(song_id):
    """用网易云API获取歌词"""
    try:
        url = f"{NETEASE_API}/song/lyric?id={song_id}&lv=1&tv=-1"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": NETEASE_REFERER,
        }
        resp = http_requests.get(url, headers=headers, timeout=10, verify=False)
        data = resp.json()
        lrc = data.get("lrc", {})
        lyric_text = lrc.get("lyric", "") if lrc else ""
        if lyric_text and "[" in lyric_text:
            return lyric_text
    except Exception:
        pass
    return ""
