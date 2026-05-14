import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, warnings
warnings.filterwarnings('ignore')
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

from flask import Flask
from models.db import db, Song
from config import SQLALCHEMY_DATABASE_URI

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# 需要清理的关键词（歌名或歌手中包含这些的删掉）
JUNK_KEYWORDS = [
    '应援', '翻自', '肖宇梁', '粉丝', '伴奏', 'DJ版', 'Remix',
    '翻唱', 'cover', 'Cover', 'COVER', 'demo', 'Demo',
    '降调版', '加速版', '变调', '0.89x', '1.2x',
    '钢琴版', '纯音乐版', ' instrumental',
]

with app.app_context():
    total_before = Song.query.count()
    print(f'清理前: {total_before}首')
    
    to_delete = []
    for s in Song.query.all():
        combined = f'{s.name} {s.artist}'
        for kw in JUNK_KEYWORDS:
            if kw in combined:
                to_delete.append(s)
                break
    
    print(f'待删除: {len(to_delete)}首')
    for s in to_delete[:30]:
        print(f'  {s.artist} - {s.name}')
    
    if to_delete:
        for s in to_delete:
            db.session.delete(s)
        db.session.commit()
    
    total_after = Song.query.count()
    print(f'\n清理后: {total_after}首 (删除了{total_before - total_after}首)')
