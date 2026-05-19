# -*- coding: utf-8 -*-
"""为现有歌曲设置音乐类型(genre)分类"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'music.db')

# ========== 歌手→类型映射 ==========
# 根据歌手的主要风格分类
ARTIST_GENRE = {
    # ---- 说唱 ----
    "GAI周延": "说唱", "KEY.L刘聪": "说唱", "功夫胖KUNGFU-PEN": "说唱",
    "盛宇D-SHINE": "说唱", "布瑞吉Bridge": "说唱", "法老": "说唱",
    "王以太": "说唱", "艾热 AIR": "说唱", "谢帝": "说唱",
    "杨和苏KeyNG": "说唱", "PSY.P": "说唱",
    "C-BLOCK": "说唱",

    # ---- 摇滚 ----
    "Beyond": "摇滚", "五月天": "摇滚", "逃跑计划": "摇滚",
    "八三夭": "摇滚", "Coldplay": "摇滚", "伍佰": "摇滚",

    # ---- 民谣 ----
    "赵雷": "民谣", "宋冬野": "民谣", "陈鸿宇": "民谣",
    "花粥": "民谣", "马頔": "民谣", "好妹妹乐队": "民谣",
    "房东的猫": "民谣", "陈绮贞": "民谣", "程响": "民谣",
    "朴树": "民谣", "许巍": "民谣",

    # ---- R&B ----
    "陶喆": "R&B", "方大同": "R&B", "裘德": "R&B",
    "黄丽玲": "R&B", "魏如萱": "R&B", "萧煌奇": "R&B",
    "戴佩妮": "R&B",

    # ---- 流行（默认） ----
    "周杰伦": "流行", "林俊杰": "流行", "陈奕迅": "流行", "薛之谦": "流行",
    "王菲": "流行", "张学友": "流行", "李荣浩": "流行", "许嵩": "流行",
    "汪苏泷": "流行", "毛不易": "流行", "周深": "流行", "单依纯": "流行",
    "田馥甄": "流行", "蔡依林": "流行", "张惠妹": "流行", "萧敬腾": "流行",
    "李健": "流行", "徐佳莹": "流行", "张靓颖": "流行", "邓紫棋": "流行",
    "G.E.M.邓紫棋": "流行", "杨宗纬": "流行", "梁静茹": "流行",
    "刘若英": "流行", "莫文蔚": "流行", "蔡健雅": "流行",
    "孙燕姿": "流行", "张韶涵": "流行", "范玮琪": "流行",
    "华晨宇": "流行", "李宇春": "流行", "易烊千玺": "流行",
    "王俊凯": "流行", "王源": "流行", "王一博": "流行",
    "王力宏": "流行", "潘玮柏": "流行", "吴青峰": "流行",
    "萧煌奇": "流行", "胡彦斌": "流行", "张杰": "流行",
    "颜人中": "流行", "阿肆": "流行", "任然": "流行",
    "王贰浪": "流行", "王赫野": "流行", "阿冗": "流行",
    "容祖儿": "流行", "李克勤": "流行", "张敬轩": "流行",
    "李宗盛": "流行", "周华健": "流行", "罗大佑": "流行",
    "张信哲": "流行", "刘欢": "流行", "韩红": "流行",
    "谭维维": "流行", "郁可唯": "流行", "黄霄雲": "流行",
    "光良": "流行", "任贤齐": "流行", "张雨生": "流行",
    "鹿晗": "流行", "筷子兄弟": "流行", "凤凰传奇": "流行",
    "徐良": "流行", "杨坤": "流行", "杨千嬅": "流行",
    "谭咏麟": "流行",

    # ---- 外语流行 ----
    "Taylor Swift": "流行", "Adele": "流行", "Ed Sheeran": "流行",
    "Bruno Mars": "流行", "Maroon 5": "流行", "Mariah Carey": "流行",
    "Gracie Abrams": "流行", "Coldplay": "摇滚",

    # ---- 电子 ----
    "Tobu": "电子", "Martin Garrix": "电子", "Aqua": "流行",

    # ---- 游戏原声 ----
    "HOYO-MiX": "游戏原声",

    # ---- 其他 ----
    "F.I.R.": "摇滚", "谢安琪": "流行", "麦浚龙": "流行",
    "陈粒": "民谣", "陈淑桦": "流行", "成龙": "流行",
    "东北周杰伦": "流行", "广科宋冬野": "民谣",
    "沈腾": "流行", "韩磊": "流行",
    "好妹妹乐队 / 孟庭苇": "民谣", "好妹妹乐队 / 小娟&山谷里的居民": "民谣",
    "岳云鹏 / 好妹妹乐队": "民谣",
}

# 歌名关键词→类型映射（覆盖歌手分类）
NAME_GENRE_PATTERNS = {
    "说唱": ["rap", "hip-hop", "hiphop", "freestyle", "diss"],
    "摇滚": ["rock", "band", "乐队版"],
    "电子": ["remix", "edm", "dj ", "mix", "电音"],
    "R&B": ["r&b", "rnb", "soul"],
}


def classify_song(name, artist):
    """根据歌手和歌名判断类型"""
    # 1. 先尝试精确匹配歌手
    if artist in ARTIST_GENRE:
        return ARTIST_GENRE[artist]

    # 2. 尝试歌手中包含已知歌手名（如 "GAI周延 / 功夫胖KUNGFU-PEN"）
    for known_artist, genre in ARTIST_GENRE.items():
        if known_artist in artist:
            return genre
    # 分割多歌手
    parts = artist.replace("/", " ").replace("、", " ").replace("&", " ").split()
    for part in parts:
        part = part.strip()
        if part in ARTIST_GENRE:
            return ARTIST_GENRE[part]

    # 3. 根据歌名关键词判断
    name_lower = name.lower()
    for genre, keywords in NAME_GENRE_PATTERNS.items():
        for kw in keywords:
            if kw in name_lower:
                return genre

    # 4. 默认：流行
    return "流行"


def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 获取所有没有genre的歌曲
    rows = c.execute("SELECT id, name, artist, genre FROM songs").fetchall()
    print(f"总歌曲数: {len(rows)}")

    updated = 0
    for row in rows:
        song_id, name, artist, genre = row
        if genre:  # 已有类型的不覆盖
            continue

        new_genre = classify_song(name, artist)
        c.execute("UPDATE songs SET genre = ? WHERE id = ?", (new_genre, song_id))
        updated += 1

    conn.commit()
    print(f"已更新 {updated} 首歌曲的类型")

    # 统计
    stats = c.execute("SELECT genre, COUNT(*) FROM songs GROUP BY genre ORDER BY COUNT(*) DESC").fetchall()
    print("\n类型统计:")
    for genre, count in stats:
        print(f"  {genre}: {count}")

    conn.close()


if __name__ == "__main__":
    main()
