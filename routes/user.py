# -*- coding: utf-8 -*-
"""用户个人中心路由"""
from flask import Blueprint, request, jsonify, render_template, session
from models.db import db, User, Favorite, Playlist, Song
from routes.auth import login_required, get_current_user

user_bp = Blueprint("user", __name__)


@user_bp.route("/profile")
@login_required
def profile_page():
    """个人中心页面"""
    user = get_current_user()
    # 收藏的歌曲
    favorites = Favorite.query.filter_by(user_id=user.id, like_status=1).all()
    fav_songs = [Song.query.get(f.song_id) for f in favorites]
    fav_songs = [s for s in fav_songs if s]
    # 歌单
    playlists = Playlist.query.filter_by(user_id=user.id).all()
    # 喜欢歌曲数量
    fav_count = len(fav_songs)
    return render_template("profile.html", user=user, fav_songs=fav_songs,
                           playlists=playlists, fav_count=fav_count)


@user_bp.route("/favorites")
@login_required
def favorites_page():
    """我喜欢的音乐 - 专用页面"""
    user = get_current_user()
    favorites = Favorite.query.filter_by(user_id=user.id, like_status=1).order_by(
        Favorite.created_at.desc()
    ).all()
    fav_songs = []
    for f in favorites:
        song = Song.query.get(f.song_id)
        if song:
            d = song.to_dict()
            d["fav_status"] = 1
            d["fav_created_at"] = f.created_at.strftime("%Y-%m-%d") if f.created_at else ""
            fav_songs.append(d)
    return render_template("favorites.html", user=user, fav_songs=fav_songs)


@user_bp.route("/api/user/favorites", methods=["GET"])
@login_required
def api_favorites():
    """获取用户收藏列表"""
    user = get_current_user()
    like_status = request.args.get("status", 1, type=int)
    favorites = Favorite.query.filter_by(user_id=user.id, like_status=like_status).all()
    songs = []
    for f in favorites:
        song = Song.query.get(f.song_id)
        if song:
            data = song.to_dict()
            data["fav_status"] = f.like_status
            songs.append(data)

    return jsonify({"code": "200", "data": songs})
