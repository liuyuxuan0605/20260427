# -*- coding: utf-8 -*-
"""
添加热门说唱歌手的免费歌曲
歌手：艾热、GAI、王以太、刘聪
每人最多23首，fee=0/8，必须有封面+歌词
"""
import os
import sys
import re
import time
import logging
import hashlib
import warnings
import requests as http_requests
from urllib.parse import quote

# Windows UTF-8
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from flask import Flask
from models.db import db, Song
from config import SQLALCHEMY_DATABASE_URI, COVERS_DIR

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["COVERS_DIR"] = COVERS_DIR
db.init_app(app)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

ALGER_BASE = "https://mc.alger.fun/api"
ALGER_REFERER = "https://mc.alger.fun/"

session = http_requests.Session()
session.headers.update(HEADERS)
session.verify = False

# 说唱歌手列表及其搜索关键词
RAP_ARTISTS = [
    {"name": "艾热", "keywords": ["艾热"]},
    {"name": "GAI", "keywords": ["GAI", "GAI周延"]},
    {"name": "王以太", "keywords": ["王以太"]},
    {"name": "刘聪", "keywords": ["刘聪", "Key.L"]},
]

# 每个歌手最多添加的歌曲数
MAX_PER_ARTIST = 23


def _parse_netease_artists(item):
    """解析网易云歌手名"""
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


def _normalize_name(name):
    """标准化歌名用于去重"""
    n = re.sub(r'[（(].*?[）)]', '', name)
    n = re.sub(r'[\s\-_\\/@&·]', '', n)
    return n.lower().strip()


def search_netease(keyword, limit=30, offset=0):
    """搜索网易云音乐"""
    try:
        url = f"https://music.163.com/api/search/get?s={quote(keyword)}&type=1&offset={offset}&limit={limit}"
        resp = session.get(url, headers={"Referer": "https://music.163.com"}, timeout=15)
        data = resp.json()
        songs = []
        for item in data.get("result", {}).get("songs", []):
            artist_str = _parse_netease_artists(item)
            album = item.get("al", {})
            fee = item.get("fee", -1)
            songs.append({
                "name": item.get("name", "").strip(),
                "artist": artist_str.strip(),
                "album": album.get("name", "").strip(),
                "cover_url": album.get("picUrl", ""),
                "platform": "wangyi",
                "platform_id": str(item.get("id", "")),
                "fee": fee,
            })
        return songs
    except Exception as e:
        logger.debug(f"搜索失败 [{keyword}]: {e}")
        return []


def get_cover_from_alger(platform_id):
    """从 algermusic 获取封面URL"""
    try:
        url = f"{ALGER_BASE}/song/detail?ids={platform_id}"
        resp = session.get(url, headers={"Referer": ALGER_REFERER}, timeout=10, verify=False)
        data = resp.json()
        songs = data.get("songs", [])
        if songs:
            al = songs[0].get("al", {})
            pic = al.get("picUrl", "")
            if pic:
                return pic
    except Exception:
        pass
    return ""


def get_lyric_from_alger(platform_id):
    """从 algermusic 获取 LRC 歌词"""
    try:
        url = f"{ALGER_BASE}/lyric?id={platform_id}"
        resp = session.get(url, headers={"Referer": ALGER_REFERER}, timeout=10, verify=False)
        data = resp.json()
        lrc = data.get("lrc", {}).get("lyric", "")
        if lrc and lrc.strip() and "[" in lrc and "]" in lrc:
            return lrc.strip()
        tlyric = data.get("tlyric", {}).get("lyric", "")
        if tlyric and tlyric.strip() and "[" in tlyric and "]" in tlyric:
            return tlyric.strip()
    except Exception:
        pass
    return ""


def cache_cover(cover_url, song_name, artist):
    """缓存封面到本地"""
    if not cover_url or not cover_url.startswith("http"):
        return ""
    try:
        ext = ".jpg"
        if ".png" in cover_url:
            ext = ".png"
        elif ".webp" in cover_url:
            ext = ".webp"
        filename = hashlib.md5(f"{song_name}_{artist}".encode("utf-8")).hexdigest() + ext
        local_path = os.path.join(COVERS_DIR, filename)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
            return f"/data/covers/{filename}"
        resp = http_requests.get(
            cover_url,
            headers={"User-Agent": HEADERS["User-Agent"], "Referer": "https://music.163.com"},
            timeout=10, verify=False
        )
        if resp.status_code == 200 and len(resp.content) > 1024:
            os.makedirs(COVERS_DIR, exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return f"/data/covers/{filename}"
    except Exception as e:
        logger.debug(f"  封面缓存失败: {e}")
    return cover_url


def is_rap_song_by_artist(song_info, artist_config):
    """判断这首歌是否确实由该说唱歌手演唱（避免搜到同名其他歌手的歌）"""
    artist_str = song_info["artist"]
    name = song_info["name"]
    for kw in artist_config["keywords"]:
        if kw.lower() in artist_str.lower():
            return True
    # 特殊处理：刘聪可能在歌手名里用 "Key.L" 或 "刘聪KEY.L"
    if artist_config["name"] == "刘聪":
        if "刘聪" in artist_str or "KEY.L" in artist_str or "Key.L" in artist_str:
            return True
    return False


def main():
    with app.app_context():
        # 收集现有歌曲的去重信息
        existing_pids = set()
        existing_name_artist = set()
        for s in Song.query.filter(Song.platform_id != None, Song.platform_id != "").all():
            existing_pids.add(str(s.platform_id))
            key = f"{_normalize_name(s.name)}_{_normalize_name(s.artist)}"
            existing_name_artist.add(key)

        total_added = 0

        for artist_config in RAP_ARTISTS:
            artist_name = artist_config["name"]
            print(f"\n{'='*50}")
            print(f"  搜索歌手: {artist_name}")
            print(f"{'='*50}")

            added_count = 0

            for keyword in artist_config["keywords"]:
                if added_count >= MAX_PER_ARTIST:
                    break

                print(f"\n  搜索关键词: {keyword}")
                songs = search_netease(keyword, limit=30)

                for song in songs:
                    if added_count >= MAX_PER_ARTIST:
                        break

                    pid = song["platform_id"]
                    name = song["name"]
                    artist = song["artist"]
                    fee = song.get("fee", -1)

                    # 1. 确保是该歌手的歌
                    if not is_rap_song_by_artist(song, artist_config):
                        continue

                    # 2. 严格过滤VIP
                    if fee == 1:
                        print(f"    SKIP VIP: {artist} - {name} (fee=1)")
                        continue
                    if fee not in (0, 8):
                        print(f"    SKIP fee={fee}: {artist} - {name}")
                        continue

                    # 3. 质量过滤
                    name_lower = name.lower()
                    skip_quality = False
                    for kw in ["DJ版", "dj版", "伴奏", "降速", "加速", "0.8x", "1.2x"]:
                        if kw in name_lower:
                            skip_quality = True
                            break
                    # 小众翻唱/Remix跳过
                    if not skip_quality and re.search(r'remix|混音', name_lower):
                        skip_quality = True  # 说唱歌手的Remix一般质量不高
                    if skip_quality:
                        continue

                    # 4. 去重
                    if pid in existing_pids:
                        print(f"    SKIP 重复PID: {artist} - {name}")
                        continue
                    name_artist_key = f"{_normalize_name(name)}_{_normalize_name(artist)}"
                    if name_artist_key in existing_name_artist:
                        print(f"    SKIP 重复歌名: {artist} - {name}")
                        continue

                    # 5. 获取封面
                    cover_url = song.get("cover_url", "")
                    if not cover_url:
                        cover_url = get_cover_from_alger(pid)
                        if not cover_url:
                            print(f"    SKIP 无封面: {artist} - {name}")
                            continue

                    # 6. 获取歌词
                    lyric = get_lyric_from_alger(pid)
                    if not lyric:
                        print(f"    SKIP 无歌词: {artist} - {name}")
                        continue

                    # 7. 缓存封面
                    local_cover = cache_cover(cover_url, name, artist)

                    # 8. 入库
                    new_song = Song(
                        name=name,
                        artist=artist or artist_name,
                        album=song.get("album", ""),
                        cover_url=local_cover or cover_url,
                        local_cover=local_cover if local_cover.startswith("/data/covers/") else "",
                        platform="wangyi",
                        platform_id=pid,
                        lyric=lyric,
                        hot_score=0,
                    )
                    db.session.add(new_song)
                    existing_pids.add(pid)
                    existing_name_artist.add(name_artist_key)
                    added_count += 1
                    total_added += 1
                    print(f"    +[{added_count}] {artist} - {name} (fee={fee})")

                    time.sleep(0.2)

                time.sleep(0.5)

            db.session.commit()
            print(f"\n  {artist_name}: 共添加 {added_count} 首")

        print(f"\n{'='*50}")
        print(f"  全部完成! 总计添加 {total_added} 首")
        print(f"  数据库总歌曲数: {Song.query.count()}")
        print(f"{'='*50}")


if __name__ == "__main__":
    main()
