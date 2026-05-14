# -*- coding: utf-8 -*-
"""
从网易云排行榜获取免费歌曲
通过 algermusic API 获取排行榜歌单，筛选fee=0/8的免费完整版歌曲入库
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, warnings, time
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
NO_PROXY = {'http': None, 'https': None}

BLACKLIST = [
    '应援', '翻自', '粉丝', '伴奏', 'DJ版', 'Remix',
    '翻唱', 'cover', 'Cover', 'demo', 'Demo',
    '降调版', '加速版', '0.89x', '钢琴版', '纯音乐版',
    '小迷妹',
]

def is_junk(name, artist):
    combined = f'{name} {artist}'
    for kw in BLACKLIST:
        if kw in combined:
            return True
    return False

def get_existing_ids():
    return set(s.platform_id for s in Song.query.with_entities(Song.platform_id).all())

def check_song_free(song_id):
    url = f'{ALGER_API}/song/url?id={song_id}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        data = r.json()
        if data.get('data') and data['data']:
            item = data['data'][0]
            fee = item.get('fee', -1)
            size = item.get('size', 0)
            play_url = item.get('url', '')
            if fee in (0, 8) and size > 1000000 and play_url:
                return size, fee, play_url
    except:
        pass
    return None

def get_song_detail(song_id):
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
                'album': s.get('al', {}).get('name', '') or '',
                'cover_url': s.get('al', {}).get('picUrl', '') or '',
            }
    except:
        pass
    return None

with app.app_context():
    existing = get_existing_ids()
    total_before = Song.query.count()
    print(f'数据库已有: {total_before}首')
    
    # 先获取排行榜列表
    print('\n获取排行榜列表...')
    r = requests.get(f'{ALGER_API}/toplist', headers=HEADERS, timeout=10, verify=False)
    toplist = r.json()
    
    # 取几个主要排行榜的ID
    chart_ids = []
    if toplist.get('list'):
        for item in toplist['list'][:8]:
            chart_ids.append((item['id'], item['name']))
            print(f'  {item["name"]} (ID={item["id"]})')
    
    # 遍历排行榜获取歌曲
    added = 0
    vip = 0
    dup = 0
    junk = 0
    TARGET = 200  # 再加200首
    
    for chart_id, chart_name in chart_ids:
        if added >= TARGET:
            break
        
        print(f'\n--- 排行榜: {chart_name} ---')
        url = f'{ALGER_API}/playlist/track/all?id={chart_id}&limit=100'
        try:
            r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
            data = r.json()
            songs = data.get('songs', [])
            if not songs:
                # 有些排行榜返回格式不同
                songs = data.get('data', [])
        except:
            print(f'  获取失败')
            continue
        
        for s in songs:
            if added >= TARGET:
                break
            
            sid = str(s.get('id', ''))
            if not sid:
                continue
            
            name = s.get('name', '')
            ar = s.get('ar', []) or s.get('artists', [])
            artist = '/'.join([a.get('name', '') for a in ar]) if ar else ''
            
            # 去重
            if sid in existing:
                dup += 1
                continue
            
            # 黑名单
            if is_junk(name, artist):
                junk += 1
                continue
            
            # 检查是否免费完整版
            result = check_song_free(s['id'])
            if result is None:
                vip += 1
                continue
            
            size, fee, play_url = result
            
            # 获取详情（封面等）
            detail = get_song_detail(s['id'])
            if detail:
                song_name = detail['name']
                song_artist = detail['artist']
                song_album = detail['album']
                cover_url = detail['cover_url']
            else:
                song_name = name
                song_artist = artist
                song_album = s.get('al', {}).get('name', '') or ''
                cover_url = s.get('al', {}).get('picUrl', '') or ''
            
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
            
            fee_tag = 'FREE' if fee == 0 else f'fee={fee}'
            print(f'  [{added}/{TARGET}] [{fee_tag}] {song_artist} - {song_name} | {size/1024/1024:.1f}MB')
            time.sleep(0.05)
    
    db.session.commit()
    print(f'\n=== 完成 ===')
    print(f'新增: {added}首')
    print(f'跳过(VIP): {vip}首')
    print(f'跳过(重复): {dup}首')
    print(f'跳过(低质): {junk}首')
    print(f'数据库总计: {Song.query.count()}首')
