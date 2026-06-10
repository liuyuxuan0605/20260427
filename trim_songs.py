# -*- coding: utf-8 -*-
"""精简歌曲库：只保留热度Top300首歌曲，删除其余歌曲和封面"""
import os
import sys
import sqlite3
import pymysql

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "music.db")
COVERS_DIR = os.path.join(BASE_DIR, "data", "covers")

MYSQL_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "258958asd",
    "database": "music_player",
    "charset": "utf8mb4",
}


def main():
    # ===== 第1步：SQLite - 确定保留的300首歌 =====
    sconn = sqlite3.connect(DB_PATH)
    sc = sconn.cursor()

    total = sc.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    print(f"当前歌曲总数: {total}")

    # 按热度降序取Top300
    top300 = sc.execute(
        "SELECT id, local_cover FROM songs ORDER BY hot_score DESC LIMIT 300"
    ).fetchall()
    keep_ids = set(t[0] for t in top300)
    print(f"保留歌曲数: {len(keep_ids)}")

    # 收集保留歌曲的封面文件名
    keep_cover_files = set()
    for song_id, local_cover in top300:
        if local_cover:
            fname = os.path.basename(local_cover)
            keep_cover_files.add(fname)
    print(f"保留封面数: {len(keep_cover_files)}")

    # ===== 第2步：MySQL - 清理引用了已删除歌曲的数据 =====
    mconn = pymysql.connect(**MYSQL_CONFIG)
    mc = mconn.cursor()

    # 构建 IN 子句的值列表
    keep_ids_list = sorted(keep_ids)
    placeholders = ",".join(["%s"] * len(keep_ids_list))

    # 清理 favorites
    mc.execute(
        f"DELETE FROM favorites WHERE song_id NOT IN ({placeholders})",
        keep_ids_list,
    )
    fav_del = mc.rowcount
    print(f"清理收藏: 删除{fav_del}条")

    # 清理 comments
    mc.execute(
        f"DELETE FROM comments WHERE song_id NOT IN ({placeholders})",
        keep_ids_list,
    )
    com_del = mc.rowcount
    print(f"清理评论: 删除{com_del}条")

    # 清理 playlist_songs
    mc.execute(
        f"DELETE FROM playlist_songs WHERE song_id NOT IN ({placeholders})",
        keep_ids_list,
    )
    ps_del = mc.rowcount
    print(f"清理歌单歌曲: 删除{ps_del}条")

    mconn.commit()
    mconn.close()

    # ===== 第3步：SQLite - 删除多余歌曲 =====
    del_count = sc.execute(
        f"SELECT COUNT(*) FROM songs WHERE id NOT IN ({','.join('?' for _ in keep_ids_list)})",
        keep_ids_list,
    ).fetchone()[0]
    print(f"将删除歌曲: {del_count}首")

    sc.execute(
        f"DELETE FROM songs WHERE id NOT IN ({','.join('?' for _ in keep_ids_list)})",
        keep_ids_list,
    )
    print(f"SQLite: 已删除{sc.rowcount}首歌曲")

    # 清理 update_logs 表（可选保留）
    # sc.execute("DELETE FROM update_logs")

    sconn.commit()

    # 验证
    remaining = sc.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    print(f"剩余歌曲: {remaining}首")
    sconn.close()

    # ===== 第4步：删除多余封面文件 =====
    all_covers = set(os.listdir(COVERS_DIR))
    to_del_covers = all_covers - keep_cover_files
    print(f"封面文件总数: {len(all_covers)}")
    print(f"需删除封面: {len(to_del_covers)}个")

    deleted_count = 0
    for fname in to_del_covers:
        fpath = os.path.join(COVERS_DIR, fname)
        try:
            os.remove(fpath)
            deleted_count += 1
        except Exception as e:
            print(f"删除封面失败: {fname} - {e}")

    print(f"已删除封面: {deleted_count}个")
    remaining_covers = len(os.listdir(COVERS_DIR))
    print(f"剩余封面: {remaining_covers}个")

    # ===== 第5步：VACUUM SQLite 压缩数据库 =====
    sconn = sqlite3.connect(DB_PATH)
    sconn.execute("VACUUM")
    sconn.close()
    print("SQLite VACUUM完成，数据库已压缩")

    db_size = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"数据库大小: {db_size:.1f}MB")

    print("\n✅ 精简完成！保留300首歌曲 + 对应封面")


if __name__ == "__main__":
    main()
