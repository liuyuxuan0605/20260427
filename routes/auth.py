# -*- coding: utf-8 -*-
"""用户认证路由"""
from functools import wraps
from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template
from models.db import db, User

auth_bp = Blueprint("auth", __name__)


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"code": "401", "message": "请先登录"}), 401
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    """获取当前登录用户"""
    uid = session.get("user_id")
    if uid:
        return User.query.get(uid)
    return None


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """用户注册"""
    if request.method == "GET":
        return render_template("register.html")

    data = request.get_json() if request.is_json else request.form
    email = data.get("email", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    confirm = data.get("confirm_password", "")

    if not all([email, username, password, confirm]):
        return jsonify({"code": "400", "message": "请填写所有字段"}), 400

    if password != confirm:
        return jsonify({"code": "400", "message": "两次密码不一致"}), 400

    if len(password) < 6:
        return jsonify({"code": "400", "message": "密码至少6位"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"code": "409", "message": "邮箱已被注册"}), 409

    if User.query.filter_by(username=username).first():
        return jsonify({"code": "409", "message": "用户名已被占用"}), 409

    user = User(email=email, username=username, nickname=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    session["user_id"] = user.id
    return jsonify({"code": "200", "message": "注册成功", "data": user.to_dict()})


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """用户登录"""
    if request.method == "GET":
        return render_template("login.html")

    data = request.get_json() if request.is_json else request.form
    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"code": "400", "message": "请输入邮箱和密码"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"code": "401", "message": "邮箱或密码错误"}), 401

    session["user_id"] = user.id
    return jsonify({"code": "200", "message": "登录成功", "data": user.to_dict()})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """退出登录"""
    session.pop("user_id", None)
    return jsonify({"code": "200", "message": "已退出登录"})


@auth_bp.route("/api/user/info", methods=["GET"])
@login_required
def user_info():
    """获取当前用户信息"""
    user = get_current_user()
    return jsonify({"code": "200", "data": user.to_dict()})


@auth_bp.route("/api/user/profile", methods=["POST"])
@login_required
def update_profile():
    """更新个人信息"""
    user = get_current_user()
    data = request.get_json() if request.is_json else request.form

    nickname = data.get("nickname", "").strip()
    if nickname:
        user.nickname = nickname

    new_password = data.get("new_password", "")
    old_password = data.get("old_password", "")
    if new_password:
        if not old_password or not user.check_password(old_password):
            return jsonify({"code": "400", "message": "原密码不正确"}), 400
        if len(new_password) < 6:
            return jsonify({"code": "400", "message": "新密码至少6位"}), 400
        user.set_password(new_password)

    avatar = data.get("avatar", "")
    if avatar:
        user.avatar = avatar

    db.session.commit()
    return jsonify({"code": "200", "message": "更新成功", "data": user.to_dict()})
