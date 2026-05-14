# -*- coding: utf-8 -*-
"""Flask 主应用"""
import os
import sys
import logging
import warnings

# Windows UTF-8 编码环境（必须在所有 import 之前）
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore")

# 确保项目根目录在 sys.path 中
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from flask import Flask, send_from_directory
from flask_apscheduler import APScheduler

from config import SQLALCHEMY_DATABASE_URI, SECRET_KEY, DATA_DIR, COVERS_DIR
from models.db import db
from routes.auth import auth_bp
from routes.music import music_bp
from routes.user import user_bp
from crawler.music_crawler import update_all_music, needs_update

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app():
    """创建 Flask 应用"""
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # 配置
    app.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = SECRET_KEY

    # APScheduler 配置
    app.config["SCHEDULER_API_ENABLED"] = True
    scheduler = APScheduler()

    # 初始化扩展
    db.init_app(app)
    scheduler.init_app(app)

    # 注册蓝图
    app.register_blueprint(auth_bp)
    app.register_blueprint(music_bp)
    app.register_blueprint(user_bp)

    # 确保数据目录存在
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(COVERS_DIR, exist_ok=True)

    # 创建数据库表
    with app.app_context():
        db.create_all()
        logger.info("数据库表已创建")

        # 启动时检查是否需要更新数据
        if needs_update():
            logger.info("检测到数据需要更新，开始更新...")
            try:
                added, updated = update_all_music()
                logger.info(f"启动更新完成: 新增{added}首, 更新{updated}首")
            except Exception as e:
                logger.error(f"启动更新失败: {e}")
        else:
            logger.info("数据已是最新，无需更新")

    # 每日0点定时更新
    @scheduler.task("cron", id="update_music", hour=0, minute=0)
    def scheduled_update():
        with app.app_context():
            logger.info("定时任务: 开始更新音乐数据...")
            try:
                added, updated = update_all_music()
                logger.info(f"定时更新完成: 新增{added}首, 更新{updated}首")
            except Exception as e:
                logger.error(f"定时更新失败: {e}")

    scheduler.start()

    # 封面图片静态路由
    @app.route("/data/covers/<path:filename>")
    def serve_cover(filename):
        return send_from_directory(COVERS_DIR, filename)

    # 错误处理
    @app.errorhandler(404)
    def not_found(e):
        return send_from_directory("templates", "404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return send_from_directory("templates", "500.html"), 500

    return app


if __name__ == "__main__":
    app = create_app()
    logger.info("🎵 音乐推荐播放器启动！")
    logger.info("📍 访问地址: http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
