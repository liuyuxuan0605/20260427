# -*- coding: utf-8 -*-
"""配置文件"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 数据库
# 默认数据库：SQLite（歌曲数据）
SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'data', 'music.db')}"

# MySQL 绑定：账号相关数据（用户/收藏/评论/歌单）
MYSQL_URI = "mysql+pymysql://root:258958asd@localhost:3306/music_player?charset=utf8mb4"

SQLALCHEMY_BINDS = {
    "mysql": MYSQL_URI,
}

SQLALCHEMY_TRACK_MODIFICATIONS = False

# 密钥
SECRET_KEY = os.environ.get("SECRET_KEY", "music-player-secret-key-2026")

# 数据目录
DATA_DIR = os.path.join(BASE_DIR, "data")
COVERS_DIR = os.path.join(DATA_DIR, "covers")

# 分页
SONGS_PER_PAGE = 20

# 音乐爬虫脚本路径
HOT_SKILL_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", "hot")
MUSIC_DOWNLOADER_PATH = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", "music-downloader", "music_downloader.py")

# APScheduler
SCHEDULER_API_ENABLED = True
