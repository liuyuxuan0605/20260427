# -*- coding: utf-8 -*-
"""
跨数据库组合服务层 — 合成复用原则（CRP）实现

核心设计思想：
1. **组合优于继承**：通过组合 Song（SQLite）+ 用户关系（MySQL）创建富对象，
   而非通过继承扩展 Song 模型
2. **封装跨库查询**：将散落在5+路由函数中的"两步查询+手工组装"逻辑
   统一封装到 CrossDBService 中
3. **单一职责**：CrossDBService 只负责跨库数据组装，
   单库查询仍由各 Model 自带的 query 处理

架构：
  ┌──────────────────────────────┐
  │     路由层 (routes/)          │  ← 只调用 CrossDBService
  ├──────────────────────────────┤
  │  CrossDBService (组合服务)    │  ← 封装跨库查询+组装
  │  ├─ SongWithUserContext       │  ← 组合对象：歌曲+用户上下文
  │  └─ PlaylistWithSongs        │  ← 组合对象：歌单+歌曲详情
  ├──────────────────────────────┤
  │  Song (SQLite) │ User/Fav... │  ← 原始 Model 不变
  └──────────────────────────────┘
"""
import logging
from models.db import db, Song, Favorite, Comment, Playlist, PlaylistSong

logger = logging.getLogger(__name__)


# ============ 组合对象 ============

class SongWithUserContext:
    """
    组合对象：歌曲 + 用户上下文

    合成复用原则的核心体现：
    - 不是通过继承 Song 来添加 fav_status 字段
    - 而是通过组合（has-a）将 Song 对象与用户关系数据组合在一起
    - Song 对象本身不变，用户上下文是额外组合的

    用法：
        ctx = SongWithUserContext(song, fav_status=1)
        d = ctx.to_dict()  # 包含歌曲信息 + fav_status
    """

    def __init__(self, song, fav_status=0, fav_created_at=None):
        self.song = song              # 组合：has-a Song（来自SQLite）
        self.fav_status = fav_status  # 组合：has-a 用户关系（来自MySQL）
        self.fav_created_at = fav_created_at  # 组合：收藏时间

    def to_dict(self):
        """序列化为字典，合并歌曲信息 + 用户上下文"""
        d = self.song.to_dict()
        d["fav_status"] = self.fav_status
        if self.fav_created_at:
            d["fav_created_at"] = self.fav_created_at
        return d


class PlaylistWithSongs:
    """
    组合对象：歌单 + 歌曲详情列表

    合成复用原则：
    - Playlist（MySQL）通过 song_id 引用 Song（SQLite）
    - 不通过继承，而是通过组合将歌单元数据与歌曲详情组装在一起
    """

    def __init__(self, playlist, songs_with_context):
        self.playlist = playlist              # 组合：has-a Playlist
        self.songs_with_context = songs_with_context  # 组合：has-a [SongWithUserContext]

    def to_dict(self, user_id=None):
        """序列化，合并歌单信息 + 歌曲详情"""
        d = self.playlist.to_dict()
        d["songs"] = [s.to_dict() for s in self.songs_with_context]
        return d


# ============ 组合服务 ============

class CrossDBService:
    """
    跨数据库组合服务 — 合成复用原则的核心载体

    职责：
    1. 封装所有 SQLite↔MySQL 的跨库查询逻辑
    2. 返回组合对象（SongWithUserContext / PlaylistWithSongs）
    3. 路由层只需调用一个方法，无需手写两步查询

    设计原则：
    - 组合优于继承：不修改 Song 模型，通过组合添加用户上下文
    - 单一职责：只负责跨库数据组装，不做业务逻辑
    - DRY：所有跨库查询逻辑集中在此，路由层零重复

    用法：
        service = CrossDBService()
        # 给歌曲列表添加收藏状态
        songs_with_ctx = service.enrich_songs_with_fav(user_id, songs)
        # 获取用户收藏歌曲
        fav_songs = service.get_fav_songs(user_id)
        # 获取歌单详情（含歌曲信息）
        playlist_data = service.get_playlist_detail(playlist_id, user_id)
    """

    def __init__(self):
        self._song_repo = Song
        self._fav_repo = Favorite
        self._comment_repo = Comment
        self._playlist_repo = Playlist
        self._playlist_song_repo = PlaylistSong

    # ---- 收藏映射查询（最核心的跨库操作） ----

    def get_fav_map(self, user_id, song_ids=None):
        """
        获取用户对歌曲的收藏状态映射

        Args:
            user_id: 用户ID（MySQL）
            song_ids: 歌曲ID列表（SQLite），None则查询全部

        Returns:
            dict: {song_id: like_status}

        这是跨库查询的核心原语：先查MySQL获取收藏记录，
        再用返回的song_id去查SQLite获取歌曲详情。
        """
        query = self._fav_repo.query.filter_by(user_id=user_id)
        if song_ids:
            query = query.filter(self._fav_repo.song_id.in_(song_ids))
        favs = query.all()
        return {f.song_id: f.like_status for f in favs}

    def get_fav_with_timestamp_map(self, user_id, like_status=1):
        """
        获取用户收藏映射（含时间戳）

        Returns:
            dict: {song_id: {"like_status": int, "created_at": str}}
        """
        favs = self._fav_repo.query.filter_by(
            user_id=user_id, like_status=like_status
        ).order_by(self._fav_repo.created_at.desc()).all()
        return {
            f.song_id: {
                "like_status": f.like_status,
                "created_at": f.created_at.strftime("%Y-%m-%d") if f.created_at else "",
            }
            for f in favs
        }

    # ---- 歌曲列表 + 用户上下文（组合） ----

    def enrich_songs_with_fav(self, user_id, songs):
        """
        给歌曲列表添加用户收藏状态 — 合成复用的核心体现

        流程：
        1. 从 songs（SQLite）提取 ID 列表
        2. 查 MySQL 获取收藏映射
        3. 组合成 SongWithUserContext 对象

        Args:
            user_id: 用户ID，None则全部fav_status=0
            songs: Song 对象列表

        Returns:
            list[SongWithUserContext]
        """
        if not user_id or not songs:
            return [SongWithUserContext(s, fav_status=0) for s in songs]

        song_ids = [s.id for s in songs]
        fav_map = self.get_fav_map(user_id, song_ids)

        return [
            SongWithUserContext(s, fav_status=fav_map.get(s.id, 0))
            for s in songs
        ]

    # ---- 收藏歌曲查询（解决N+1问题） ----

    def get_fav_songs(self, user_id, like_status=1, with_timestamp=False):
        """
        获取用户收藏的歌曲 — 两步查询，解决N+1问题

        Before（N+1跨库查询）：
            favs = Favorite.query.filter_by(user_id=uid).all()
            for f in favs:
                song = Song.query.get(f.song_id)  # N次SQLite查询！

        After（批量2次查询）：
            fav_songs = service.get_fav_songs(user_id)  # 1次MySQL + 1次SQLite

        Returns:
            list[SongWithUserContext]
        """
        if with_timestamp:
            fav_map = self.get_fav_with_timestamp_map(user_id, like_status)
        else:
            query = self._fav_repo.query.filter_by(user_id=user_id, like_status=like_status)
            query = query.order_by(self._fav_repo.created_at.desc())
            favs = query.all()
            fav_map = {f.song_id: f.like_status for f in favs}

        if not fav_map:
            return []

        # 批量查SQLite（1次查询替代N次）
        song_ids = list(fav_map.keys())
        songs = self._song_repo.query.filter(self._song_repo.id.in_(song_ids)).all()
        song_dict = {s.id: s for s in songs}

        # 组装（按收藏时间倒序）
        result = []
        for song_id, info in fav_map.items():
            song = song_dict.get(song_id)
            if song:
                if isinstance(info, dict):
                    ctx = SongWithUserContext(
                        song,
                        fav_status=info["like_status"],
                        fav_created_at=info.get("created_at"),
                    )
                else:
                    ctx = SongWithUserContext(song, fav_status=info)
                result.append(ctx)

        return result

    # ---- 单首歌曲 + 用户上下文 ----

    def get_song_with_fav(self, song_id, user_id=None):
        """
        获取单首歌曲 + 用户收藏状态

        Returns:
            SongWithUserContext or None
        """
        song = self._song_repo.query.get(song_id)
        if not song:
            return None

        fav_status = 0
        if user_id:
            fav = self._fav_repo.query.filter_by(
                user_id=user_id, song_id=song_id
            ).first()
            if fav:
                fav_status = fav.like_status

        return SongWithUserContext(song, fav_status=fav_status)

    # ---- 歌单详情（跨库组合） ----

    def get_playlist_detail(self, playlist_id, user_id=None):
        """
        获取歌单详情 + 歌曲列表 — 跨库组合

        流程：
        1. 查MySQL获取歌单元数据 + 歌曲关联列表
        2. 提取song_ids，批量查SQLite获取歌曲详情
        3. 组合成PlaylistWithSongs

        Returns:
            PlaylistWithSongs or None
        """
        playlist = self._playlist_repo.query.get_or_404(playlist_id)
        ps_items = playlist.songs.all()

        if not ps_items:
            return PlaylistWithSongs(playlist, [])

        # 批量查SQLite
        song_ids = [ps.song_id for ps in ps_items]
        songs = self._song_repo.query.filter(
            self._song_repo.id.in_(song_ids)
        ).all() if song_ids else []
        song_dict = {s.id: s for s in songs}

        # 获取收藏状态
        fav_map = {}
        if user_id:
            fav_map = self.get_fav_map(user_id, song_ids)

        # 按MySQL中的顺序排列，组合成 SongWithUserContext
        songs_with_ctx = []
        for ps in ps_items:
            song = song_dict.get(ps.song_id)
            if song:
                ctx = SongWithUserContext(song, fav_status=fav_map.get(ps.song_id, 0))
                songs_with_ctx.append(ctx)

        return PlaylistWithSongs(playlist, songs_with_ctx)

    # ---- 歌曲存在性验证（跨库安全） ----

    def song_exists(self, song_id):
        """
        验证歌曲是否存在（不加载到session，避免跨库事务冲突）

        关键：用 get() 而不是 get_or_404()
        get_or_404() 会把 Song 对象 attach 到 session，
        与 MySQL 的 Favorite/Comment 在同一 db.session 提交时冲突
        """
        return self._song_repo.query.get(song_id) is not None

    # ---- 推荐系统辅助 ----

    def get_liked_songs(self, user_id):
        """
        获取用户喜欢的歌曲列表 — 用于推荐系统

        Returns:
            list[Song] (纯Song对象，无用户上下文，推荐系统不需要)
        """
        liked_favs = self._fav_repo.query.filter_by(
            user_id=user_id, like_status=1
        ).all()
        liked_song_ids = [f.song_id for f in liked_favs]
        if not liked_song_ids:
            return []
        return self._song_repo.query.filter(
            self._song_repo.id.in_(liked_song_ids)
        ).all()
