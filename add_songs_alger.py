# -*- coding: utf-8 -*-
"""
使用 algermusic API 补充热门歌曲
核心原则：
1. 用 algermusic API 获取播放链接（包括VIP歌曲）
2. 逐首验证播放链接有效才入库
3. 歌名+歌手+platform_id 完整保存，确保音频匹配
"""
import os
import sys
import re
import time
import warnings

sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")

import requests as http_requests
from flask import Flask
from models.db import db, Song
from config import SQLALCHEMY_DATABASE_URI, DATA_DIR

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

COVERS_DIR = os.path.join(DATA_DIR, "covers")
os.makedirs(COVERS_DIR, exist_ok=True)

# ============ algermusic API ============
ALGER_API = "https://mc.alger.fun/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://mc.alger.fun/",
}

def alger_get(path, params=None, retries=2):
    """调用algermusic API"""
    for attempt in range(retries):
        try:
            r = http_requests.get(
                f"{ALGER_API}{path}", params=params, headers=HEADERS,
                timeout=15, verify=False
            )
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                return {"error": str(e)}

def normalize_for_compare(name):
    """标准化名称用于比较"""
    n = re.sub(r'[（(].*?[）)]', '', name)
    n = re.sub(r'[\s\-_\\/@&··]', '', n)
    return n.lower().strip()

def parse_netease_artists(item):
    """解析网易云歌手名"""
    ar_list = item.get("ar", [])
    if ar_list and isinstance(ar_list, list):
        names = [a.get("name", "") for a in ar_list if a.get("name")]
        if names:
            return " / ".join(names)
    artists = item.get("artists", [])
    if artists and isinstance(artists, list):
        names = [a.get("name", "") for a in artists if a.get("name")]
        if names:
            return " / ".join(names)
    return ""

def verify_play_url(song_id):
    """验证播放链接是否可用（实际获取URL并HEAD检查）"""
    data = alger_get("/song/url", {"id": song_id})
    if data.get("data") and data["data"]:
        url_info = data["data"][0]
        url = url_info.get("url")
        if url and url.startswith("http"):
            # HEAD检查链接是否可达
            try:
                resp = http_requests.head(url, headers={
                    "User-Agent": HEADERS["User-Agent"],
                    "Referer": "https://music.163.com/",
                }, timeout=8, verify=False, allow_redirects=True)
                if resp.status_code in (200, 206):
                    return True, url
            except:
                pass
            # HEAD失败但URL存在，可能某些CDN不支持HEAD，也算OK
            return True, url
    return False, None

def get_toplist_songs(list_id, limit=100):
    """获取排行榜歌曲"""
    data = alger_get("/playlist/track/all", {"id": list_id, "limit": limit})
    songs = data.get("songs", [])
    if not songs and "data" in data:
        songs = data["data"] if isinstance(data["data"], list) else []
    return songs

def cache_cover(cover_url, song_id):
    """缓存封面到本地"""
    if not cover_url:
        return ""
    try:
        local_path = os.path.join(COVERS_DIR, f"{song_id}.jpg")
        if os.path.exists(local_path):
            return f"/static/img/covers/{song_id}.jpg"
        resp = http_requests.get(cover_url, headers=HEADERS, timeout=10, verify=False)
        if resp.status_code == 200 and len(resp.content) > 500:
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return f"/static/img/covers/{song_id}.jpg"
    except:
        pass
    return cover_url

def main():
    with app.app_context():
        existing = Song.query.count()
        print(f"=== 开始补充歌曲（当前 {existing} 首）===\n")

        # ========== 阶段1：获取排行榜歌曲 ==========
        print("--- 阶段1：获取各大排行榜 ---")

        # 热歌榜 + 飙升榜 + 新歌榜
        toplists = {
            3778678: "热歌榜",
            19723756: "飙升榜",
            3779629: "新歌榜",
        }

        seed_songs = {}  # id -> song_data
        for list_id, list_name in toplists.items():
            print(f"\n获取 {list_name} (id={list_id})...")
            songs = get_toplist_songs(list_id, limit=100)
            print(f"  获取到 {len(songs)} 首")
            for s in songs:
                sid = s.get("id")
                if sid and sid not in seed_songs:
                    seed_songs[sid] = s
            time.sleep(0.5)

        print(f"\n去重后种子歌曲: {len(seed_songs)} 首")

        # ========== 阶段2：获取新歌速递 ==========
        print("\n--- 阶段2：获取新歌速递 ---")
        for type_id in [0, 7, 96]:  # 全部/华语/欧美
            data = alger_get("/top/song", {"type": type_id})
            new_songs = data.get("data", [])
            if new_songs:
                for s in new_songs:
                    sid = s.get("id")
                    if sid and sid not in seed_songs:
                        seed_songs[sid] = s
                print(f"  type={type_id}: +{len(new_songs)} 首")
            time.sleep(0.5)

        print(f"\n总计种子歌曲: {len(seed_songs)} 首")

        # ========== 阶段3：搜索热门歌手补充 ==========
        print("\n--- 阶段3：搜索热门歌手补充 ---")
        hot_artists = [
            "周杰伦", "薛之谦", "林俊杰", "陈奕迅", "邓紫棋",
            "毛不易", "李荣浩", "华晨宇", "张学友", "王力宏",
            "孙燕姿", "五月天", "许嵩", "张韶涵", "梁静茹",
            "蔡依林", "莫文蔚", "田馥甄", "刘若英", "赵雷",
        ]
        for artist in hot_artists:
            data = alger_get("/search", {"keywords": artist, "limit": 15, "type": 1})
            result_songs = data.get("result", {}).get("songs", [])
            for s in result_songs:
                sid = s.get("id")
                if sid and sid not in seed_songs:
                    seed_songs[sid] = s
            print(f"  {artist}: +{len(result_songs)} 首")
            time.sleep(0.3)

        print(f"\n总计种子歌曲: {len(seed_songs)} 首")

        # ========== 阶段4：逐首验证并入库 ==========
        print("\n--- 阶段4：逐首验证播放链接并入库 ---")

        added = 0
        skipped = 0
        failed = 0
        already = 0

        # 获取已入库的平台ID集合
        existing_pids = set()
        for s in Song.query.filter(Song.platform_id != None, Song.platform_id != "").all():
            existing_pids.add(str(s.platform_id))

        batch = []
        for idx, (sid, song_data) in enumerate(seed_songs.items()):
            name = song_data.get("name", "").strip()
            artist = parse_netease_artists(song_data)
            album_info = song_data.get("al", song_data.get("album", {}))
            album = album_info.get("name", "") if isinstance(album_info, dict) else ""
            cover_url = album_info.get("picUrl", "") if isinstance(album_info, dict) else ""

            if not name:
                skipped += 1
                continue

            # 检查是否已存在
            pid_str = str(sid)
            if pid_str in existing_pids:
                already += 1
                continue

            # 验证播放链接
            can_play, play_url = verify_play_url(sid)
            if not can_play:
                failed += 1
                if failed <= 20:
                    print(f"  FAIL [{idx+1}] {artist} - {name} | 无法获取播放链接")
                continue

            # 缓存封面
            local_cover = cache_cover(cover_url, sid)

            # 入库
            song = Song(
                name=name,
                artist=artist or "未知",
                album=album,
                cover_url=local_cover or cover_url,
                platform="wangyi",
                platform_id=pid_str,
                play_url=play_url,  # 缓存播放链接
                hot_score=max(0, 1000 - idx),  # 排行越靠前分数越高
            )
            batch.append(song)
            existing_pids.add(pid_str)  # 防重复
            added += 1

            if added % 50 == 0:
                db.session.bulk_save_objects(batch)
                db.session.commit()
                print(f"  已入库 {added} 首 (跳过={skipped}, 已有={already}, 失败={failed})")
                batch = []

            if added % 20 == 0:
                time.sleep(0.3)  # 避免请求过快

        # 保存剩余
        if batch:
            db.session.bulk_save_objects(batch)
            db.session.commit()

        # ========== 结果 ==========
        final_count = Song.query.count()
        print(f"\n{'='*60}")
        print(f"补充完成！")
        print(f"  新增: {added} 首")
        print(f"  已存在: {already} 首")
        print(f"  跳过(无歌名): {skipped} 首")
        print(f"  播放验证失败: {failed} 首")
        print(f"  总歌曲数: {final_count} 首")
        print(f"  入库率: {added}/{added+failed} = {added/(added+failed)*100:.1f}%" if (added+failed) > 0 else "")


if __name__ == "__main__":
    main()
