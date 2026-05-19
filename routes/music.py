# -*- coding: utf-8 -*-
"""音乐相关路由"""
import logging
import requests as http_requests
from flask import Blueprint, request, jsonify, session, render_template, Response, stream_with_context
from sqlalchemy import or_, func
from models.db import db, Song, Favorite, Comment, Playlist, PlaylistSong
from routes.auth import login_required, get_current_user
from crawler.music_crawler import search_play_url, update_all_music, needs_update
from config import SONGS_PER_PAGE

logger = logging.getLogger(__name__)

music_bp = Blueprint("music", __name__)


@music_bp.route("/")
def index():
    """主页"""
    # 热门歌曲（按热度降序）
    hot_songs = Song.query.order_by(Song.hot_score.desc()).limit(12).all()
    # 新歌（按创建时间降序）
    new_songs = Song.query.order_by(Song.created_at.desc()).limit(12).all()

    # 用户个性推荐
    recommended = []
    user = get_current_user()
    if user:
        # 获取用户喜欢的歌曲
        liked_songs = Song.query.join(Favorite).filter(
            Favorite.user_id == user.id,
            Favorite.like_status == 1,
        ).all()

        if liked_songs:
            # 策略1: 基于喜欢的歌手推荐（同歌手不同歌）
            liked_artists = set()
            for s in liked_songs:
                # 处理多歌手的情况 "歌手1 / 歌手2"
                parts = s.artist.replace(" / ", "/").replace("、", "/").split("/")
                for p in parts:
                    p = p.strip()
                    if p:
                        liked_artists.add(p)

            liked_song_ids = [s.id for s in liked_songs]

            # 同歌手推荐（优先级最高）
            artist_recommended = []
            if liked_artists:
                for artist_name in list(liked_artists)[:10]:
                    matches = Song.query.filter(
                        Song.artist.contains(artist_name),
                        Song.id.not_in(liked_song_ids),
                    ).order_by(Song.hot_score.desc()).limit(3).all()
                    artist_recommended.extend(matches)

            # 策略2: 基于喜欢的类型推荐
            liked_genres = set(s.genre for s in liked_songs if s.genre)
            genre_recommended = []
            if liked_genres:
                genre_recommended = Song.query.filter(
                    Song.genre.in_(liked_genres),
                    Song.id.not_in(liked_song_ids + [s.id for s in artist_recommended]),
                ).order_by(func.random()).limit(8).all()

            # 合并：歌手推荐优先，类型推荐补充
            recommended = artist_recommended[:6]
            if len(recommended) < 8:
                needed = 8 - len(recommended)
                existing_ids = liked_song_ids + [s.id for s in recommended]
                more = [s for s in genre_recommended if s.id not in existing_ids][:needed]
                recommended.extend(more)

        # 如果推荐不够，补充热门
        if len(recommended) < 8:
            existing_ids = [s.id for s in recommended] + [s.id for s in liked_songs]
            more = Song.query.filter(
                Song.id.not_in(existing_ids)
            ).order_by(Song.hot_score.desc()).limit(8 - len(recommended)).all()
            recommended.extend(more)
    else:
        recommended = Song.query.order_by(func.random()).limit(8).all()

    return render_template("index.html",
                           hot_songs=hot_songs,
                           new_songs=new_songs,
                           recommended=recommended)


@music_bp.route("/songs")
def song_list():
    """音乐列表页"""
    return render_template("songs.html")


@music_bp.route("/song/<int:song_id>")
def song_detail(song_id):
    """音乐详情页"""
    song = Song.query.get_or_404(song_id)
    # 相似歌曲（同歌手或同类型）
    similar = Song.query.filter(
        Song.id != song.id,
        or_(Song.artist == song.artist, Song.genre == song.genre)
    ).limit(6).all()

    # 用户喜好状态
    fav_status = 0
    user = get_current_user()
    if user:
        fav = Favorite.query.filter_by(user_id=user.id, song_id=song.id).first()
        if fav:
            fav_status = fav.like_status

    return render_template("song_detail.html",
                           song=song,
                           similar=similar,
                           fav_status=fav_status)


@music_bp.route("/about")
def about():
    """关于页面"""
    return render_template("about.html")


# ============ API ============

@music_bp.route("/api/songs", methods=["GET"])
def api_songs():
    """获取歌曲列表（分页、搜索、筛选）"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", SONGS_PER_PAGE, type=int)
    keyword = request.args.get("q", "").strip()
    genre = request.args.get("genre", "").strip()
    artist = request.args.get("artist", "").strip()
    sort = request.args.get("sort", "hot")  # hot, new, name

    query = Song.query

    if keyword:
        query = query.filter(or_(
            Song.name.contains(keyword),
            Song.artist.contains(keyword),
            Song.album.contains(keyword),
        ))
    if genre:
        query = query.filter(Song.genre == genre)
    if artist:
        query = query.filter(Song.artist.contains(artist))

    # 排序
    if sort == "new":
        query = query.order_by(Song.created_at.desc())
    elif sort == "name":
        query = query.order_by(Song.name.asc())
    else:  # hot
        query = query.order_by(Song.hot_score.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    # 获取当前用户的喜欢状态
    user = get_current_user()
    fav_map = {}
    if user:
        song_ids = [s.id for s in pagination.items]
        favs = Favorite.query.filter(
            Favorite.user_id == user.id,
            Favorite.song_id.in_(song_ids)
        ).all()
        fav_map = {f.song_id: f.like_status for f in favs}

    songs_data = []
    for s in pagination.items:
        d = s.to_dict()
        d["fav_status"] = fav_map.get(s.id, 0)
        songs_data.append(d)

    return jsonify({
        "code": "200",
        "data": {
            "songs": songs_data,
            "total": pagination.total,
            "page": page,
            "per_page": per_page,
            "pages": pagination.pages,
        }
    })


@music_bp.route("/api/song/<int:song_id>", methods=["GET"])
def api_song_detail(song_id):
    """获取歌曲详情"""
    song = Song.query.get_or_404(song_id)
    song.play_count += 1
    db.session.commit()

    data = song.to_dict()
    # 获取用户喜好
    user = get_current_user()
    if user:
        fav = Favorite.query.filter_by(user_id=user.id, song_id=song.id).first()
        data["fav_status"] = fav.like_status if fav else 0
    else:
        data["fav_status"] = 0

    return jsonify({"code": "200", "data": data})


@music_bp.route("/api/song/<int:song_id>/play", methods=["GET"])
def api_song_play(song_id):
    """获取歌曲播放链接（代理转发）"""
    song = Song.query.get_or_404(song_id)
    play_url = song.play_url

    # 如果有缓存的链接，先验证是否还有效
    if play_url:
        try:
            probe = http_requests.head(play_url, headers=_proxy_headers(play_url), timeout=5, verify=False, allow_redirects=True)
            if probe.status_code not in (200, 206):
                logger.info(f"缓存链接已失效 (HTTP {probe.status_code}), 重新搜索")
                play_url = None
        except Exception:
            play_url = None

    # 缓存无效或没有缓存，重新搜索
    if not play_url:
        play_url = search_play_url(song.name, song.artist, song.platform, song.platform_id)
        if play_url:
            song.play_url = play_url
            db.session.commit()
        else:
            return jsonify({"code": "404", "message": "该歌曲暂无播放源，请尝试其他歌曲"}), 404

    # 代理转发音频流
    return proxy_audio(play_url)


def _proxy_headers(url):
    """根据 URL 判断需要的 Referer"""
    referer = "https://www.kuwo.cn/"
    if "kugou" in url:
        referer = "https://www.kugou.com/"
    elif "163.com" in url or "126.net" in url:
        referer = "https://music.163.com/"
    elif "qq.com" in url:
        referer = "https://y.qq.com/"
    elif "thttt" in url:
        referer = "https://www.thttt.com/"
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
    }


def proxy_audio(url):
    """代理转发音频流，添加正确的 Referer 头"""
    headers = _proxy_headers(url)

    # 支持浏览器的 Range 请求（拖动进度条）
    req_headers = dict(headers)
    range_header = request.headers.get("Range")
    if range_header:
        req_headers["Range"] = range_header

    try:
        resp = http_requests.get(url, headers=req_headers, timeout=30, stream=True, verify=False)
        
        # 如果代理返回了错误（链接过期），尝试重新搜索一次
        if resp.status_code not in (200, 206):
            return jsonify({"code": "502", "message": "音频源不可用，请刷新重试"}), 502

        content_type = resp.headers.get("Content-Type", "audio/mpeg")
        # 确保 content_type 正确
        if "audio" not in content_type and "octet" not in content_type:
            content_type = "audio/mpeg"

        content_length = resp.headers.get("Content-Length")
        accept_ranges = resp.headers.get("Accept-Ranges", "bytes")

        def generate():
            for chunk in resp.iter_content(8192):
                if chunk:
                    yield chunk

        resp_headers = {
            "Cache-Control": "no-cache",
            "Accept-Ranges": accept_ranges,
        }
        if content_length:
            resp_headers["Content-Length"] = content_length
        if resp.status_code == 206:
            resp_headers["Content-Range"] = resp.headers.get("Content-Range", "")

        return Response(
            stream_with_context(generate()),
            status=resp.status_code,
            content_type=content_type,
            headers=resp_headers,
        )
    except Exception as e:
        logger.error(f"音频代理失败: {e}")
        return jsonify({"code": "500", "message": f"音频代理失败: {str(e)}"}), 500


@music_bp.route("/api/song/<int:song_id>/favorite", methods=["POST"])
@login_required
def toggle_favorite(song_id):
    """标记/取消 喜欢歌曲"""
    user = get_current_user()
    song = Song.query.get_or_404(song_id)
    data = request.get_json() or {}
    like_status = data.get("like_status", 1)  # 1=喜欢, -1=不喜欢, 0=取消

    fav = Favorite.query.filter_by(user_id=user.id, song_id=song.id).first()
    if fav:
        if like_status == 0:
            db.session.delete(fav)
            msg = "已取消标记"
        else:
            fav.like_status = like_status
            msg = "喜欢" if like_status == 1 else "不喜欢"
    else:
        if like_status == 0:
            return jsonify({"code": "400", "message": "未标记过该歌曲"}), 400
        fav = Favorite(user_id=user.id, song_id=song.id, like_status=like_status)
        db.session.add(fav)
        msg = "喜欢" if like_status == 1 else "不喜欢"

    db.session.commit()
    return jsonify({"code": "200", "message": f"已标记为{msg}"})


@music_bp.route("/api/song/<int:song_id>/comments", methods=["GET"])
def api_comments(song_id):
    """获取歌曲评论"""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    pagination = Comment.query.filter_by(song_id=song_id).order_by(
        Comment.created_at.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "code": "200",
        "data": {
            "comments": [c.to_dict() for c in pagination.items],
            "total": pagination.total,
            "page": page,
        }
    })


@music_bp.route("/api/song/<int:song_id>/comments", methods=["POST"])
@login_required
def add_comment(song_id):
    """发表评论"""
    Song.query.get_or_404(song_id)
    user = get_current_user()
    data = request.get_json() or {}
    content = data.get("content", "").strip()

    if not content:
        return jsonify({"code": "400", "message": "评论内容不能为空"}), 400

    if len(content) > 500:
        return jsonify({"code": "400", "message": "评论不能超过500字"}), 400

    comment = Comment(user_id=user.id, song_id=song_id, content=content)
    db.session.add(comment)
    db.session.commit()

    return jsonify({"code": "200", "message": "评论成功", "data": comment.to_dict()})


@music_bp.route("/api/playlists", methods=["GET"])
@login_required
def api_playlists():
    """获取用户歌单列表"""
    user = get_current_user()
    playlists = Playlist.query.filter_by(user_id=user.id).all()
    return jsonify({"code": "200", "data": [p.to_dict() for p in playlists]})


@music_bp.route("/api/playlists", methods=["POST"])
@login_required
def create_playlist():
    """创建歌单"""
    user = get_current_user()
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    description = data.get("description", "")

    if not name:
        return jsonify({"code": "400", "message": "歌单名称不能为空"}), 400

    playlist = Playlist(user_id=user.id, name=name, description=description)
    db.session.add(playlist)
    db.session.commit()

    return jsonify({"code": "200", "message": "歌单创建成功", "data": playlist.to_dict()})


@music_bp.route("/api/playlist/<int:pid>/songs", methods=["POST"])
@login_required
def add_to_playlist(pid):
    """添加歌曲到歌单"""
    user = get_current_user()
    playlist = Playlist.query.filter_by(id=pid, user_id=user.id).first_or_404()
    data = request.get_json() or {}
    song_id = data.get("song_id")

    if not song_id:
        return jsonify({"code": "400", "message": "请选择歌曲"}), 400

    Song.query.get_or_404(song_id)

    existing = PlaylistSong.query.filter_by(playlist_id=pid, song_id=song_id).first()
    if existing:
        return jsonify({"code": "409", "message": "歌曲已在歌单中"}), 409

    ps = PlaylistSong(playlist_id=pid, song_id=song_id)
    db.session.add(ps)
    db.session.commit()

    return jsonify({"code": "200", "message": "已添加到歌单"})


@music_bp.route("/api/playlist/<int:pid>", methods=["GET"])
def api_playlist_detail(pid):
    """获取歌单详情"""
    playlist = Playlist.query.get_or_404(pid)
    songs = [ps.song.to_dict() for ps in playlist.songs.all() if ps.song]
    data = playlist.to_dict()
    data["songs"] = songs
    return jsonify({"code": "200", "data": data})


@music_bp.route("/api/genres", methods=["GET"])
def api_genres():
    """获取所有风格"""
    genres = db.session.query(Song.genre).filter(Song.genre != "").distinct().all()
    return jsonify({"code": "200", "data": [g[0] for g in genres]})


@music_bp.route("/api/update", methods=["POST"])
@login_required
def api_update_music():
    """手动触发更新音乐数据"""
    try:
        added, updated = update_all_music()
        return jsonify({"code": "200", "message": f"更新完成: 新增{added}首, 更新{updated}首"})
    except Exception as e:
        return jsonify({"code": "500", "message": f"更新失败: {str(e)}"}), 500
