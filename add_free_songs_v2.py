# -*- coding: utf-8 -*-
"""
扩充免费歌曲（v2版本）
核心原则：
1. 只添加网易云免费歌曲：fee=0（免费）+ fee=8（免费/可播放）= 完整版
2. 跳过 fee=1（VIP）= 只有30秒试听版
3. 用网易云直链验证播放链接有效（HEAD检查 + 文件大小>800KB）
4. 保存 platform_id，播放时直查100%准确
5. 不删除现有歌曲，只做增量添加
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

# 项目路径
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


# ============ 歌手列表 ============

ARTISTS = [
    # 华语男歌手
    "周杰伦", "林俊杰", "陈奕迅", "薛之谦", "王力宏",
    "李荣浩", "张杰", "华晨宇", "毛不易", "许嵩",
    "汪苏泷", "赵雷", "李健", "周深", "朴树",
    "任贤齐", "张学友", "刘德华", "陶喆", "方大同",
    "萧敬腾", "胡彦斌", "张敬轩", "李圣杰", "光良",
    "吴青峰", "林宥嘉", "杨宗纬", "徐佳莹", "萧煌奇",
    # 华语女歌手
    "孙燕姿", "邓紫棋", "王菲", "梁静茹", "刘若英",
    "张韶涵", "蔡依林", "莫文蔚", "田馥甄", "陈粒",
    "张靓颖", "李宇春", "袁娅维", "谭维维", "杨千嬅",
    "容祖儿", "谢安琪", "A-Lin", "戴佩妮", "郁可唯",
    "张惠妹", "范玮琪", "丁当", "郭静", "任然",
    # 乐队/组合
    "五月天", "BEYOND", "苏打绿", "逃跑计划", "好妹妹",
    "房东的猫", "陈绮贞", "飞儿乐队",
    # 经典老歌手
    "罗大佑", "李宗盛", "崔健", "齐秦", "周华健",
    "张信哲", "伍佰", "张雨生",
    # 独立/民谣
    "花粥", "陈鸿宇", "宋冬野", "马頔", "程响",
    "阿冗", "王贰浪", "尧十三",
    # 说唱/流行
    "凤凰传奇", "筷子兄弟",
    # 国际
    "Taylor Swift", "Ed Sheeran", "Adele", "Bruno Mars",
    "Coldplay", "Maroon 5", "Imagine Dragons", "OneRepublic",
    "Sam Smith", "Dua Lipa",
    # 日韩
    "YOASOBI", "米津玄師", "RADWIMPS", "LiSA",
    # 纯音乐
    "久石让", "坂本龍一", "Yiruma",
]


# ============ 工具函数 ============

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


def search_netease(keyword, limit=30, offset=0):
    """搜索网易云音乐，返回含fee信息的数据"""
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
    """
    验证播放链接是否有效且为完整版。
    返回 (is_valid, play_url, size_kb)
    """
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
                    if size_kb > 800:  # >800KB = 完整版
                        return True, loc, size_kb
                    else:
                        return False, None, size_kb  # 试听版
                except Exception:
                    # HEAD失败但URL存在，保守认为可用
                    return True, loc, 0
        elif resp.status_code == 200:
            # 某些情况下直接返回内容
            return True, None, 0
    except Exception as e:
        logger.debug(f"  验证失败 [{song_id}]: {e}")
    return False, None, 0


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
            headers={"User-Agent": HEADERS["User-Agent"], "Referer": "https://y.qq.com/"},
            timeout=10, verify=False
        )
        if resp.status_code == 200 and len(resp.content) > 1024:
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return f"/data/covers/{filename}"
    except Exception:
        pass
    return cover_url


# ============ 主流程 ============

def add_free_songs():
    """增量添加免费歌曲"""
    with app.app_context():
        existing_count = Song.query.count()
        logger.info(f"=== 开始扩充免费歌曲（当前 {existing_count} 首）===")

        # 获取已入库的平台ID集合
        existing_pids = set()
        for s in Song.query.filter(Song.platform_id != None, Song.platform_id != "").all():
            existing_pids.add(str(s.platform_id))
        logger.info(f"已有 platform_id 数量: {len(existing_pids)}")

        total_added = 0
        total_vip_skip = 0
        total_dup_skip = 0
        total_verify_fail = 0
        total_artists = len(ARTISTS)

        for artist_idx, artist_name in enumerate(ARTISTS):
            logger.info(f"\n[{artist_idx+1}/{total_artists}] 搜索: {artist_name}")

            # 搜索前30首
            results = search_netease(artist_name, limit=30)
            if not results:
                logger.info("  搜索无结果")
                time.sleep(0.5)
                continue

            # 再搜索下一页（offset=30），增加覆盖
            results2 = search_netease(artist_name, limit=30, offset=30)
            all_results = results + results2

            artist_added = 0
            artist_vip = 0
            artist_dup = 0
            artist_fail = 0

            for song_data in all_results:
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
                    if artist_fail <= 3:
                        size_info = f"({size_kb:.0f}KB)" if size_kb > 0 else ""
                        logger.debug(f"  验证失败{size_info}: {artist} - {name}")
                    continue

                # 缓存封面
                cover_url = song_data.get("cover_url", "")
                local_cover = cache_cover(cover_url, name, artist) if cover_url else ""

                # 入库
                song = Song(
                    name=name,
                    artist=artist or "未知",
                    album=song_data.get("album", ""),
                    cover_url=local_cover or cover_url,
                    platform="wangyi",
                    platform_id=pid,
                    hot_score=max(0, 500 - total_added),
                )
                db.session.add(song)
                existing_pids.add(pid)
                artist_added += 1
                total_added += 1

                if total_added % 20 == 0:
                    db.session.commit()
                    logger.info(f"  >> 累计入库 {total_added} 首")

                # 请求间隔
                time.sleep(0.1)

            total_vip_skip += artist_vip
            total_dup_skip += artist_dup
            total_verify_fail += artist_fail

            logger.info(
                f"  {artist_name}: +{artist_added}首, "
                f"VIP={artist_vip}, 重复={artist_dup}, 失败={artist_fail}"
            )

            # 每个歌手后提交
            db.session.commit()
            time.sleep(0.3)

        # 最终统计
        db.session.commit()
        final_count = Song.query.count()
        with_pid = Song.query.filter(Song.platform_id != None, Song.platform_id != "").count()

        logger.info("\n" + "=" * 60)
        logger.info("扩充完成！")
        logger.info(f"  原有歌曲: {existing_count}")
        logger.info(f"  新增免费: {total_added}")
        logger.info(f"  VIP跳过: {total_vip_skip}")
        logger.info(f"  重复跳过: {total_dup_skip}")
        logger.info(f"  验证失败: {total_verify_fail}")
        logger.info(f"  当前总数: {final_count}")
        logger.info(f"  有平台ID: {with_pid} ({with_pid/final_count*100:.0f}%)" if final_count > 0 else "")
        logger.info("=" * 60)


if __name__ == "__main__":
    add_free_songs()
