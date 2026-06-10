# -*- coding: utf-8 -*-
"""用户个人中心路由 — 使用 CrossDBService 组合服务（合成复用原则）"""
from flask import Blueprint, request, jsonify, render_template, session
from models.db import db, User, Favorite, Playlist, Song
from routes.auth import login_required, get_current_user
from services.cross_db import CrossDBService

user_bp = Blueprint("user", __name__)

# 初始化跨库组合服务
_cross_db = CrossDBService()


@user_bp.route("/profile")
@login_required
def profile_page():
    """个人中心页面"""
    user = get_current_user()
    # 使用组合服务获取收藏歌曲（批量2次查询，解决N+1问题）
    fav_songs_ctx = _cross_db.get_fav_songs(user.id, like_status=1)
    fav_songs = [ctx.song for ctx in fav_songs_ctx]
    # 歌单
    playlists = Playlist.query.filter_by(user_id=user.id).all()
    fav_count = len(fav_songs)
    return render_template("profile.html", user=user, fav_songs=fav_songs,
                           playlists=playlists, fav_count=fav_count)


@user_bp.route("/favorites")
@login_required
def favorites_page():
    """我喜欢的音乐 - 专用页面"""
    user = get_current_user()
    # 使用组合服务获取收藏歌曲（含时间戳，批量2次查询）
    fav_songs_ctx = _cross_db.get_fav_songs(user.id, like_status=1, with_timestamp=True)
    return render_template("favorites.html", user=user, fav_songs=fav_songs_ctx)


@user_bp.route("/api/user/favorites", methods=["GET"])
@login_required
def api_favorites():
    """获取用户收藏列表"""
    user = get_current_user()
    like_status = request.args.get("status", 1, type=int)
    # 使用组合服务获取收藏歌曲（批量2次查询）
    fav_songs_ctx = _cross_db.get_fav_songs(user.id, like_status=like_status)
    return jsonify({"code": "200", "data": [ctx.to_dict() for ctx in fav_songs_ctx]})
