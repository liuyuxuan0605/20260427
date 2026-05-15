# -*- coding: utf-8 -*-
"""
说唱专区免费歌曲扩充脚本
核心原则：
1. 只添加网易云免费歌曲：fee=0 + fee=8 = 完整版
2. 跳过 fee=1（VIP）= 30秒试听版
3. 每位歌手最多添加21首免费歌曲
4. 保存 platform_id + 封面 + 歌词
"""
import os
import sys
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
db.init_app(app)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

session = http_requests.Session()
session.headers.update(HEADERS)
session.verify = False

# ============ 说唱歌手列表 ============
# 热门中文说唱歌手 + 国际说唱歌手
RAP_ARTISTS = [
    # 中文说唱
    "宝石Gem", "GAI周延", "艾热AIR", "王以太", "法老",
    "杨和苏KeyNG", "谢帝", "那吾克热", "热狗MC Hotdog", "潘玮柏",
    "VAVA毛衍七", "Gai", "派克特", "刘聪KEY.L", "功夫胖KUNGFU-PEN",
    "丁飞DeeBaD", "盛宇SHINE", "早安", "布瑞吉Bridge", "TizzyT",
    "JonyJ", "满舒克", "小鬼王琳凯", "更高兄弟HigherBrothers", "马思唯",
    "KNOWKNOW", "Psy.P", "Melo", "DZknow", "Ty.",
    # 国际说唱
    "Eminem", "Drake", "Kendrick Lamar", "J. Cole",
    "Travis Scott", "Post Malone", "Lil Nas X",
]


def _parse_netease_artists(item):
    """解析网易云歌手名"""
    ar_list = item.get("ar", [])
    if ar_list and isinstance(ar_list, list):
        names = [a.get("name", "") for a in ar_list if a.get("name")]
        if names:
            return " / ".join(names)
    artists_list = item.get("artists", [])
    if artists_list and isinstance(ar_list, list):
        names = [a.get("name", "") for a in artists_list if a.get("name")]
        if names:
            return " / ".join(names)
    return ""


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


def verify_play_url(platform_id):
    """验证播放链接是否有效且为完整版"""
    song_id = str(platform_id)
    try:
        redirect_url = f"https://music.163.com/song/media/outer/url?id={song_id}"
        resp = session.get(redirect_url, timeout=10, allow_redirects=False)
        if resp.status_code == 302:
            loc = resp.headers.get("Location", "")
            if loc and "music.126.net" in loc:
                try:
                    head_resp = session.head(loc, timeout=8, allow_redirects=True)
                    cl = int(head_resp.headers.get("Content-Length", 0))
                    size_kb = cl / 1024
                    if size_kb > 800:
                        return True, loc, size_kb
                    else:
                        return False, None, size_kb
                except Exception:
                    return True, loc, 0
        elif resp.status_code == 200:
            return True, None, 0
    except Exception as e:
        logger.debug(f"  验证失败 [{song_id}]: {e}")
    return False, None, 0


def fetch_lyric(platform_id):
    """通过 algermusic API 获取歌词"""
    try:
        url = f"https://mc.alger.fun/api/lyric?id={platform_id}"
        resp = session.get(url, headers={"Referer": "https://mc.alger.fun/"}, timeout=10, verify=False)
        data = resp.json()
        # 优先 lrc 歌词
        lrc = data.get("lrc", {}).get("lyric", "")
        if lrc and lrc.strip():
            return lrc.strip()
        # 备用 tlyric
        tlyric = data.get("tlyric", {}).get("lyric", "")
        if tlyric and tlyric.strip():
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
            headers={"User-Agent": HEADERS["User-Agent"], "Referer": "https://music.163.com/"},
            timeout=10, verify=False
        )
        if resp.status_code == 200 and len(resp.content) > 1024:
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return f"/data/covers/{filename}"
    except Exception:
        pass
    return cover_url


def add_rap_songs():
    """增量添加说唱免费歌曲"""
    with app.app_context():
        existing_count = Song.query.count()
        logger.info(f"=== 开始扩充说唱免费歌曲（当前 {existing_count} 首）===")

        # 获取已入库的平台ID集合
        existing_pids = set()
        for s in Song.query.filter(Song.platform_id != None, Song.platform_id != "").all():
            existing_pids.add(str(s.platform_id))
        logger.info(f"已有 platform_id 数量: {len(existing_pids)}")

        total_added = 0
        total_vip_skip = 0
        total_dup_skip = 0
        total_verify_fail = 0
        MAX_PER_ARTIST = 21

        for artist_idx, artist_name in enumerate(RAP_ARTISTS):
            logger.info(f"\n[{artist_idx+1}/{len(RAP_ARTISTS)}] 搜索: {artist_name}")

            # 搜索前50首，扩大覆盖
            results = search_netease(artist_name, limit=30)
            results2 = search_netease(artist_name, limit=30, offset=30)
            all_results = results + results2

            artist_added = 0
            artist_vip = 0
            artist_dup = 0
            artist_fail = 0

            for song_data in all_results:
                if artist_added >= MAX_PER_ARTIST:
                    logger.info(f"  {artist_name}: 已达上限 {MAX_PER_ARTIST} 首，跳过剩余")
                    break

                name = song_data["name"]
                artist = song_data["artist"]
                pid = song_data["platform_id"]
                fee = song_data.get("fee", -1)

                if not name or not pid:
                    continue

                # 去重
                if pid in existing_pids:
                    artist_dup += 1
                    continue

                # VIP歌曲跳过（fee=1）
                if fee == 1:
                    artist_vip += 1
                    continue

                # fee=0 或 fee=8：验证播放链接
                is_valid, play_url, size_kb = verify_play_url(pid)
                if not is_valid:
                    artist_fail += 1
                    continue

                # 缓存封面
                cover_url = song_data.get("cover_url", "")
                local_cover = cache_cover(cover_url, name, artist) if cover_url else ""

                # 获取歌词
                lyric = fetch_lyric(pid)
                has_lyric = "✅" if lyric else "❌"

                # 入库
                song = Song(
                    name=name,
                    artist=artist or "未知",
                    album=song_data.get("album", ""),
                    genre="说唱",
                    cover_url=local_cover or cover_url,
                    local_cover=local_cover,
                    lyric=lyric,
                    platform="wangyi",
                    platform_id=pid,
                    hot_score=max(0, 500 - total_added),
                )
                db.session.add(song)
                existing_pids.add(pid)
                artist_added += 1
                total_added += 1

                logger.info(f"  +{name} | {artist} | fee={fee} | 歌词={has_lyric}")

                if total_added % 10 == 0:
                    db.session.commit()
                    logger.info(f"  >> 累计入库 {total_added} 首")

                time.sleep(0.15)

            total_vip_skip += artist_vip
            total_dup_skip += artist_dup
            total_verify_fail += artist_fail

            logger.info(
                f"  {artist_name}: +{artist_added}首, "
                f"VIP={artist_vip}, 重复={artist_dup}, 失败={artist_fail}"
            )

            db.session.commit()
            time.sleep(0.3)

        # 最终统计
        db.session.commit()
        final_count = Song.query.count()

        logger.info("\n" + "=" * 60)
        logger.info("说唱歌曲扩充完成！")
        logger.info(f"  原有歌曲: {existing_count}")
        logger.info(f"  新增免费: {total_added}")
        logger.info(f"  VIP跳过: {total_vip_skip}")
        logger.info(f"  重复跳过: {total_dup_skip}")
        logger.info(f"  验证失败: {total_verify_fail}")
        logger.info(f"  当前总数: {final_count}")
        logger.info("=" * 60)


if __name__ == "__main__":
    add_rap_songs()
