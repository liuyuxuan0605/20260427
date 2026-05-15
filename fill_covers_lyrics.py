# -*- coding: utf-8 -*-
"""
批量补充封面和歌词
1. 封面：用网易云 /api/song/detail 获取封面URL → 下载到本地
2. 歌词：用 algermusic /api/lyric 获取LRC歌词
"""
import os
import sys
import time
import logging
import hashlib
import warnings
import requests as http_requests
from urllib.parse import quote

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


def batch_get_song_details(platform_ids):
    """批量获取网易云歌曲详情（封面+歌名+歌手），每次最多1000个"""
    ids_str = ",".join(platform_ids)
    try:
        url = f"https://music.163.com/api/song/detail/?id={platform_ids[0]}&ids=[{ids_str}]"
        resp = session.get(url, headers={"Referer": "https://music.163.com"}, timeout=20)
        data = resp.json()
        songs = data.get("songs", [])
        result = {}
        for s in songs:
            sid = str(s.get("id", ""))
            album = s.get("album", {}) or s.get("al", {})
            pic_url = album.get("picUrl", "") or album.get("blurPicUrl", "")
            result[sid] = {
                "cover_url": pic_url,
                "album_name": album.get("name", ""),
            }
        return result
    except Exception as e:
        logger.debug(f"批量获取详情失败: {e}")
        return {}


def get_lyric_alger(platform_id):
    """通过 algermusic API 获取歌词"""
    try:
        url = f"https://mc.alger.fun/api/lyric?id={platform_id}"
        resp = session.get(url, headers={"Referer": "https://mc.alger.fun/"}, timeout=10, verify=False)
        data = resp.json()
        lrc = data.get("lrc", {}).get("lyric", "")
        if lrc and lrc.strip():
            return lrc.strip()
        tlyric = data.get("tlyric", {}).get("lyric", "")
        if tlyric and tlyric.strip():
            return tlyric.strip()
    except Exception:
        pass
    return ""


def download_cover(cover_url, song_name, artist):
    """下载封面到本地"""
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
    return ""


def fill_covers_and_lyrics():
    """批量补充封面和歌词"""
    with app.app_context():
        total_songs = Song.query.count()
        
        # === 第一步：补充封面 ===
        # 找出没有封面的网易云歌曲（有platform_id的）
        no_cover_songs = Song.query.filter(
            Song.platform == "wangyi",
            Song.platform_id != None,
            Song.platform_id != "",
            (Song.local_cover == None) | (Song.local_cover == ""),
            (Song.cover_url == None) | (Song.cover_url == "") | (Song.cover_url == "/static/img/default-cover.svg")
        ).all()
        
        logger.info(f"=== 需要补充封面的网易云歌曲: {len(no_cover_songs)} 首 ===")
        
        if no_cover_songs:
            # 按1000首一批获取详情
            batch_size = 500
            total_covers = 0
            
            for batch_start in range(0, len(no_cover_songs), batch_size):
                batch = no_cover_songs[batch_start:batch_start + batch_size]
                pids = [s.platform_id for s in batch if s.platform_id]
                
                if not pids:
                    continue
                
                # 批量获取详情
                details = batch_get_song_details(pids)
                
                for song in batch:
                    pid = str(song.platform_id)
                    if pid in details and details[pid]["cover_url"]:
                        cover_url = details[pid]["cover_url"]
                        local_cover = download_cover(cover_url, song.name, song.artist)
                        if local_cover:
                            song.cover_url = local_cover
                            song.local_cover = local_cover
                        else:
                            song.cover_url = cover_url
                        total_covers += 1
                
                db.session.commit()
                logger.info(f"  封面进度: {min(batch_start + batch_size, len(no_cover_songs))}/{len(no_cover_songs)}, 已获取 {total_covers}")
                time.sleep(0.5)
            
            logger.info(f"封面补充完成: 获取 {total_covers} 张")
        
        # === 第二步：补充歌词 ===
        no_lyric_songs = Song.query.filter(
            Song.platform == "wangyi",
            Song.platform_id != None,
            Song.platform_id != "",
            (Song.lyric == None) | (Song.lyric == "")
        ).all()
        
        logger.info(f"\n=== 需要补充歌词的网易云歌曲: {len(no_lyric_songs)} 首 ===")
        
        if no_lyric_songs:
            total_lyrics = 0
            for idx, song in enumerate(no_lyric_songs):
                lyric = get_lyric_alger(song.platform_id)
                if lyric:
                    song.lyric = lyric
                    total_lyrics += 1
                
                if (idx + 1) % 50 == 0:
                    db.session.commit()
                    logger.info(f"  歌词进度: {idx+1}/{len(no_lyric_songs)}, 已获取 {total_lyrics}")
                
                time.sleep(0.1)
            
            db.session.commit()
            logger.info(f"歌词补充完成: 获取 {total_lyrics} 条")
        
        # 最终统计
        final_total = Song.query.count()
        with_cover = Song.query.filter(
            (Song.cover_url != None) & (Song.cover_url != '') & (Song.cover_url != '/static/img/default-cover.svg')
        ).count()
        with_lyric = Song.query.filter((Song.lyric != None) & (Song.lyric != '')).count()
        
        logger.info(f"\n=== 最终统计 ===")
        logger.info(f"  总歌曲: {final_total}")
        logger.info(f"  有封面: {with_cover} ({with_cover*100//final_total}%)")
        logger.info(f"  有歌词: {with_lyric} ({with_lyric*100//final_total}%)")


if __name__ == "__main__":
    fill_covers_and_lyrics()
