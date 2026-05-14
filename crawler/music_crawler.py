# -*- coding: utf-8 -*-
"""音乐数据爬虫 - 整合热点数据采集和多源音乐下载"""
import os
import sys
import io
import json
import subprocess
import logging
import hashlib
import re
import warnings
import requests as http_requests
from datetime import datetime, timezone
from urllib.parse import quote

from models.db import db, Song, UpdateLog
from config import HOT_SKILL_DIR, COVERS_DIR

logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore")

# ============ 通用 HTTP 头 ============
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


# ============ 歌名标准化工具 ============

def normalize_name(name):
    """标准化歌名用于模糊匹配"""
    n = re.sub(r'[（(].*?[）)]', '', name)
    n = re.sub(r'[\s\-_\\/@&·]', '', n)
    n = n.replace('〜', '~').replace('～', '~')
    return n.lower().strip()


# ============ 热点数据采集（爬取排行榜） ============

def run_crawl_music(platform="all", chart_type="all"):
    """运行热点数据采集的 crawl-music.js 获取音乐排行榜"""
    script_path = os.path.join(HOT_SKILL_DIR, "scripts", "crawl-music.js")
    if not os.path.exists(script_path):
        logger.error(f"爬虫脚本不存在: {script_path}")
        return None

    cmd = ["node", script_path]
    if platform != "all":
        cmd.append(f"--platform={platform}")
    if chart_type != "all":
        cmd.append(f"--type={chart_type}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            cwd=HOT_SKILL_DIR, encoding="utf-8"
        )
        if result.returncode != 0:
            logger.error(f"爬虫执行失败: {result.stderr}")
            return None
        data = json.loads(result.stdout)
        return data
    except subprocess.TimeoutExpired:
        logger.error("爬虫执行超时")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"爬虫结果解析失败: {e}")
        return None
    except Exception as e:
        logger.error(f"爬虫执行异常: {e}")
        return None


# ============ 排行榜数据解析 ============

def parse_and_store_music(data):
    """解析爬虫数据并存入数据库"""
    if not data or data.get("status") != "ok":
        return 0, 0

    results = data.get("results", {})
    total_added = 0
    total_updated = 0

    platform_map = {
        "qq": "qq", "wangyi": "wangyi", "kugou": "kugou", "kuwo": "kuwo",
    }

    for platform_key, platform_data in results.items():
        platform_name = platform_map.get(platform_key, platform_key)

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
                song_name = item.get("name", "").strip()
                artist = item.get("geshou", "").strip()
                hot_value = item.get("hot", 0)
                cover_img = item.get("img", "")

                if not song_name:
                    continue

                try:
                    hot_value = int(hot_value) if hot_value else 0
                except (ValueError, TypeError):
                    hot_value = 0

                existing = Song.query.filter_by(name=song_name, artist=artist).first()
                if existing:
                    new_score = hot_value or (idx + 1)
                    if new_score > existing.hot_score:
                        existing.hot_score = new_score
                        existing.updated_at = datetime.now(timezone.utc)
                        total_updated += 1
                    continue

                song = Song(
                    name=song_name, artist=artist, platform=platform_name,
                    hot_score=hot_value or (idx + 1), cover_url=cover_img,
                )
                db.session.add(song)
                total_added += 1

    if total_added > 0 or total_updated > 0:
        db.session.commit()

    return total_added, total_updated


# ============ 封面缓存 ============

def cache_cover_images(limit=200):
    """缓存封面图片到本地"""
    if not os.path.exists(COVERS_DIR):
        os.makedirs(COVERS_DIR, exist_ok=True)

    songs = Song.query.filter(
        Song.cover_url != "",
        Song.cover_url.startswith("http"),
        (Song.local_cover == None) | (Song.local_cover == "")
    ).limit(limit).all()

    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Referer": "https://y.qq.com/",
    }

    cached = 0
    for song in songs:
        try:
            url = song.cover_url
            if not url or not url.startswith("http"):
                continue

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

            resp = http_requests.get(url, headers=headers, timeout=10, verify=False)
            if resp.status_code == 200 and len(resp.content) > 1024:
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                song.local_cover = f"/data/covers/{filename}"
                cached += 1
        except Exception as e:
            logger.debug(f"缓存封面失败 [{song.name}]: {e}")
            continue

    if cached > 0:
        db.session.commit()

    return cached


# ============ 每日更新 ============

def update_all_music():
    """
    更新所有平台的音乐数据。
    核心原则：用平台API搜索获取精确的平台ID，确保歌名与音频100%对应。
    """
    logger.info("开始更新音乐数据（精确匹配模式）...")

    total_added = 0
    total_updated = 0

    # 获取排行榜种子
    hot_data = run_crawl_music(platform="all", chart_type="hot")
    rising_data = run_crawl_music(platform="all", chart_type="rising")

    # 合并所有排行榜歌曲
    seed_songs = []  # (name, artist, hot)
    seen = set()

    for data in [hot_data, rising_data]:
        if not data or data.get("status") != "ok":
            continue
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
                for idx, item in enumerate(chart_entry.get("data", {}).get("sj", [])):
                    name = item.get("name", "").strip()
                    artist = item.get("geshou", "").strip()
                    if not name:
                        continue
                    key = f"{normalize_name(name)}_{normalize_name(artist)}"
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        hot = int(item.get("hot", 0)) or (idx + 1)
                    except (ValueError, TypeError):
                        hot = idx + 1
                    seed_songs.append((name, artist, hot))

    logger.info(f"从排行榜获取 {len(seed_songs)} 首种子歌曲")

    # 用平台API精确搜索每首歌
    fetcher = get_fetcher()
    for idx, (name, artist, hot) in enumerate(seed_songs):
        # 检查是否已存在
        existing = Song.query.filter_by(name=name, artist=artist).first()
        if existing:
            if hot > existing.hot_score:
                existing.hot_score = hot
                total_updated += 1
            continue

        # 用API搜索精确匹配
        match = _enrich_song_from_api(name, artist, hot)
        if match:
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
            if total_added % 20 == 0:
                db.session.commit()
                logger.info(f"  已入库 {total_added} 首")

    # 缓存封面
    cached = cache_cover_images()

    if total_added > 0 or total_updated > 0:
        db.session.commit()

    log = UpdateLog(
        source="crawl-music-v2", status="success",
        songs_added=total_added, songs_updated=total_updated,
        message=f"精确匹配模式: 新增{total_added}首, 更新{total_updated}首, 缓存封面{cached}张"
    )
    db.session.add(log)
    db.session.commit()

    logger.info(f"更新完成: 新增{total_added}首, 更新{total_updated}首, 缓存封面{cached}张")
    return total_added, total_updated


def _enrich_song_from_api(song_name, artist, hot_rank=0):
    """
    用平台API搜索歌曲，获取精确的平台ID和元数据。
    优先级：网易云 → 酷我 → 酷狗
    """
    keyword = f"{artist} {song_name}" if artist else song_name
    keyword_only_name = song_name

    search_funcs = [
        ("wangyi", search_netease_for_enrich),
        ("kuwo", search_kuwo_for_enrich),
        ("kugou", search_kugou_for_enrich),
    ]

    fetcher = get_fetcher()

    for source_name, search_fn in search_funcs:
        for kw in [keyword, keyword_only_name]:
            try:
                results = search_fn(kw, limit=20)
                if not results:
                    continue

                for result in results[:10]:
                    r_name = result.get("name", "")
                    r_artist = result.get("artist", "")

                    name_sim = fetcher.name_similarity(song_name, r_name)
                    art_sim = fetcher.artist_similarity(artist, r_artist)

                    if name_sim < 0.6 or art_sim < 0.3:
                        continue

                    score = name_sim * 0.5 + art_sim * 0.5
                    if score >= 0.5:
                        result["hot_score"] = hot_rank
                        result["platform"] = source_name
                        return result

            except Exception:
                continue

    return None


def search_netease_for_enrich(keyword, limit=20):
    """搜索网易云用于enrich（返回含平台ID的数据）"""
    try:
        url = f"https://music.163.com/api/search/get?s={quote(keyword)}&type=1&offset=0&limit={limit}"
        resp = http_requests.get(url, headers={**HEADERS, "Referer": "https://music.163.com"}, timeout=15, verify=False)
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
    except Exception:
        return []


def search_kuwo_for_enrich(keyword, limit=20):
    """搜索酷我用于enrich"""
    try:
        url = f"https://www.kuwo.cn/api/www/search/searchMusicBykeyWord?key={quote(keyword)}&pn=1&rn={limit}"
        resp = http_requests.get(url, headers={**HEADERS, "Referer": "https://www.kuwo.cn", "csrf": "4X1D5B3E5F"}, timeout=15, verify=False)
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
    except Exception:
        return []


def search_kugou_for_enrich(keyword, limit=20):
    """搜索酷狗用于enrich"""
    try:
        url = f"https://complexsearch.kugou.com/v2/search/song?keyword={quote(keyword)}&page=1&pagesize={limit}"
        resp = http_requests.get(url, headers=HEADERS, timeout=15, verify=False)
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
    except Exception:
        return []


# ============ 播放链接搜索（核心：确保歌名+歌手都正确） ============

def _parse_netease_artists(item):
    """解析网易云歌曲的歌手名，兼容 ar/artists 两种字段格式"""
    # 优先 ar 字段（标准API格式）
    ar_list = item.get("ar", [])
    if ar_list and isinstance(ar_list, list) and len(ar_list) > 0:
        names = [a.get("name", "") for a in ar_list if a.get("name")]
        if names:
            return " / ".join(names)
    # 回退 artists 字段（部分API响应使用此字段）
    artists_list = item.get("artists", [])
    if artists_list and isinstance(artists_list, list) and len(artists_list) > 0:
        names = [a.get("name", "") for a in artists_list if a.get("name")]
        if names:
            return " / ".join(names)
    return ""


class PlayUrlFetcher:
    """
    独立的播放链接获取器。
    核心原则：歌名和歌手都必须匹配，才能确认是同一首歌。
    使用综合评分系统：歌名相似度 * 0.5 + 歌手相似度 * 0.5 >= 0.5 才接受。
    """

    def __init__(self):
        self.session = http_requests.Session()
        self.session.headers.update(HEADERS)
        self.session.verify = False

    # ---------- 标准化与匹配工具 ----------

    @staticmethod
    def clean_keyword(song_name, artist=""):
        """清理搜索关键词"""
        # 移除括号内容 (Live), (Remix), (feat. xxx) 等
        clean_name = re.sub(r'[（(].*?[）)]', '', song_name).strip()
        # 移除 Prod. by, feat., Ft. 等
        clean_name = re.sub(r'(?i)(prod\.?\s*by|feat\.?|ft\.?).*$', '', clean_name).strip()
        # 移除日文波浪号
        clean_name = clean_name.replace('〜', '~').replace('～', '~')
        # 移除多余空格
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()

        clean_artist = artist.strip()
        clean_artist = re.sub(r'[（(].*?[）)]', '', clean_artist).strip()

        return clean_name, clean_artist

    @staticmethod
    def name_similarity(name1, name2):
        """计算两个歌名的相似度（0-1）"""
        n1 = normalize_name(name1)
        n2 = normalize_name(name2)
        if n1 == n2:
            return 1.0
        if n1 in n2 or n2 in n1:
            shorter = min(len(n1), len(n2))
            longer = max(len(n1), len(n2))
            return shorter / longer if longer > 0 else 0.0
        return 0.0

    @staticmethod
    def artist_similarity(target_artist, result_artist):
        """
        计算歌手匹配度（0-1）。
        target_artist: DB中的歌手名
        result_artist: 搜索结果中的歌手名
        支持 "徐良/汪苏泷" 这样的多歌手匹配
        策略：尝试完全匹配、子串匹配、多歌手分割匹配，取最高分
        """
        if not target_artist or not result_artist:
            return 0.0

        # 标准化：去掉空格和分隔符
        t = normalize_name(target_artist)
        r = normalize_name(result_artist)

        # 完全匹配
        if t == r:
            return 1.0

        best_score = 0.0

        # 策略1: 子串匹配（如 "周杰伦" 在 "周杰伦." 里）
        if t in r or r in t:
            shorter = min(len(t), len(r))
            longer = max(len(t), len(r))
            best_score = max(best_score, shorter / longer if longer > 0 else 0.0)

        # 策略2: 多歌手分割匹配（更精准）
        # "Sazablue / 林俊杰." → ["Sazablue", "林俊杰"]
        t_parts = re.split(r'[/、,，&]', target_artist)
        r_parts = re.split(r'[/、,，&]', result_artist)

        t_parts_norm = [normalize_name(p) for p in t_parts if normalize_name(p)]
        r_parts_norm = [normalize_name(p) for p in r_parts if normalize_name(p)]

        if t_parts_norm and r_parts_norm:
            # 计算：target中的每个歌手是否在result的歌手列表中
            matched = 0
            for tp in t_parts_norm:
                for rp in r_parts_norm:
                    if tp == rp or tp in rp or rp in tp:
                        matched += 1
                        break
            multi_score = matched / len(t_parts_norm)
            best_score = max(best_score, multi_score)

        return best_score

    def match_score(self, target_name, target_artist, result_name, result_artist):
        """
        综合匹配评分（0-1）。
        歌名相似度权重 0.5，歌手相似度权重 0.5。
        只有歌名和歌手都匹配才算正确。
        """
        name_sim = self.name_similarity(target_name, result_name)
        artist_sim = self.artist_similarity(target_artist, result_artist)
        return name_sim * 0.5 + artist_sim * 0.5, name_sim, artist_sim

    # ---------- 搜索关键词策略 ----------

    def get_search_keywords(self, song_name, artist=""):
        """生成搜索关键词变体（仅一个最精准的关键词）"""
        clean_name, clean_artist = self.clean_keyword(song_name, artist)
        keywords = []

        # 策略1: 歌手 + 歌名（最精准，优先使用）
        if clean_artist and clean_name:
            keywords.append(f"{clean_artist} {clean_name}")
        # 策略2: 仅歌名
        if clean_name:
            keywords.append(clean_name)

        return keywords

    # ---------- 音源1: 网易云音乐 ----------

    def search_netease(self, keyword):
        """搜索网易云音乐（修复歌手名解析）"""
        try:
            url = f"https://music.163.com/api/search/get?s={quote(keyword)}&type=1&offset=0&limit=15"
            resp = self.session.get(url, headers={"Referer": "https://music.163.com"}, timeout=10)
            data = resp.json()
            songs = []
            for item in data.get("result", {}).get("songs", []):
                artist_str = _parse_netease_artists(item)
                songs.append({
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "artist": artist_str,
                    "source": "netease"
                })
            return songs
        except Exception as e:
            logger.debug(f"网易云搜索失败: {e}")
            return []

    def get_url_netease(self, song_id):
        """获取网易云播放链接"""
        try:
            song_id = str(song_id)
            # 方法1: 直链重定向
            url = f"https://music.163.com/song/media/outer/url?id={song_id}"
            resp = self.session.get(url, timeout=10, allow_redirects=False)
            if resp.status_code == 302:
                loc = resp.headers.get("Location", "")
                if loc and "music.126.net" in loc:
                    return loc
            # 方法2: API
            url = f"https://music.163.com/api/song/enhance/player/url?ids=[{song_id}]&br=320000"
            resp = self.session.get(url, headers={"Referer": "https://music.163.com"}, timeout=10)
            data = resp.json()
            play_url = data.get("data", [{}])[0].get("url", "")
            if play_url and play_url.startswith("http"):
                return play_url
        except Exception as e:
            logger.debug(f"网易云获取链接失败: {e}")
        return None

    # ---------- 音源2: 酷我 ----------

    def search_kuwo(self, keyword):
        """搜索酷我音乐"""
        try:
            url = f"https://www.kuwo.cn/api/www/search/searchMusicBykeyWord?key={quote(keyword)}&pn=1&rn=15"
            resp = self.session.get(url, headers={"Referer": "https://www.kuwo.cn", "csrf": "4X1D5B3E5F"}, timeout=10)
            data = resp.json()
            songs = []
            for item in data.get("data", {}).get("list", []):
                songs.append({
                    "rid": item.get("rid", ""),
                    "name": item.get("name", ""),
                    "artist": item.get("artist", ""),
                    "source": "kuwo"
                })
            return songs
        except Exception as e:
            logger.debug(f"酷我搜索失败: {e}")
            return []

    def get_url_kuwo(self, rid):
        """获取酷我播放链接"""
        try:
            url = f"https://www.kuwo.cn/api/v1/www/music/playInfo?mid={rid}&type=music&httpsStatus=1"
            resp = self.session.get(url, timeout=10)
            play_url = resp.json().get("data", {}).get("url", "")
            if play_url and play_url.startswith("http"):
                return play_url
        except Exception as e:
            logger.debug(f"酷我获取链接失败: {e}")
        return None

    # ---------- 音源3: 歌曲宝 ----------

    def search_gequbao(self, keyword):
        """搜索歌曲宝"""
        try:
            url = f"https://www.gequbao.com/s/{quote(keyword)}"
            resp = self.session.get(url, timeout=10)
            songs = []
            pattern = r'data-id="(\d+)"[^>]*data-name="([^"]*)"[^>]*data-singer="([^"]*)"'
            for song_id, name, singer in re.findall(pattern, resp.text)[:15]:
                songs.append({
                    "id": song_id, "name": name.strip(),
                    "artist": singer.strip(), "source": "gequbao"
                })
            return songs
        except Exception as e:
            logger.debug(f"歌曲宝搜索失败: {e}")
            return []

    def get_url_gequbao(self, song_id):
        """获取歌曲宝播放链接"""
        try:
            url = f"https://www.gequbao.com/api/song/url?id={song_id}"
            resp = self.session.get(url, timeout=10)
            play_url = resp.json().get("url", "")
            if play_url and play_url.startswith("http"):
                return play_url
        except Exception as e:
            logger.debug(f"歌曲宝获取链接失败: {e}")
        return None

    # ---------- 音源4: 酷狗 ----------

    def search_kugou(self, keyword):
        """搜索酷狗音乐"""
        try:
            url = f"https://complexsearch.kugou.com/v2/search/song?keyword={quote(keyword)}&page=1&pagesize=15"
            resp = self.session.get(url, timeout=10)
            data = resp.json()
            songs = []
            for item in data.get("data", {}).get("lists", []):
                songs.append({
                    "hash": item.get("FileHash", ""),
                    "name": item.get("SongName", ""),
                    "artist": item.get("SingerName", ""),
                    "source": "kugou"
                })
            return songs
        except Exception as e:
            logger.debug(f"酷狗搜索失败: {e}")
            return []

    def get_url_kugou(self, hash_code):
        """获取酷狗播放链接"""
        try:
            url = f"https://www.kugou.com/yy/html/singer.html?hash={hash_code}"
            resp = self.session.get(url, timeout=10)
            match = re.search(r'"play_url":"([^"]+)"', resp.text)
            if match:
                play_url = match.group(1).replace("\\/", "/")
                if play_url.startswith("http"):
                    return play_url
        except Exception as e:
            logger.debug(f"酷狗获取链接失败: {e}")
        return None

    # ---------- 统一搜索 + 获取（核心） ----------

    def fetch_play_url(self, song_name, artist="", platform="", platform_id=""):
        """
        从多个源搜索并获取播放链接。
        核心原则：宁可播放失败，也不播错歌。

        优先级：
        1. 如果有 platform + platform_id，直接用ID获取（100%准确）
        2. 否则，用 "歌手 歌名" 搜索，严格匹配歌名+歌手

        匹配规则（强制）：
        1. 歌名相似度 >= 0.5
        2. 歌手相似度 > 0（歌手必须至少部分匹配）
        3. 综合分 >= 0.6
        """
        # ========== 优先：直接用 platform_id 获取（100%准确） ==========
        if platform and platform_id:
            direct_url = self._fetch_by_platform_id(platform, platform_id)
            if direct_url:
                logger.info(f"直接通过 {platform} ID 获取播放链接: {song_name}")
                return direct_url

        # ========== 降级：搜索匹配（严格歌手+歌名验证） ==========
        logger.info(f"搜索播放链接: {artist} - {song_name}")

        keywords = self.get_search_keywords(song_name, artist)

        # 按可靠性排序的源
        search_funcs = [
            ("netease", self.search_netease, lambda s: self.get_url_netease(s["id"])),
            ("kuwo", self.search_kuwo, lambda s: self.get_url_kuwo(s["rid"])),
            ("gequbao", self.search_gequbao, lambda s: self.get_url_gequbao(s["id"])),
            ("kugou", self.search_kugou, lambda s: self.get_url_kugou(s["hash"])),
        ]

        # 收集所有候选
        candidates = []

        for keyword in keywords:
            for source_name, search_fn, get_url_fn in search_funcs:
                try:
                    results = search_fn(keyword)
                    if not results:
                        continue

                    for result in results[:10]:
                        r_name = result.get("name", "")
                        r_artist = result.get("artist", "")

                        # 计算综合匹配分
                        score, name_sim, artist_sim = self.match_score(
                            song_name, artist, r_name, r_artist
                        )

                        # === 强制条件 ===
                        # 1. 歌名必须相似（>= 0.5）
                        if name_sim < 0.5:
                            continue
                        # 2. 歌手必须至少部分匹配（> 0）——防止同名不同歌手
                        if artist_sim <= 0:
                            continue
                        # 3. 综合分必须 >= 0.6
                        if score < 0.6:
                            continue

                        candidates.append({
                            "score": score,
                            "name_sim": name_sim,
                            "artist_sim": artist_sim,
                            "source": source_name,
                            "result": result,
                            "get_url_fn": get_url_fn,
                        })

                except Exception as e:
                    logger.debug(f"  {source_name} [{keyword}]: 搜索异常 {e}")
                    continue

        # 按综合分从高到低排序
        candidates.sort(key=lambda c: c["score"], reverse=True)

        # 从最高分开始，尝试获取播放链接
        for cand in candidates:
            try:
                play_url = cand["get_url_fn"](cand["result"])
                if play_url and play_url.startswith("http"):
                    r_name = cand["result"].get("name", "")
                    r_artist = cand["result"].get("artist", "")
                    logger.info(
                        f"  找到播放链接 ({cand['source']}): "
                        f"[{r_artist}] {r_name} "
                        f"[综合={cand['score']:.2f} 歌名={cand['name_sim']:.2f} 歌手={cand['artist_sim']:.2f}]"
                    )
                    return play_url
            except Exception as e:
                logger.debug(f"  获取链接异常: {e}")
                continue

        logger.warning(f"所有源都无法获取播放链接: {artist} - {song_name}")
        return None

    def _fetch_by_platform_id(self, platform, platform_id):
        """通过平台ID直接获取播放链接（100%准确）。失败则尝试algermusic API。"""
        try:
            if platform == "wangyi" and platform_id:
                # 第1步：网易云直查
                url = self.get_url_netease(platform_id)
                if url:
                    logger.info(f"  网易云直接获取: ID={platform_id}")
                    return url
                # 第2步：网易云直查失败（VIP），用algermusic API
                url = self.get_url_alger(platform_id)
                if url:
                    logger.info(f"  网易云VIP→algermusic获取: ID={platform_id}")
                    return url

            elif platform == "kuwo" and platform_id:
                url = self.get_url_kuwo(platform_id)
                if url:
                    logger.info(f"  酷我直接获取: rid={platform_id}")
                    return url

            elif platform == "qq" and platform_id:
                url = self.get_url_gequbao(platform_id)
                if url:
                    logger.info(f"  QQ→歌曲宝直接获取: mid={platform_id}")
                    return url

            elif platform == "kugou" and platform_id:
                url = self.get_url_kugou(platform_id)
                if url:
                    logger.info(f"  酷狗直接获取: hash={platform_id}")
                    return url

        except Exception as e:
            logger.debug(f"  平台ID直接获取失败 [{platform}/{platform_id}]: {e}")

        return None

    def get_url_alger(self, song_id):
        """通过 algermusic API 获取网易云歌曲播放链接（包括VIP歌曲）"""
        try:
            url = f"https://mc.alger.fun/api/song/url?id={song_id}"
            resp = self.session.get(url, headers={
                **HEADERS, "Referer": "https://mc.alger.fun/"
            }, timeout=10, verify=False)
            data = resp.json()
            if data.get("data") and data["data"]:
                play_url = data["data"][0].get("url")
                if play_url and play_url.startswith("http"):
                    return play_url
        except Exception as e:
            logger.debug(f"  algermusic获取失败 [{song_id}]: {e}")
        return None


# ============ 全局 fetcher 实例 ============

_fetcher = None

def get_fetcher():
    """获取全局 PlayUrlFetcher 实例（懒加载）"""
    global _fetcher
    if _fetcher is None:
        _fetcher = PlayUrlFetcher()
    return _fetcher


def search_play_url(song_name, artist="", platform="", platform_id=""):
    """搜索歌曲的播放链接"""
    return get_fetcher().fetch_play_url(song_name, artist, platform, platform_id)


def needs_update():
    """检查是否需要更新数据（今日是否已更新）"""
    today = datetime.now(timezone.utc).date()
    last_log = UpdateLog.query.filter_by(status="success").order_by(UpdateLog.created_at.desc()).first()
    if not last_log:
        return True
    return last_log.created_at.date() < today
