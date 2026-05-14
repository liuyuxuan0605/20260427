# -*- coding: utf-8 -*-
"""
音乐数据批量导入与修复脚本
功能：
1. 通过QQ音乐/网易云搜索API导入更多热门歌曲（含封面、专辑信息）
2. 为现有歌曲补充封面URL和专辑信息
3. 批量预缓存播放链接
4. 下载缓存封面图片到本地
"""
import sys
import os

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import time
import hashlib
import logging
import requests
import warnings
import re

warnings.filterwarnings("ignore")
requests.packages.urllib3.disable_warnings()

# 设置路径
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============ HTTP 配置 ============
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
session = requests.Session()
session.headers.update(HEADERS)
session.verify = False


# ============ QQ音乐搜索（最强数据源） ============

def qq_search(keyword, page=1, num=20):
    """QQ音乐搜索 - 返回含封面的歌曲列表"""
    try:
        url = f"https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w={keyword}&format=json&p={page}&n={num}"
        resp = session.get(url, headers={"Referer": "https://y.qq.com"}, timeout=15)
        data = resp.json()
        songs = []
        for item in data.get("data", {}).get("song", {}).get("list", []):
            singers = [s.get("name", "") for s in item.get("singer", [])]
            albummid = item.get("albummid", "")
            cover_url = f"https://y.qq.com/music/photo_new/T002R300x300M000{albummid}.jpg" if albummid else ""
            songs.append({
                "name": item.get("songname", "").strip(),
                "artist": " / ".join(singers).strip(),
                "album": item.get("albumname", "").strip(),
                "cover_url": cover_url,
                "platform": "qq",
                "platform_id": item.get("songmid", ""),
                "hot_score": 0,
            })
        return songs
    except Exception as e:
        logger.debug(f"QQ搜索失败 [{keyword}]: {e}")
        return []


# ============ 网易云搜索 ============

def netease_search(keyword, limit=20):
    """网易云搜索（修复歌手名解析：兼容 ar/artists 两种字段格式）"""
    try:
        url = f"https://music.163.com/api/search/get?s={keyword}&type=1&offset=0&limit={limit}"
        resp = session.get(url, headers={"Referer": "https://music.163.com"}, timeout=15)
        data = resp.json()
        songs = []
        for item in data.get("result", {}).get("songs", []):
            # 修复：优先 ar 字段，回退 artists 字段
            ar_list = item.get("ar", [])
            if ar_list and isinstance(ar_list, list) and len(ar_list) > 0:
                artist_names = [a.get("name", "") for a in ar_list if a.get("name")]
                artists = " / ".join(artist_names) if artist_names else ""
            else:
                artists_list = item.get("artists", [])
                if artists_list and isinstance(artists_list, list) and len(artists_list) > 0:
                    artist_names = [a.get("name", "") for a in artists_list if a.get("name")]
                    artists = " / ".join(artist_names) if artist_names else ""
                else:
                    artists = ""

            album = item.get("al", {})
            pic_url = album.get("picUrl", "")
            songs.append({
                "name": item.get("name", "").strip(),
                "artist": artists.strip(),
                "album": album.get("name", "").strip(),
                "cover_url": pic_url,
                "platform": "wangyi",
                "platform_id": str(item.get("id", "")),
                "hot_score": 0,
            })
        return songs
    except Exception as e:
        logger.debug(f"网易云搜索失败 [{keyword}]: {e}")
        return []


# ============ 酷我搜索（补充封面和播放rid） ============

def kuwo_search(keyword, pn=1, rn=20):
    """酷我搜索"""
    try:
        from urllib.parse import quote
        url = f"https://www.kuwo.cn/api/www/search/searchMusicBykeyWord?key={quote(keyword)}&pn={pn}&rn={rn}"
        resp = session.get(url, headers={"Referer": "https://www.kuwo.cn", "csrf": "4X1D5B3E5F"}, timeout=15)
        data = resp.json()
        songs = []
        for item in data.get("data", {}).get("list", []):
            pic = item.get("pic", "")
            if pic and not pic.startswith("http"):
                pic = ""
            songs.append({
                "name": item.get("name", "").strip(),
                "artist": item.get("artist", "").strip(),
                "album": item.get("album", "").strip(),
                "cover_url": pic,
                "platform": "kuwo",
                "platform_id": str(item.get("rid", "")),
                "hot_score": 0,
            })
        return songs
    except Exception as e:
        logger.debug(f"酷我搜索失败 [{keyword}]: {e}")
        return []


# ============ 歌曲去重与匹配 ============

def normalize_name(name):
    """标准化歌名用于匹配"""
    # 移除括号内容、空格、特殊字符
    n = re.sub(r'[（(].*?[）)]', '', name)
    n = re.sub(r'[\s\-_\\/@&]', '', n)
    return n.lower().strip()


def is_same_song(existing_name, existing_artist, new_name, new_artist):
    """判断是否是同一首歌（模糊匹配）"""
    en = normalize_name(existing_name)
    nn = normalize_name(new_name)
    if en == nn:
        return True
    if en in nn or nn in en:
        # 歌名包含关系，再检查歌手
        ea = existing_artist.lower().replace(" ", "")
        na = new_artist.lower().replace(" ", "")
        if ea and na and (ea in na or na in ea):
            return True
    return False


# ============ 核心逻辑 ============

# 要搜索的热门歌手/关键词列表（覆盖多种风格）
SEARCH_KEYWORDS = [
    # 华语流行
    "周杰伦", "林俊杰", "薛之谦", "陈奕迅", "邓紫棋",
    "毛不易", "华晨宇", "李荣浩", "许嵩", "王力宏",
    "张学友", "刘德华", "王菲", "孙燕姿", "蔡依林",
    "五月天", "BEYOND", "朴树", "赵雷", "陈粒",
    "汪苏泷", "徐良", "周深", "张杰", "张韶涵",
    "陶喆", "方大同", "萧敬腾", "吴青峰", "李健",
    # 华语女声
    "田馥甄", "梁静茹", "刘若英", "张惠妹", "莫文蔚",
    "那英", "李宇春", "周笔畅", "张靓颖", "谭维维",
    # 说唱/独立
    "GAI", "法老", "马思唯", "万妮达", "陈绮贞",
    # 欧美
    "Taylor Swift", "Ed Sheeran", "Adele", "Bruno Mars",
    "Maroon 5", "Coldplay", "Imagine Dragons", "Billie Eilish",
    "Justin Bieber", "The Weeknd", "Dua Lipa",
    # 韩语
    "BTS", "BLACKPINK", "IU", "EXO",
    # 经典老歌
    "罗大佑", "李宗盛", "齐秦", "周华健", "任贤齐",
    # 国风/古风
    "双笙", "银临", "排骨教主", "音频怪物", "河图",
    # 网络热歌
    "是晚星呀", "白小白", "宝石Gem",
]

# 榜单搜索关键词
CHART_KEYWORDS = [
    "热歌榜", "新歌榜", "飙升榜", "经典榜", "抖音热歌",
    "华语金曲", "欧美热歌", "网络热歌", "古风热歌",
]


def enrich_database(app):
    """核心：扩充数据库歌曲"""
    from models.db import db, Song

    with app.app_context():
        before_count = Song.query.count()
        logger.info(f"当前歌曲数: {before_count}")

        total_added = 0
        total_enriched = 0
        processed_keywords = 0

        for keyword in SEARCH_KEYWORDS:
            processed_keywords += 1
            logger.info(f"[{processed_keywords}/{len(SEARCH_KEYWORDS)}] 搜索: {keyword}")

            # 从QQ音乐搜索（最强数据源）
            qq_songs = qq_search(keyword, page=1, num=30)
            logger.info(f"  QQ: {len(qq_songs)} 首")

            # 补充网易云搜索
            wy_songs = netease_search(keyword, limit=20)
            logger.info(f"  网易云: {len(wy_songs)} 首")

            # 合并去重
            all_songs = qq_songs + wy_songs

            for song_data in all_songs:
                name = song_data["name"]
                artist = song_data["artist"]
                if not name or not artist:
                    continue

                # 检查是否已存在（精确匹配）
                existing = Song.query.filter_by(name=name, artist=artist).first()

                if existing:
                    # 补充封面和专辑
                    enriched = False
                    if song_data.get("cover_url") and not existing.cover_url:
                        existing.cover_url = song_data["cover_url"]
                        enriched = True
                    if song_data.get("album") and not existing.album:
                        existing.album = song_data["album"]
                        enriched = True
                    if song_data.get("platform_id") and not existing.platform_id:
                        existing.platform_id = song_data["platform_id"]
                        enriched = True
                    if enriched:
                        total_enriched += 1
                    continue

                # 新增歌曲
                song = Song(
                    name=name,
                    artist=artist,
                    album=song_data.get("album", ""),
                    cover_url=song_data.get("cover_url", ""),
                    platform=song_data.get("platform", ""),
                    platform_id=song_data.get("platform_id", ""),
                    hot_score=song_data.get("hot_score", 0),
                    genre="",
                )
                db.session.add(song)
                total_added += 1

            # 每10个关键词提交一次
            if processed_keywords % 10 == 0:
                db.session.commit()
                logger.info(f"  >> 已提交, 累计新增 {total_added}, 补充 {total_enriched}")

            # 礼貌延迟
            time.sleep(0.5)

        # 最终提交
        db.session.commit()
        after_count = Song.query.count()
        logger.info(f"=== 扩充完成 ===")
        logger.info(f"新增: {total_added}, 补充信息: {total_enriched}")
        logger.info(f"歌曲总数: {before_count} -> {after_count}")

        return total_added, total_enriched


def enrich_missing_covers(app):
    """为缺少封面的歌曲通过搜索补充封面URL"""
    from models.db import db, Song

    with app.app_context():
        # 找出没有封面的歌曲
        no_cover_songs = Song.query.filter(
            (Song.cover_url == None) | (Song.cover_url == "")
        ).all()
        logger.info(f"缺少封面的歌曲: {len(no_cover_songs)} 首")

        enriched = 0
        for i, song in enumerate(no_cover_songs):
            keyword = f"{song.artist} {song.name}"
            logger.info(f"[{i+1}/{len(no_cover_songs)}] 搜索封面: {keyword}")

            # 用QQ音乐搜索封面
            results = qq_search(keyword, page=1, num=5)
            if results:
                for r in results:
                    if r["cover_url"] and is_same_song(song.name, song.artist, r["name"], r["artist"]):
                        song.cover_url = r["cover_url"]
                        if r.get("album") and not song.album:
                            song.album = r["album"]
                        if r.get("platform_id") and not song.platform_id:
                            song.platform_id = r["platform_id"]
                        enriched += 1
                        break

            if (i + 1) % 20 == 0:
                db.session.commit()
                logger.info(f"  >> 已提交, 补充封面 {enriched}")

            time.sleep(0.3)

        db.session.commit()
        logger.info(f"=== 封面补充完成: {enriched}/{len(no_cover_songs)} ===")
        return enriched


def cache_all_covers(app):
    """下载缓存所有封面图片到本地"""
    from models.db import db, Song
    from config import COVERS_DIR

    os.makedirs(COVERS_DIR, exist_ok=True)

    with app.app_context():
        # 找出有远程封面但没本地缓存的歌曲
        songs = Song.query.filter(
            Song.cover_url != None,
            Song.cover_url != "",
            Song.cover_url.startswith("http")
        ).all()
        logger.info(f"需要缓存封面的歌曲: {len(songs)} 首")

        cached = 0
        failed = 0
        for i, song in enumerate(songs):
            url = song.cover_url
            if not url or not url.startswith("http"):
                continue

            try:
                # 生成本地文件名
                ext = ".jpg"
                if ".png" in url:
                    ext = ".png"
                elif ".webp" in url:
                    ext = ".webp"
                filename = hashlib.md5(f"{song.name}_{song.artist}".encode("utf-8")).hexdigest() + ext
                local_path = os.path.join(COVERS_DIR, filename)

                if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
                    song.local_cover = f"/data/covers/{filename}"
                    cached += 1
                    continue

                # 下载
                resp = session.get(url, timeout=15, verify=False)
                if resp.status_code == 200 and len(resp.content) > 1024:
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    song.local_cover = f"/data/covers/{filename}"
                    cached += 1
                else:
                    failed += 1
                    logger.debug(f"下载失败 [{song.name}]: HTTP {resp.status_code}, size={len(resp.content)}")
            except Exception as e:
                failed += 1
                logger.debug(f"下载异常 [{song.name}]: {e}")

            if (i + 1) % 50 == 0:
                db.session.commit()
                logger.info(f"  >> 进度 {i+1}/{len(songs)}, 缓存 {cached}, 失败 {failed}")

            time.sleep(0.1)

        db.session.commit()
        logger.info(f"=== 封面缓存完成: {cached} 成功, {failed} 失败 ===")
        return cached


def pre_cache_play_urls(app, limit=200):
    """为最热门的歌曲预缓存播放链接"""
    from models.db import db, Song
    from crawler.music_crawler import PlayUrlFetcher

    with app.app_context():
        # 优先处理没有播放链接的热门歌曲
        songs = Song.query.filter(
            (Song.play_url == None) | (Song.play_url == "")
        ).order_by(Song.hot_score.desc()).limit(limit).all()

        logger.info(f"需要预缓存播放链接: {len(songs)} 首")

        fetcher = PlayUrlFetcher()
        cached = 0
        failed = 0

        for i, song in enumerate(songs):
            logger.info(f"[{i+1}/{len(songs)}] 搜索: {song.artist} - {song.name}")
            try:
                url = fetcher.fetch_play_url(song.name, song.artist)
                if url:
                    song.play_url = url
                    cached += 1
                    logger.info(f"  OK: {url[:60]}...")
                else:
                    failed += 1
                    logger.info(f"  FAIL: 无法获取")
            except Exception as e:
                failed += 1
                logger.debug(f"  异常: {e}")

            if (i + 1) % 10 == 0:
                db.session.commit()
                logger.info(f"  >> 进度 {i+1}/{len(songs)}, 成功 {cached}, 失败 {failed}")

            # 防止过快
            time.sleep(1)

        db.session.commit()
        logger.info(f"=== 播放链接缓存完成: {cached} 成功, {failed} 失败 ===")
        return cached


# ============ 主函数 ============

def main():
    from app import create_app

    app = create_app()

    print("\n" + "=" * 60)
    print("  音乐数据批量导入与修复工具")
    print("=" * 60)

    # Step 1: 扩充歌曲库
    print("\n[Step 1/4] 扩充歌曲库...")
    added, enriched = enrich_database(app)

    # Step 2: 补充缺失封面URL
    print("\n[Step 2/4] 补充缺失封面URL...")
    cover_enriched = enrich_missing_covers(app)

    # Step 3: 下载缓存封面图片
    print("\n[Step 3/4] 下载缓存封面图片...")
    covers_cached = cache_all_covers(app)

    # Step 4: 预缓存播放链接
    print("\n[Step 4/4] 预缓存播放链接（前200首热门歌曲）...")
    play_cached = pre_cache_play_urls(app, limit=200)

    # 最终统计
    from models.db import Song
    with app.app_context():
        total = Song.query.count()
        with_cover = Song.query.filter(Song.cover_url != None, Song.cover_url != "").count()
        with_local = Song.query.filter(Song.local_cover != None, Song.local_cover != "").count()
        with_play = Song.query.filter(Song.play_url != None, Song.play_url != "").count()

    print("\n" + "=" * 60)
    print("  最终统计")
    print("=" * 60)
    print(f"  歌曲总数:  {total}")
    print(f"  有封面URL: {with_cover}")
    print(f"  本地缓存:  {with_local}")
    print(f"  有播放链接: {with_play}")
    print("=" * 60)


if __name__ == "__main__":
    main()
