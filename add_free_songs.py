# -*- coding: utf-8 -*-
"""
添加免费网易云歌曲到数据库
核心规则：只入库 fee=0 或 fee=8 且 size > 1MB 的歌曲（确保是完整版非试听）
使用 algermusic API 获取播放链接
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, warnings, time, json
warnings.filterwarnings('ignore')
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

from flask import Flask
from models.db import db, Song
from config import SQLALCHEMY_DATABASE_URI
import requests

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://mc.alger.fun/',
}
ALGER_API = 'https://mc.alger.fun/api'

# 过滤黑名单（歌名或歌手中包含这些关键词的跳过）
BLACKLIST = [
    '应援', '翻自', '肖宇梁', '粉丝', '伴奏', 'DJ版', 'Remix',
    '翻唱', 'cover', 'Cover', 'COVER', 'demo', 'Demo',
    '降调版', '加速版', '变调', '0.89x', '1.2x',
    '钢琴版', '纯音乐版', 'instrumental',
    '小迷妹',  # 网易云翻唱号
]

def is_junk(name, artist):
    """检查歌曲是否为低质量（翻唱、应援、伴奏等）"""
    combined = f'{name} {artist}'
    for kw in BLACKLIST:
        if kw in combined:
            return True
    return False

# 目标添加数量
TARGET = 300

def search_songs(keyword, limit=30):
    """搜索歌曲，返回列表"""
    url = f'{ALGER_API}/search?keywords={keyword}&limit={limit}&type=1'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        data = r.json()
        if data.get('result') and data['result'].get('songs'):
            return data['result']['songs']
    except:
        pass
    return []

def get_song_detail(song_id):
    """获取歌曲详情（歌名、歌手、封面）"""
    url = f'{ALGER_API}/song/detail?ids={song_id}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        data = r.json()
        if data.get('songs') and data['songs']:
            s = data['songs'][0]
            ar = s.get('ar', []) or s.get('artists', [])
            artist = '/'.join([a.get('name', '') for a in ar]) if ar else ''
            return {
                'name': s.get('name', ''),
                'artist': artist,
                'album': s.get('al', {}).get('name', '') or s.get('album', {}).get('name', ''),
                'cover_url': s.get('al', {}).get('picUrl', '') or s.get('album', {}).get('picUrl', ''),
            }
    except:
        pass
    return None

def check_song_free(song_id):
    """检查歌曲是否免费完整版，返回 (size, fee, play_url) 或 None"""
    url = f'{ALGER_API}/song/url?id={song_id}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        data = r.json()
        if data.get('data') and data['data']:
            item = data['data'][0]
            fee = item.get('fee', -1)
            size = item.get('size', 0)
            play_url = item.get('url', '')
            # 只接受 fee=0 或 fee=8，且 size > 1MB
            if fee in (0, 8) and size > 1000000 and play_url:
                return size, fee, play_url
    except:
        pass
    return None

def get_existing_ids():
    """获取数据库中已有的 platform_id 集合"""
    return set(s.platform_id for s in Song.query.with_entities(Song.platform_id).all())

with app.app_context():
    existing = get_existing_ids()
    print(f'数据库已有: {len(existing)}首')
    
    # 多种搜索来源
    search_keywords = [
        # 热门歌手（只搜知名歌手，避免翻唱号）
        '陈奕迅', '邓紫棋', '林俊杰', '周杰伦', '薛之谦',
        '毛不易', '华晨宇', '孙燕姿', '张韶涵', '王力宏',
        '李荣浩', '梁静茹', '五月天', '许嵩', '李宇春',
        '蔡健雅', '田馥甄', '张惠妹', '萧敬腾', '林宥嘉',
        '周深', '张杰', '刘宇宁', '赵雷',
        '陈粒', '房东的猫', '颜人中', '郭顶', '汪苏泷',
        '徐佳莹', '袁娅维', '张碧晨', '霍尊', '宋冬野',
        '李健', '朴树', '赵雷', '马頔', '陈鸿宇',
        '张震岳', '热狗MC HotDog', '潘玮柏', '陶喆',
        '单依纯', '希林娜依高', '郑润泽',
    ]
    
    added = 0
    skipped = 0
    vip = 0
    no_url = 0
    dup = 0
    
    print(f'\n目标: 添加 {TARGET} 首免费歌曲\n')
    
    for kw in search_keywords:
        if added >= TARGET:
            break
        
        print(f'搜索: {kw}')
        songs = search_songs(kw, limit=30)
        
        for s in songs:
            if added >= TARGET:
                break
            
            sid = str(s['id'])
            name = s.get('name', '')
            artist = s.get('artists', [{}])[0].get('name', '') if s.get('artists') else ''
            
            # 黑名单过滤
            if is_junk(name, artist):
                skipped += 1
                continue
            
            # 去重
            if sid in existing:
                dup += 1
                continue
            
            # 检查是否免费完整版
            result = check_song_free(s['id'])
            if result is None:
                # 可能是VIP或试听版
                vip += 1
                continue
            
            size, fee, play_url = result
            
            # 获取详情
            detail = get_song_detail(s['id'])
            if detail:
                song_name = detail['name']
                song_artist = detail['artist']
                song_album = detail['album']
                cover_url = detail['cover_url']
            else:
                song_name = name
                song_artist = artist
                song_album = ''
                cover_url = ''
            
            # 入库
            new_song = Song(
                name=song_name,
                artist=song_artist,
                album=song_album,
                cover_url=cover_url,
                platform='wangyi',
                platform_id=sid,
                play_url=play_url,
                hot_score=0,
            )
            db.session.add(new_song)
            existing.add(sid)
            added += 1
            
            size_mb = size / 1024 / 1024
            fee_tag = 'FREE' if fee == 0 else f'fee={fee}'
            print(f'  [{added}/{TARGET}] [{fee_tag}] {song_artist} - {song_name} | {size_mb:.1f}MB')
            
            time.sleep(0.1)
        
        time.sleep(0.3)
    
    db.session.commit()
    print(f'\n=== 完成 ===')
    print(f'新增: {added}首')
    print(f'跳过(VIP/试听): {vip}首')
    print(f'跳过(重复): {dup}首')
    print(f'数据库总计: {Song.query.count()}首')
