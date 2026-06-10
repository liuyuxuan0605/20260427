# -*- coding: utf-8 -*-
"""数据库模型"""
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    """用户表 - 存储在 MySQL"""
    __tablename__ = "users"
    __bind_key__ = "mysql"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    nickname = db.Column(db.String(80), default="")
    avatar = db.Column(db.String(500), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    favorites = db.relationship("Favorite", backref="user", lazy="dynamic")
    comments = db.relationship("Comment", backref="user", lazy="dynamic")
    playlists = db.relationship("Playlist", backref="user", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "nickname": self.nickname or self.username,
            "avatar": self.avatar,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
        }


class Song(db.Model):
    """歌曲表"""
    __tablename__ = "songs"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    artist = db.Column(db.String(200), nullable=False, index=True)
    album = db.Column(db.String(200), default="")
    genre = db.Column(db.String(50), default="")
    cover_url = db.Column(db.String(500), default="")
    local_cover = db.Column(db.String(500), default="")
    play_url = db.Column(db.String(500), default="")
    lyric = db.Column(db.Text, default="")
    platform = db.Column(db.String(20), default="")  # qq, wangyi, kugou, kuwo
    platform_id = db.Column(db.String(100), default="")
    hot_score = db.Column(db.Integer, default=0)  # 热度/排名
    play_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # 注意：favorites/comments/playlist_songs 的 song_id 不设外键，
    # 因为 songs 表在 SQLite，跨数据库无法建外键

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "artist": self.artist,
            "album": self.album,
            "genre": self.genre,
            "cover_url": self.local_cover or self.cover_url or "/static/img/default-cover.svg",
            "lyric": self.lyric,
            "platform": self.platform,
            "hot_score": self.hot_score,
            "play_count": self.play_count,
            "favorite_count": 0,  # 跨库查询，由路由层按需填充
        }


class Comment(db.Model):
    """评论表 - 存储在 MySQL"""
    __tablename__ = "comments"
    __bind_key__ = "mysql"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    song_id = db.Column(db.Integer, nullable=False)  # 跨库引用 SQLite songs，不建外键
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.user.nickname or self.user.username,
            "avatar": self.user.avatar,
            "song_id": self.song_id,
            "content": self.content,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
        }


class Favorite(db.Model):
    """收藏/喜好表 - 存储在 MySQL"""
    __tablename__ = "favorites"
    __bind_key__ = "mysql"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    song_id = db.Column(db.Integer, nullable=False)  # 跨库引用 SQLite songs，不建外键
    like_status = db.Column(db.Integer, default=0)  # 1=喜欢, -1=不喜欢, 0=中性
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("user_id", "song_id"),)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "song_id": self.song_id,
            "like_status": self.like_status,
        }


class Playlist(db.Model):
    """歌单表 - 存储在 MySQL"""
    __tablename__ = "playlists"
    __bind_key__ = "mysql"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    songs = db.relationship("PlaylistSong", backref="playlist", lazy="dynamic",
                             order_by="PlaylistSong.added_at.desc()")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description,
            "song_count": self.songs.count(),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
        }


class PlaylistSong(db.Model):
    """歌单-歌曲关联表 - 存储在 MySQL"""
    __tablename__ = "playlist_songs"
    __bind_key__ = "mysql"

    id = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id"), nullable=False)
    song_id = db.Column(db.Integer, nullable=False)  # 跨库引用 SQLite songs，不建外键
    added_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("playlist_id", "song_id"),)


class UpdateLog(db.Model):
    """数据更新日志"""
    __tablename__ = "update_logs"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(50), nullable=False)  # 数据来源
    status = db.Column(db.String(20), default="success")  # success/failed
    songs_added = db.Column(db.Integer, default=0)
    songs_updated = db.Column(db.Integer, default=0)
    message = db.Column(db.String(500), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
