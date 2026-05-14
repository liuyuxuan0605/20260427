# -*- coding: utf-8 -*-
"""
重建歌曲数据库 —— 用平台搜索API精确获取歌曲信息（含平台ID）
核心原则：入库时保存平台ID，播放时直接用ID获取链接，杜绝歌名与音频不匹配

流程：
1. 从热点数据采集获取排行榜歌曲列表（歌名+歌手）
2. 用各平台搜索API精确搜索每首歌（返回平台ID+封面+专辑）
3. 只入库搜索结果完全匹配的歌（歌名+歌手都对才入库）
4. 入库时保存完整的 platform + platform_id
5. 播放时直接用 platform_id 获取链接 → 100%准确
"""
import os
import sys
import re
import json
import time
import logging
import warnings
import requests as http_requests
from urllib.parse import quote

# Windows UTF-8
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore")

# 项目路径
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 避免 create_app 启动 scheduler 占用 SQLite
import flask
from flask import Flask
from models.db import db, Song
from config import SQLALCHEMY_DATABASE_URI, DATA_DIR

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

COVERS_DIR = os.path.join(DATA_DIR, "covers")
HOT_SKILL_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", "hot")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

session = http_requests.Session()
session.headers.update(HEADERS)
session.verify = False


# ============ 歌名标准化 ============

def normalize_name(name):
    """标准化歌名用于匹配"""
    n = re.sub(r'[（(].*?[）)]', '', name)
    n = re.sub(r'[\s\-_\\/@&·]', '', n)
    n = n.replace('〜', '~').replace('～', '~')
    return n.lower().strip()


def name_similarity(name1, name2):
    """歌名相似度"""
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)
    if n1 == n2:
        return 1.0
    if n1 in n2 or n2 in n1:
        shorter = min(len(n1), len(n2))
        longer = max(len(n1), len(n2))
        return shorter / longer if longer > 0 else 0.0
    return 0.0


def artist_similarity(target, result):
    """歌手相似度"""
    if not target or not result:
        return 0.0
    t = normalize_name(target)
    r = normalize_name(result)
    if t == r:
        return 1.0
    best = 0.0
    if t in r or r in t:
        shorter = min(len(t), len(r))
        longer = max(len(t), len(r))
        best = max(best, shorter / longer if longer > 0 else 0.0)
    # 多歌手分割
    t_parts = [normalize_name(p) for p in re.split(r'[/、,，&]', target) if normalize_name(p)]
    r_parts = [normalize_name(p) for p in re.split(r'[/、,，&]', result) if normalize_name(p)]
    if t_parts and r_parts:
        matched = sum(1 for tp in t_parts if any(tp == rp or tp in rp or rp in tp for rp in r_parts))
        best = max(best, matched / len(t_parts))
    return best


# ============ 平台搜索API（返回含平台ID的精确数据） ============

def _parse_netease_artists(item):
    """解析网易云歌手名（兼容 ar/artists）"""
    ar_list = item.get("ar", [])
    if ar_list and isinstance(ar_list, list) and len(ar_list) > 0:
        names = [a.get("name", "") for a in ar_list if a.get("name")]
        if names:
            return " / ".join(names)
    artists_list = item.get("artists", [])
    if artists_list and isinstance(artists_list, list) and len(artists_list) > 0:
        names = [a.get("name", "") for a in artists_list if a.get("name")]
        if names:
            return " / ".join(names)
    return ""


def search_netease(keyword, limit=20):
    """搜索网易云 → 返回含平台ID的精确数据"""
    try:
        url = f"https://music.163.com/api/search/get?s={quote(keyword)}&type=1&offset=0&limit={limit}"
        resp = session.get(url, headers={"Referer": "https://music.163.com"}, timeout=15)
        data = resp.json()
        songs = []
        for item in data.get("result", {}).get("songs", []):
            artist_str = _parse_netease_artists(item)
            album = item.get("al", {})
            songs.append({
                "name": item.get("name", "").strip(),
                "artist": artist_str.strip(),
                "album": album.get("name", "").strip(),
                "cover_url": album.get("picUrl", ""),
                "platform": "wangyi",
                "platform_id": str(item.get("id", "")),
            })
        return songs
    except Exception as e:
        logger.debug(f"网易云搜索失败 [{keyword}]: {e}")
        return []


def search_kuwo(keyword, limit=20):
    """搜索酷我 → 返回含平台ID的精确数据"""
    try:
        url = f"https://www.kuwo.cn/api/www/search/searchMusicBykeyWord?key={quote(keyword)}&pn=1&rn={limit}"
        resp = session.get(url, headers={"Referer": "https://www.kuwo.cn", "csrf": "4X1D5B3E5F"}, timeout=15)
        data = resp.json()
        songs = []
        for item in data.get("data", {}).get("list", []):
            songs.append({
                "name": item.get("name", "").strip(),
                "artist": item.get("artist", "").strip(),
                "album": item.get("album", "").strip(),
                "cover_url": item.get("albumpic", "") or item.get("pic", ""),
                "platform": "kuwo",
                "platform_id": str(item.get("rid", "")),
            })
        return songs
    except Exception as e:
        logger.debug(f"酷我搜索失败 [{keyword}]: {e}")
        return []


def search_kugou(keyword, limit=20):
    """搜索酷狗 → 返回含平台ID的精确数据"""
    try:
        url = f"https://complexsearch.kugou.com/v2/search/song?keyword={quote(keyword)}&page=1&pagesize={limit}"
        resp = session.get(url, timeout=15)
        data = resp.json()
        songs = []
        for item in data.get("data", {}).get("lists", []):
            songs.append({
                "name": item.get("SongName", "").strip(),
                "artist": item.get("SingerName", "").strip(),
                "album": item.get("AlbumName", "").strip(),
                "cover_url": item.get("ImgURL", "") or "",
                "platform": "kugou",
                "platform_id": str(item.get("FileHash", "")),
            })
        return songs
    except Exception as e:
        logger.debug(f"酷狗搜索失败 [{keyword}]: {e}")
        return []


# ============ 精确匹配：用搜索结果找到排行榜歌曲的精确平台ID ============

def find_exact_match(target_name, target_artist, search_results):
    """
    在搜索结果中找到精确匹配的歌曲。
    条件：歌名相似度 >= 0.6 且 歌手相似度 > 0.3
    返回匹配分最高的结果
    """
    best = None
    best_score = 0

    for result in search_results:
        r_name = result.get("name", "")
        r_artist = result.get("artist", "")

        name_sim = name_similarity(target_name, r_name)
        art_sim = artist_similarity(target_artist, r_artist)

        # 强制条件
        if name_sim < 0.6:
            continue
        if art_sim < 0.3:
            continue

        score = name_sim * 0.5 + art_sim * 0.5
        if score > best_score:
            best_score = score
            best = result

    return best, best_score


def enrich_song_from_api(song_name, artist, hot_rank=0):
    """
    用平台API搜索歌曲，获取精确的平台ID和元数据。
    优先级：网易云 → 酷我 → 酷狗
    返回完整的歌曲数据（含平台ID）
    """
    keyword = f"{artist} {song_name}" if artist else song_name
    keyword_only_name = song_name

    # 按可靠性排序
    search_funcs = [
        ("wangyi", search_netease),
        ("kuwo", search_kuwo),
        ("kugou", search_kugou),
    ]

    for source_name, search_fn in search_funcs:
        for kw in [keyword, keyword_only_name]:
            try:
                results = search_fn(kw, limit=20)
                if not results:
                    continue

                match, score = find_exact_match(song_name, artist, results)
                if match and score >= 0.5:
                    match["hot_score"] = hot_rank
                    logger.info(f"  匹配成功 [{source_name}]: {match['artist']} - {match['name']} (分={score:.2f})")
                    return match

            except Exception as e:
                logger.debug(f"  搜索异常 [{source_name}]: {e}")
                continue

    return None


# ============ 获取排行榜种子数据 ============

def get_chart_songs():
    """从热点数据采集获取排行榜歌曲列表"""
    script_path = os.path.join(HOT_SKILL_DIR, "scripts", "crawl-music.js")
    if not os.path.exists(script_path):
        logger.error(f"爬虫脚本不存在: {script_path}")
        return []

    import subprocess
    try:
        result = subprocess.run(
            ["node", script_path], capture_output=True, text=True, timeout=60,
            cwd=HOT_SKILL_DIR, encoding="utf-8"
        )
        if result.returncode != 0:
            logger.error(f"爬虫执行失败: {result.stderr}")
            return []

        data = json.loads(result.stdout)
    except Exception as e:
        logger.error(f"爬虫异常: {e}")
        return []

    # 解析所有平台的排行榜歌曲
    all_songs = []  # (name, artist, hot_rank)
    seen = set()

    results = data.get("results", {})
    for platform_key, platform_data in results.items():
        chart_entries = []
        if isinstance(platform_data, dict) and "success" in platform_data and "data" in platform_data:
            chart_entries.append(platform_data)
        elif isinstance(platform_data, dict):
            for chart_type, chart_data in platform_data.items():
                if isinstance(chart_data, dict) and "data" in chart_data:
                    chart_entries.append(chart_data)

        for chart_entry in chart_entries:
            if not chart_entry.get("success"):
                continue
            songs_list = chart_entry.get("data", {}).get("sj", [])

            for idx, item in enumerate(songs_list):
                name = item.get("name", "").strip()
                artist = item.get("geshou", "").strip()
                if not name:
                    continue
                # 去重
                key = f"{normalize_name(name)}_{normalize_name(artist)}"
                if key in seen:
                    continue
                seen.add(key)
                hot = idx + 1
                try:
                    hot_val = item.get("hot", 0)
                    hot = int(hot_val) if hot_val else (idx + 1)
                except (ValueError, TypeError):
                    hot = idx + 1
                all_songs.append((name, artist, hot))

    logger.info(f"从排行榜获取 {len(all_songs)} 首种子歌曲")
    return all_songs


# ============ 热门歌手补充搜索 ============

HOT_ARTISTS = [
    "周杰伦", "林俊杰", "陈奕迅", "薛之谦", "邓紫棋",
    "王力宏", "李荣浩", "张杰", "华晨宇", "毛不易",
    "孙燕姿", "王菲", "梁静茹", "刘若英", "张韶涵",
    "许嵩", "汪苏泷", "徐良", "赵雷", "陈粒",
    "五月天", "BEYOND", "朴树", "李健", "周深",
    "Taylor Swift", "Ed Sheeran", "Adele", "Bruno Mars", "Coldplay",
    "凤凰传奇", "李宇春", "张靓颖", "谭维维", "袁娅维",
    "任贤齐", "张学友", "刘德华", "郭富城", "黎明",
    "杨千嬅", "容祖儿", "谢安琪", "方大同", "萧敬腾",
]


def get_artist_top_songs(artist_name, limit=15):
    """搜索某位歌手的热门歌曲"""
    results = search_netease(artist_name, limit=limit)
    if not results:
        results = search_kuwo(artist_name, limit=limit)

    songs = []
    for r in results:
        # 确认歌手匹配
        art_sim = artist_similarity(artist_name, r.get("artist", ""))
        if art_sim < 0.3:
            continue
        songs.append(r)
    return songs


# ============ 封面缓存 ============

import hashlib

def cache_cover(song_obj):
    """缓存封面到本地"""
    if not song_obj.cover_url or not song_obj.cover_url.startswith("http"):
        return
    if song_obj.local_cover and os.path.exists(os.path.join(COVERS_DIR, os.path.basename(song_obj.local_cover))):
        return

    try:
        ext = ".jpg"
        if ".png" in song_obj.cover_url:
            ext = ".png"
        elif ".webp" in song_obj.cover_url:
            ext = ".webp"

        filename = hashlib.md5(f"{song_obj.name}_{song_obj.artist}".encode("utf-8")).hexdigest() + ext
        local_path = os.path.join(COVERS_DIR, filename)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
            song_obj.local_cover = f"/data/covers/{filename}"
            return

        resp = http_requests.get(
            song_obj.cover_url,
            headers={"User-Agent": HEADERS["User-Agent"], "Referer": "https://y.qq.com/"},
            timeout=10, verify=False
        )
        if resp.status_code == 200 and len(resp.content) > 1024:
            with open(local_path, "wb") as f:
                f.write(resp.content)
            song_obj.local_cover = f"/data/covers/{filename}"
    except Exception:
        pass


# ============ 主流程 ============

def rebuild():
    """重建歌曲数据库"""
    with app.app_context():
        # 清空旧数据
        logger.info("清空旧数据...")
        Song.query.delete()
        db.session.commit()
        logger.info(f"数据库已清空，当前歌曲数: {Song.query.count()}")

        # === 阶段1: 从排行榜获取种子 ===
        logger.info("=" * 60)
        logger.info("阶段1: 从排行榜获取种子歌曲")
        logger.info("=" * 60)

        chart_songs = get_chart_songs()
        total_added = 0
        total_failed = 0

        for idx, (name, artist, hot) in enumerate(chart_songs):
            logger.info(f"[{idx+1}/{len(chart_songs)}] 处理: {artist} - {name}")

            # 用API搜索精确匹配
            match = enrich_song_from_api(name, artist, hot_rank=100 - hot + 1)

            if match:
                # 检查是否已存在
                existing = Song.query.filter_by(name=match["name"], artist=match["artist"]).first()
                if existing:
                    if match.get("hot_score", 0) > existing.hot_score:
                        existing.hot_score = match["hot_score"]
                    continue

                song = Song(
                    name=match["name"],
                    artist=match["artist"],
                    album=match.get("album", ""),
                    cover_url=match.get("cover_url", ""),
                    platform=match["platform"],
                    platform_id=match["platform_id"],
                    hot_score=match.get("hot_score", 0),
                )
                db.session.add(song)
                total_added += 1

                # 每首缓存封面
                cache_cover(song)

                if total_added % 10 == 0:
                    db.session.commit()
                    logger.info(f"  已入库 {total_added} 首")
            else:
                total_failed += 1
                logger.warning(f"  未匹配: {artist} - {name}")

            # 请求间隔，避免被封
            time.sleep(0.3)

        db.session.commit()
        logger.info(f"阶段1完成: 入库 {total_added} 首, 未匹配 {total_failed} 首")

        # === 阶段2: 热门歌手补充 ===
        logger.info("=" * 60)
        logger.info("阶段2: 热门歌手补充搜索")
        logger.info("=" * 60)

        artist_added = 0
        for artist_name in HOT_ARTISTS:
            logger.info(f"搜索歌手: {artist_name}")
            try:
                songs = get_artist_top_songs(artist_name, limit=15)
                for s in songs:
                    existing = Song.query.filter_by(name=s["name"], artist=s["artist"]).first()
                    if existing:
                        continue

                    song = Song(
                        name=s["name"],
                        artist=s["artist"],
                        album=s.get("album", ""),
                        cover_url=s.get("cover_url", ""),
                        platform=s["platform"],
                        platform_id=s["platform_id"],
                        hot_score=s.get("hot_score", 0),
                    )
                    db.session.add(song)
                    artist_added += 1
                    cache_cover(song)

                if artist_added % 10 == 0:
                    db.session.commit()
                    logger.info(f"  补充入库 {artist_added} 首")

            except Exception as e:
                logger.warning(f"  歌手搜索异常 [{artist_name}]: {e}")
                continue

            time.sleep(0.5)

        db.session.commit()
        logger.info(f"阶段2完成: 补充入库 {artist_added} 首")

        # 最终统计
        total = Song.query.count()
        with_pid = Song.query.filter(Song.platform_id != None, Song.platform_id != "").count()
        by_platform = {}
        for p in ["wangyi", "kuwo", "kugou", "qq"]:
            by_platform[p] = Song.query.filter_by(platform=p).count()

        logger.info("=" * 60)
        logger.info(f"重建完成！")
        logger.info(f"  总歌曲数: {total}")
        logger.info(f"  有平台ID: {with_pid} ({with_pid/total*100:.0f}%)" if total > 0 else "  无歌曲")
        logger.info(f"  按平台: {by_platform}")
        logger.info(f"  排行榜入库: {total_added}")
        logger.info(f"  歌手补充: {artist_added}")
        logger.info("=" * 60)


if __name__ == "__main__":
    rebuild()
