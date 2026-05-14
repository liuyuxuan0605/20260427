import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, warnings
warnings.filterwarnings('ignore')
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

from flask import Flask
from models.db import db, Song
from config import SQLALCHEMY_DATABASE_URI
import requests, time

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://mc.alger.fun/',
}

def get_song_size(platform_id):
    """通过algermusic API获取歌曲的size和fee"""
    try:
        url = f'https://mc.alger.fun/api/song/url?id={platform_id}'
        r = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        data = r.json()
        if data.get('data') and data['data']:
            item = data['data'][0]
            return item.get('size', 0), item.get('fee', 0), item.get('url', '')
        return 0, -1, ''
    except:
        return 0, -1, ''

with app.app_context():
    total = Song.query.count()
    print(f'总歌曲: {total}')
    
    # 检查所有歌曲的fee和size
    full_count = 0
    trial_count = 0
    no_url_count = 0
    trial_songs = []
    
    songs = Song.query.all()
    for i, s in enumerate(songs):
        size, fee, play_url = get_song_size(s.platform_id)
        
        if not play_url:
            no_url_count += 1
            if no_url_count <= 10:
                print(f'  NO_URL [{s.id}] {s.artist} - {s.name} | pid={s.platform_id}')
        elif size < 1000000:  # < 1MB = trial
            trial_count += 1
            trial_songs.append(s.id)
            if trial_count <= 15:
                size_mb = size / 1024 / 1024
                print(f'  TRIAL [{s.id}] {s.artist} - {s.name} | size={size_mb:.1f}MB | fee={fee} | pid={s.platform_id}')
        else:
            full_count += 1
        
        if (i+1) % 200 == 0:
            print(f'  --- 进度: {i+1}/{total} | FULL={full_count} TRIAL={trial_count} NO_URL={no_url_count}')
        time.sleep(0.05)  # 控制请求频率
    
    print(f'\n=== 最终结果 ===')
    print(f'FULL: {full_count} | TRIAL: {trial_count} | NO_URL: {no_url_count} | TOTAL: {total}')
    
    if trial_songs:
        print(f'\n试听版歌曲ID（前30个）: {trial_songs[:30]}')
