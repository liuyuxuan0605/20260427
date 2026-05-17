# -*- coding: utf-8 -*-
"""
每日自动更新免费热榜歌曲
核心原则：
1. 用 algermusic API 获取热榜歌曲（飙升榜/热歌榜/说唱榜）
2. 严格过滤 fee=1（VIP/30秒试听），只入库 fee=0 和 fee=8 的免费歌曲
3. 每首歌必须拿到封面和LRC歌词才入库
4. 不添加音乐库中已存在的歌曲（按 platform_id 去重）
5. 每次最多添加 10 首歌，只加知名歌手的歌曲
6. 每天最多更新一次
"""
import os
import sys
import time
import logging
import hashlib
import re
import warnings
import requests as http_requests
from urllib.parse import quote
from datetime import datetime, timezone, date

warnings.filterwarnings("ignore")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

ALGER_BASE = "https://mc.alger.fun/api"
ALGER_REFERER = "https://mc.alger.fun/"

logger = logging.getLogger(__name__)

# 指定要抓取的热榜歌单（飙升榜、热歌榜、中文说唱榜）
TARGET_PLAYLIST_IDS = [
    19723756,   # 飙升榜
    3778678,    # 热歌榜
    991319590,  # 网易云中文说唱榜
]

# 每次最多添加的歌曲数
MAX_DAILY_ADD = 10

# 质量过滤关键词：歌名含这些词的跳过
QUALITY_BLACKLIST = {
    "DJ版", "dj版", "伴奏", "降速", "加速", "0.8x", "1.2x",
    "Montagem", "MONTAGEM", "Funk", "FUNK", "Jumpstyle", "JUMPSTYLE",
}

# 语言/地域过滤正则
LANGUAGE_SKIP_PATTERNS = [
    r'[\u3040-\u309F]',   # 平假名
    r'[\u30A0-\u30FF]',   # 片假名
    r'[\uAC00-\uD7AF]',   # 韩语
    r'[\u0400-\u04FF]',   # 西里尔字母（俄语等）
    r'[\u0E00-\u0E7F]',   # 泰语
]

# 知名歌手白名单（只有这些歌手的歌才会入库，合唱时所有歌手都必须知名）
FAMOUS_ARTISTS = {
    # 华语一线男歌手
    "周杰伦", "林俊杰", "陈奕迅", "薛之谦", "王力宏", "李健", "毛不易",
    "许嵩", "汪苏泷", "徐良", "李荣浩", "华晨宇", "张杰", "萧敬腾",
    "周深", "赵雷", "朴树", "许巍", "胡彦斌", "陶喆", "方大同",
    "杨坤", "萧煌奇", "沙宝亮", "满文军",
    # 华语一线女歌手
    "邓紫棋", "G.E.M.邓紫棋", "G.E.M. 邓紫棋", "王菲", "那英", "蔡依林",
    "孙燕姿", "梁静茹", "田馥甄", "张靓颖", "李宇春", "张惠妹",
    "莫文蔚", "袁娅维", "谭维维", "韩红", "范玮琪", "戴佩妮",
    "郁可唯", "杨千嬅", "容祖儿", "谢安琪", "A-Lin", "郭静", "丁当",
    "徐佳莹", "杨宗纬", "黄丽玲", "刘若英", "任然", "程响",
    "黄龄", "蔡健雅", "单依纯", "黄霄雲", "希林娜依高",
    # 港台经典
    "刘德华", "张学友", "谭咏麟", "李克勤", "光良", "品冠",
    "任贤齐", "周华健", "张信哲", "伍佰", "罗大佑", "李宗盛",
    "齐秦", "张雨生", "动力火车", "彭佳慧", "张宇",
    "潘玮柏", "张韶涵", "李玟", "温岚", "张敬轩", "侧田",
    "麦浚龙", "林忆莲", "陈淑桦", "吴克群",
    # 大陆经典
    "宋祖英", "孙楠", "韩磊", "刘欢", "凤凰传奇", "筷子兄弟",
    # 民谣/独立
    "花粥", "陈鸿宇", "宋冬野", "马頔", "赵英俊", "好妹妹", "好妹妹乐队",
    "房东的猫", "逃跑计划", "阿冗", "王贰浪",
    # 流量明星
    "王俊凯", "王源", "易烊千玺", "鹿晗", "张艺兴", "肖战", "王一博",
    "王嘉尔", "刘雨昕XIN LIU", "刘雨昕",
    # 说唱歌手
    "艾热", "艾热 AIR", "GAI", "GAI周延", "王以太", "刘聪", "KEY.L刘聪",
    "功夫胖", "功夫胖KUNGFU-PEN", "盛宇", "盛宇D-SHINE",
    "布瑞吉", "布瑞吉Bridge", "法老", "杨和苏", "杨和苏KeyNG", "KeyNG",
    "C-BLOCK", "MC HotDog 热狗", "蛋堡", "谢帝", "马思唯", "PSY.P",
    # 五月天/苏打绿/Beyond/F.I.R.
    "五月天", "五月天 阿信", "阿信", "苏打绿", "吴青峰",
    "BEYOND", "Beyond", "F.I.R.", "飞儿乐团",
    # 演员/其他
    "成龙", "谢霆锋", "吴宗宪", "沈腾", "何炅", "黄绮珊", "杨培安",
    # 更多知名
    "阿肆", "裘德", "陈粒", "薛凯琪", "郭采洁", "黄小琥", "梁咏琪",
    "陈小春", "黄立行", "八三夭", "茄子蛋", "颜人中", "王赫野",
    "蔡琴", "张芸京", "张栋梁", "苏有朋", "庾澄庆",
    "陈绮贞", "魏如萱", "蔡卓妍", "钟欣潼",
    "岳云鹏", "孟庭苇", "关之琳", "张震岳",
    "久石譲", "久石让", "Fa",
    # 知名品牌/游戏音乐
    "HOYO-MiX", "剑网3", "恋与深空", "小娟&山谷里的居民",
    "The Midnight",
    # 知名国际
    "Maroon 5", "Maroon5", "Coldplay", "Adele", "Ed Sheeran",
    "Taylor Swift", "Bruno Mars", "Imagine Dragons", "OneRepublic",
    "Sam Smith", "Dua Lipa", "Aqua", "Tobu", "Mariah Carey",
    "Gracie Abrams", "SanE", "Linkin Park", "Owl City",
    "Charlie Puth", "Shawn Mendes", "The Chainsmokers",
    "John Legend", "Kelly Clarkson", "P!nk", "P!NK",
    "Michael Jackson", "Madonna", "Beyoncé", "Rihanna",
    "Wiz Khalifa", "Christina Aguilera", "Andrea Bocelli",
    "Martin Garrix", "LISA", "B.o.B",
}


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


def _normalize_name(name):
    """标准化歌名用于去重比较"""
    n = re.sub(r'[（(].*?[）)]', '', name)
    n = re.sub(r'[\s\-_\\/@&·]', '', n)
    n = n.replace('〜', '~').replace('～', '~')
    return n.lower().strip()


def _is_all_famous(artist_str):
    """
    检查歌手字符串中所有歌手是否都是知名歌手。
    合唱歌曲：拆分 artist（按 / 分隔），所有歌手都必须在 FAMOUS_ARTISTS 中。
    独唱歌曲：歌手必须在 FAMOUS_ARTISTS 中。
    """
    if not artist_str:
        return False
    # 拆分歌手
    parts = [p.strip().rstrip('.').rstrip('-').rstrip('、').strip() for p in artist_str.replace('、', '/').split('/')]
    parts = [p for p in parts if p]
    if not parts:
        return False
    for p in parts:
        # 每个 singer 都必须匹配某个知名歌手名（精确匹配）
        matched = False
        for fa in FAMOUS_ARTISTS:
            if fa == p:  # 精确匹配
                matched = True
                break
            # 也允许子串匹配，但必须有一方包含另一方且长度差不超过2
            if (fa in p or p in fa) and abs(len(fa) - len(p)) <= 2:
                matched = True
                break
        if not matched:
            return False
    return True


def get_hot_playlist_ids(session):
    """获取指定的热榜歌单ID列表（飙升榜/热歌榜/新歌榜/中文说唱榜）"""
    return list(TARGET_PLAYLIST_IDS)


def get_playlist_tracks(session, playlist_id, limit=50):
    """从歌单获取歌曲列表（含 fee 信息）"""
    songs = []
    try:
        url = f"{ALGER_BASE}/playlist/track/all?id={playlist_id}&limit={limit}"
        resp = session.get(url, headers={"Referer": ALGER_REFERER}, timeout=15, verify=False)
        data = resp.json()
        track_list = data.get("songs", [])
        if not track_list:
            track_list = data.get("data", [])
        for item in track_list:
            song_id = item.get("id")
            if not song_id:
                continue
            fee = item.get("fee", -1)
            name = item.get("name", "").strip()
            artist = _parse_netease_artists(item)
            album = item.get("al", {})
            cover_url = album.get("picUrl", "")
            songs.append({
                "name": name,
                "artist": artist.strip(),
                "album": album.get("name", "").strip(),
                "cover_url": cover_url,
                "platform_id": str(song_id),
                "fee": fee,
            })
    except Exception as e:
        logger.debug(f"获取歌单 {playlist_id} 失败: {e}")
    return songs


def search_free_song(session, name, artist=""):
    """搜索一首歌的免费版本（fee=0 或 fee=8）"""
    try:
        keyword = f"{artist} {name}" if artist else name
        url = f"https://music.163.com/api/search/get?s={quote(keyword)}&type=1&offset=0&limit=20"
        resp = session.get(url, headers={"Referer": "https://music.163.com"}, timeout=10)
        data = resp.json()
        for item in data.get("result", {}).get("songs", []):
            fee = item.get("fee", -1)
            if fee in (0, 8):  # 只取免费歌曲
                artist_str = _parse_netease_artists(item)
                album = item.get("al", {})
                return {
                    "name": item.get("name", "").strip(),
                    "artist": artist_str.strip(),
                    "album": album.get("name", "").strip(),
                    "cover_url": album.get("picUrl", ""),
                    "platform_id": str(item.get("id", "")),
                    "fee": fee,
                }
    except Exception:
        pass
    return None


def get_cover_from_alger(session, platform_id):
    """从 algermusic 获取歌曲封面"""
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


def get_lyric_from_alger(session, platform_id):
    """从 algermusic 获取 LRC 歌词"""
    try:
        url = f"{ALGER_BASE}/lyric?id={platform_id}"
        resp = session.get(url, headers={"Referer": ALGER_REFERER}, timeout=10, verify=False)
        data = resp.json()
        # 优先原歌词
        lrc = data.get("lrc", {}).get("lyric", "")
        if lrc and lrc.strip() and "[" in lrc and "]" in lrc:
            return lrc.strip()
        # 回退翻译歌词
        tlyric = data.get("tlyric", {}).get("lyric", "")
        if tlyric and tlyric.strip() and "[" in tlyric and "]" in tlyric:
            return tlyric.strip()
    except Exception:
        pass
    return ""


def cache_cover_image(cover_url, song_name, artist, covers_dir):
    """缓存封面到本地"""
    if not cover_url or not cover_url.startswith("http"):
        return ""
    # 防止 covers_dir 为空导致文件存到项目根目录
    if not covers_dir:
        covers_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "covers")
    try:
        ext = ".jpg"
        if ".png" in cover_url:
            ext = ".png"
        elif ".webp" in cover_url:
            ext = ".webp"
        filename = hashlib.md5(f"{song_name}_{artist}".encode("utf-8")).hexdigest() + ext
        local_path = os.path.join(covers_dir, filename)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
            return f"/data/covers/{filename}"
        resp = http_requests.get(
            cover_url,
            headers={"User-Agent": HEADERS["User-Agent"], "Referer": "https://music.163.com"},
            timeout=10, verify=False
        )
        if resp.status_code == 200 and len(resp.content) > 1024:
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return f"/data/covers/{filename}"
    except Exception:
        pass
    return cover_url  # 返回远程URL作为兜底


def daily_update_free_songs(app):
    """
    每日自动更新：从飙升榜/热歌榜/说唱榜中获取知名歌手的免费歌曲入库。
    最多添加 10 首，每天只执行一次。
    只添加 FAMOUS_ARTISTS 中的歌手歌曲，合唱时所有歌手都必须知名。
    
    返回: (added_count, message)
    """
    # 调试：确认 _is_all_famous 被正确引用
    _test = _is_all_famous("张昊晴")
    logger.info(f"[DEBUG] _is_all_famous('张昊晴') = {_test} (应为False)")
    
    from models.db import db, Song, UpdateLog

    session = http_requests.Session()
    session.headers.update(HEADERS)
    session.verify = False

    # 检查今天是否已更新过
    today = date.today()
    last_log = UpdateLog.query.filter_by(source="daily-free-update", status="success").order_by(
        UpdateLog.created_at.desc()
    ).first()
    if last_log and last_log.created_at.date() >= today:
        logger.info("今日已更新过免费热榜歌曲，跳过")
        return 0, "今日已更新"

    logger.info("=== 开始每日免费热榜更新 ===")

    # 收集现有 platform_id 和歌名+歌手 用于去重
    existing_pids = set()
    existing_name_artist = set()
    for s in Song.query.filter(Song.platform_id != None, Song.platform_id != "").all():
        existing_pids.add(str(s.platform_id))
        key = f"{_normalize_name(s.name)}_{_normalize_name(s.artist)}"
        existing_name_artist.add(key)
    logger.info(f"当前歌曲库: {Song.query.count()} 首, 已有 platform_id: {len(existing_pids)} 个")

    # 收集候选歌曲：只从3个热榜歌单获取（按排名顺序）
    candidates = []
    seen_pids = set()

    playlist_ids = get_hot_playlist_ids(session)
    logger.info(f"获取到 {len(playlist_ids)} 个热榜歌单")
    for pid in playlist_ids:
        tracks = get_playlist_tracks(session, pid, limit=100)
        for t in tracks:
            if t["platform_id"] not in seen_pids:
                seen_pids.add(t["platform_id"])
                candidates.append(t)
        time.sleep(0.3)

    logger.info(f"共获取 {len(candidates)} 首候选歌曲")

    # 过滤 + 入库（只加知名歌手歌曲，合唱时所有歌手都必须知名）
    total_added = 0
    total_vip_skip = 0
    total_dup_skip = 0
    total_no_cover = 0
    total_no_lyric = 0
    total_not_famous = 0

    for candidate in candidates:
        if total_added >= MAX_DAILY_ADD:
            break

        pid = candidate["platform_id"]
        name = candidate["name"]
        artist = candidate["artist"]
        fee = candidate.get("fee", -1)

        if not name or not pid:
            continue

        # 0. 核心过滤：只加知名歌手的歌曲
        # 合唱时，所有歌手都必须是知名歌手
        famous_check = _is_all_famous(artist)
        logger.info(f"  [CHECK] {artist} → famous={famous_check}")
        if not famous_check:
            total_not_famous += 1
            continue

        # 1. 严格过滤 VIP（fee=1 只有30秒试听）
        if fee == 1:
            # 尝试搜索这首歌的免费版本
            free_version = search_free_song(session, name, artist)
            if free_version and free_version["platform_id"] not in existing_pids:
                candidate = free_version
                pid = candidate["platform_id"]
                name = candidate["name"]
                artist = candidate["artist"]
                fee = candidate["fee"]
            else:
                total_vip_skip += 1
                continue

        # 再次检查 fee
        if fee not in (0, 8):
            total_vip_skip += 1
            continue

        # 1.5 质量过滤：DJ版/伴奏/降速版等
        name_lower = name.lower()
        skip_for_quality = False

        # DJ版/伴奏/降速 - 全部跳过
        for kw in QUALITY_BLACKLIST:
            if kw in name_lower:
                skip_for_quality = True
                break

        # 语言过滤：日语/韩语/俄语/泰语等
        if not skip_for_quality:
            combined_text = name + artist
            for pattern in LANGUAGE_SKIP_PATTERNS:
                if re.search(pattern, combined_text):
                    skip_for_quality = True
                    break

        if skip_for_quality:
            total_not_famous += 1
            continue

        # 2. 去重（按 platform_id）
        if pid in existing_pids:
            total_dup_skip += 1
            continue

        # 3. 去重（按歌名+歌手模糊匹配）
        name_artist_key = f"{_normalize_name(name)}_{_normalize_name(artist)}"
        if name_artist_key in existing_name_artist:
            total_dup_skip += 1
            continue

        # 4. 获取封面（如果候选没有封面URL，从 algermusic 补充）
        cover_url = candidate.get("cover_url", "")
        if not cover_url:
            cover_url = get_cover_from_alger(session, pid)
            if not cover_url:
                total_no_cover += 1
                continue  # 没封面的不添加

        # 5. 获取 LRC 歌词
        lyric = get_lyric_from_alger(session, pid)
        if not lyric:
            total_no_lyric += 1
            continue  # 没歌词的不添加

        # 6. 缓存封面到本地
        local_cover = cache_cover_image(cover_url, name, artist, app.config.get("COVERS_DIR", ""))

        # 7. 入库
        song = Song(
            name=name,
            artist=artist or "未知",
            album=candidate.get("album", ""),
            cover_url=local_cover or cover_url,
            local_cover=local_cover if local_cover.startswith("/data/covers/") else "",
            platform="wangyi",
            platform_id=pid,
            lyric=lyric,
            hot_score=candidate.get("hot_score", 0),
        )
        db.session.add(song)
        existing_pids.add(pid)
        existing_name_artist.add(name_artist_key)
        total_added += 1

        logger.info(f"  +[{total_added}] {artist} - {name} (fee={fee})")

        if total_added % 10 == 0:
            db.session.commit()

        time.sleep(0.15)

    # 提交
    db.session.commit()

    # 记录更新日志
    msg = (
        f"每日免费更新: 新增{total_added}首, "
        f"VIP跳过={total_vip_skip}, 重复={total_dup_skip}, "
        f"非知名跳过={total_not_famous}, 无封面={total_no_cover}, 无歌词={total_no_lyric}"
    )
    log = UpdateLog(
        source="daily-free-update",
        status="success",
        songs_added=total_added,
        songs_updated=0,
        message=msg,
    )
    db.session.add(log)
    db.session.commit()

    logger.info(f"=== 每日更新完成: {msg} ===")
    return total_added, msg
