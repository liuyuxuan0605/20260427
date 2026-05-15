# -*- coding: utf-8 -*-
"""
增强版封面补充 + 清理无封面歌曲
1. 网易云批量API获取封面
2. algermusic歌曲详情API获取封面
3. algermusic搜索API按歌名+歌手搜索获取封面
4. 删除所有仍无封面的歌曲
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


def batch_get_song_details(platform_ids):
    """批量获取网易云歌曲详情（封面），每次最多1000个"""
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
            result[sid] = {"cover_url": pic_url}
        return result
    except Exception as e:
        logger.debug(f"批量获取详情失败: {e}")
        return {}


def alger_song_detail(platform_id):
    """通过algermusic API获取歌曲封面"""
    try:
        url = f"https://mc.alger.fun/api/song/detail?ids={platform_id}"
        resp = session.get(url, headers={"Referer": "https://mc.alger.fun/"}, timeout=10, verify=False)
        data = resp.json()
        songs = data.get("songs", [])
        if songs:
            album = songs[0].get("al", {}) or songs[0].get("album", {})
            pic_url = album.get("picUrl", "")
            if pic_url:
                return pic_url
    except Exception:
        pass
    return ""


def alger_search_cover(song_name, artist):
    """通过algermusic搜索API按歌名+歌手搜索获取封面"""
    try:
        keyword = f"{song_name} {artist}"
        url = f"https://mc.alger.fun/api/search?keywords={quote(keyword)}&limit=5&type=1"
        resp = session.get(url, headers={"Referer": "https://mc.alger.fun/"}, timeout=10, verify=False)
        data = resp.json()
        result = data.get("result", {})
        songs = result.get("songs", [])
        if songs:
            # 找第一个匹配的
            for s in songs:
                # 检查歌手是否匹配
                ars = s.get("ar", []) or s.get("artists", [])
                ar_names = [a.get("name", "") for a in ars if a.get("name")]
                ar_str = " ".join(ar_names)
                # 宽松匹配：歌手名包含或被包含
                if artist.lower() in ar_str.lower() or ar_str.lower() in artist.lower():
                    album = s.get("al", {}) or s.get("album", {})
                    pic_url = album.get("picUrl", "")
                    if pic_url:
                        return pic_url
            # 没有精确匹配，用第一个结果
            album = songs[0].get("al", {}) or songs[0].get("album", {})
            pic_url = album.get("picUrl", "")
            if pic_url:
                return pic_url
    except Exception:
        pass
    return ""


def is_no_cover(song):
    """判断歌曲是否没有封面"""
    cu = song.cover_url or ""
    lc = song.local_cover or ""
    default = "/static/img/default-cover.svg"
    return (not cu or cu == default) and (not lc or lc == default)


def fill_all_covers():
    """三阶段封面补充"""
    with app.app_context():
        # ===== 阶段1：网易云批量API =====
        no_cover_songs = [s for s in Song.query.filter(
            Song.platform == "wangyi",
            Song.platform_id != None,
            Song.platform_id != ""
        ).all() if is_no_cover(s)]
        
        logger.info(f"=== 阶段1：网易云批量API，需补充封面: {len(no_cover_songs)} 首 ===")
        
        if no_cover_songs:
            batch_size = 500
            total_covers = 0
            
            for batch_start in range(0, len(no_cover_songs), batch_size):
                batch = no_cover_songs[batch_start:batch_start + batch_size]
                pids = [s.platform_id for s in batch if s.platform_id]
                
                if not pids:
                    continue
                
                details = batch_get_song_details(pids)
                
                for song in batch:
                    pid = str(song.platform_id)
                    if pid in details and details[pid]["cover_url"]:
                        cover_url = details[pid]["cover_url"]
                        local_cover = download_cover(cover_url, song.name, song.artist)
                        if local_cover:
                            song.cover_url = local_cover
                            song.local_cover = local_cover
                            total_covers += 1
                        else:
                            song.cover_url = cover_url
                            total_covers += 1
                
                db.session.commit()
                logger.info(f"  阶段1进度: {min(batch_start + batch_size, len(no_cover_songs))}/{len(no_cover_songs)}, 已获取 {total_covers}")
                time.sleep(0.5)
            
            logger.info(f"阶段1完成: 获取 {total_covers} 张封面")
        
        # ===== 阶段2：algermusic歌曲详情API（逐首） =====
        still_no_cover = [s for s in Song.query.filter(
            Song.platform_id != None,
            Song.platform_id != ""
        ).all() if is_no_cover(s)]
        
        logger.info(f"\n=== 阶段2：algermusic详情API，剩余无封面: {len(still_no_cover)} 首 ===")
        
        phase2_covers = 0
        for idx, song in enumerate(still_no_cover):
            cover_url = alger_song_detail(song.platform_id)
            if cover_url:
                local_cover = download_cover(cover_url, song.name, song.artist)
                if local_cover:
                    song.cover_url = local_cover
                    song.local_cover = local_cover
                else:
                    song.cover_url = cover_url
                phase2_covers += 1
            
            if (idx + 1) % 100 == 0:
                db.session.commit()
                logger.info(f"  阶段2进度: {idx+1}/{len(still_no_cover)}, 已获取 {phase2_covers}")
            
            time.sleep(0.15)
        
        db.session.commit()
        logger.info(f"阶段2完成: 获取 {phase2_covers} 张封面")
        
        # ===== 阶段3：algermusic搜索API（按歌名+歌手） =====
        still_no_cover2 = [s for s in Song.query.all() if is_no_cover(s)]
        
        logger.info(f"\n=== 阶段3：algermusic搜索API，剩余无封面: {len(still_no_cover2)} 首 ===")
        
        phase3_covers = 0
        for idx, song in enumerate(still_no_cover2):
            cover_url = alger_search_cover(song.name, song.artist)
            if cover_url:
                local_cover = download_cover(cover_url, song.name, song.artist)
                if local_cover:
                    song.cover_url = local_cover
                    song.local_cover = local_cover
                else:
                    song.cover_url = cover_url
                phase3_covers += 1
            
            if (idx + 1) % 50 == 0:
                db.session.commit()
                logger.info(f"  阶段3进度: {idx+1}/{len(still_no_cover2)}, 已获取 {phase3_covers}")
            
            time.sleep(0.3)  # 搜索API更慢，间隔长一点
        
        db.session.commit()
        logger.info(f"阶段3完成: 获取 {phase3_covers} 张封面")
        
        # ===== 阶段4：删除所有仍无封面的歌曲 =====
        still_no_cover_final = [s for s in Song.query.all() if is_no_cover(s)]
        
        logger.info(f"\n=== 阶段4：清理无封面歌曲，共 {len(still_no_cover_final)} 首 ===")
        
        if still_no_cover_final:
            # 先删除关联数据
            from models.db import Favorite, Comment, PlaylistSong
            
            song_ids = [s.id for s in still_no_cover_final]
            
            # 批量删除关联
            for i in range(0, len(song_ids), 500):
                batch_ids = song_ids[i:i+500]
                Favorite.query.filter(Favorite.song_id.in_(batch_ids)).delete(synchronize_session=False)
                Comment.query.filter(Comment.song_id.in_(batch_ids)).delete(synchronize_session=False)
                PlaylistSong.query.filter(PlaylistSong.song_id.in_(batch_ids)).delete(synchronize_session=False)
                db.session.commit()
            
            # 删除歌曲
            for song in still_no_cover_final:
                db.session.delete(song)
            
            db.session.commit()
            logger.info(f"已删除 {len(still_no_cover_final)} 首无封面歌曲")
        
        # ===== 最终统计 =====
        final_total = Song.query.count()
        with_cover = Song.query.filter(
            (Song.cover_url != None) & (Song.cover_url != '') & (Song.cover_url != '/static/img/default-cover.svg')
        ).count()
        with_lyric = Song.query.filter((Song.lyric != None) & (Song.lyric != '')).count()
        
        logger.info(f"\n========== 最终统计 ==========")
        logger.info(f"  总歌曲: {final_total}")
        logger.info(f"  有封面: {with_cover} ({with_cover*100//max(final_total,1)}%)")
        logger.info(f"  有歌词: {with_lyric} ({with_lyric*100//max(final_total,1)}%)")
        logger.info(f"  无封面: {final_total - with_cover}")


if __name__ == "__main__":
    fill_all_covers()
