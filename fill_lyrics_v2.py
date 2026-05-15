# -*- coding: utf-8 -*-
"""
批量补充LRC歌词
使用 algermusic /api/lyric 接口，逐首获取带时间戳的LRC歌词
"""
import os
import sys
import time
import logging
import warnings

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
from config import SQLALCHEMY_DATABASE_URI

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

import requests as http_requests

session = http_requests.Session()
session.headers.update(HEADERS)
session.verify = False


def get_lyric_alger(platform_id):
    """通过 algermusic API 获取LRC歌词"""
    try:
        url = f"https://mc.alger.fun/api/lyric?id={platform_id}"
        resp = session.get(url, headers={"Referer": "https://mc.alger.fun/"}, timeout=10, verify=False)
        data = resp.json()
        
        # 优先原歌词
        lrc = data.get("lrc", {}).get("lyric", "")
        if lrc and lrc.strip():
            # 验证是否包含时间戳（有效LRC格式）
            if "[" in lrc and "]" in lrc:
                return lrc.strip()
        
        # 试试翻译歌词
        tlyric = data.get("tlyric", {}).get("lyric", "")
        if tlyric and tlyric.strip():
            if "[" in tlyric and "]" in tlyric:
                return tlyric.strip()
        
        # 如果返回了纯文本歌词（无时间戳），也保存
        if lrc and lrc.strip():
            return lrc.strip()
            
    except Exception:
        pass
    return ""


def fill_lyrics():
    with app.app_context():
        # 找出没有歌词的歌曲
        no_lyric_songs = Song.query.filter(
            Song.platform_id != None,
            Song.platform_id != "",
            (Song.lyric == None) | (Song.lyric == "")
        ).all()
        
        logger.info(f"=== 需要补充歌词: {len(no_lyric_songs)} 首 ===")
        
        total_lyrics = 0
        total_lrc = 0  # 带时间戳的LRC歌词
        
        for idx, song in enumerate(no_lyric_songs):
            lyric = get_lyric_alger(song.platform_id)
            if lyric:
                song.lyric = lyric
                total_lyrics += 1
                # 检查是否有LRC时间戳
                if "[" in lyric and "]" in lyric and any(c.isdigit() for c in lyric[:10]):
                    total_lrc += 1
            
            if (idx + 1) % 50 == 0:
                db.session.commit()
                logger.info(f"  歌词进度: {idx+1}/{len(no_lyric_songs)}, 已获取 {total_lyrics} (LRC格式 {total_lrc})")
            
            time.sleep(0.1)
        
        db.session.commit()
        
        # 最终统计
        final_total = Song.query.count()
        with_lyric = Song.query.filter((Song.lyric != None) & (Song.lyric != '')).count()
        with_lrc = 0
        for s in Song.query.filter((Song.lyric != None) & (Song.lyric != '')).all():
            if s.lyric and "[" in s.lyric and any(c.isdigit() for c in s.lyric[:10]):
                with_lrc += 1
        
        logger.info(f"\n========== 最终统计 ==========")
        logger.info(f"  总歌曲: {final_total}")
        logger.info(f"  有歌词: {with_lyric} ({with_lyric*100//max(final_total,1)}%)")
        logger.info(f"  LRC格式歌词: {with_lrc}")
        logger.info(f"  无歌词: {final_total - with_lyric}")


if __name__ == "__main__":
    fill_lyrics()
