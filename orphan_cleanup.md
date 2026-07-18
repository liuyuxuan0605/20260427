# 孤儿临时文件 7 天 TTL 启动清理 — 实现与知识点

> 配套 patch：`D:\newproject\orphan_cleanup.patch`（应用方式见文末）
> 改动文件：`MediaServer/include/clogic.h`、`MediaServer/src/clogic.cpp`

---

## 1. 背景与动机

断点续传 v2 在上传过程中会产生两类临时文件，与最终成品 `.flv` 同目录（`/home/colin/video/flv/<user>/`）：

| 后缀 | 作用 | 生命周期 |
|------|------|----------|
| `.part` | 真实数据块，边收边追加写 | 收到 END 且 MD5 校验通过 → `rename` 成正式 `.flv`；失败/超时 → 删除 |
| `.meta` | 续传点元数据（已收字节、MD5、路径等），每收一块 `rename` 重写 | 上传完成 → 删除 |

关键设计点：**客户端异常断连（不发 END 直接关 socket）时，服务端保留 `.part/.meta`**，供客户端后续断点续传（见 `OnClientDisconnect`，它只清内存里的 task + busy 锁，不删磁盘文件）。

但这就留下一个缺口：**如果客户端永远不再续传**，这些 `.part/.meta` 会成为孤儿，永久占磁盘。
本功能在 `CLogic` 构造（即 MediaServer 启动）时做一次兜底扫描：把**超过 7 天没有任何活跃写入**的孤儿 `.part/.meta` 清掉。

---

## 2. 改了什么

### `clogic.h`
1. 构造函数里挂上启动钩子：
   ```cpp
   CLogic( TcpKernel* pkernel )
   {
       m_pKernel = pkernel;
       m_sql = pkernel->m_sql;
       m_tcp = pkernel->m_tcp;
       CleanupOrphanTempFiles(); // 启动扫描：清理超过 7 天的孤儿 .part/.meta
   }
   ```
2. private 区新增声明：
   ```cpp
   void CleanupOrphanTempFiles(); // 启动扫描：清理超期孤儿 .part/.meta
   ```

### `clogic.cpp`
1. 顶部新增 4 个 POSIX 头：
   ```cpp
   #include <dirent.h>    // DIR / opendir / readdir / closedir
   #include <sys/stat.h>  // struct stat / stat()
   #include <unistd.h>    // access() / remove()
   #include <cstring>     // strlen() / strcmp()
   ```
2. 新增两个函数：`TryRemoveSibling()`（配对删兄弟）+ `CleanupOrphanTempFiles()`（主扫描）。

---

## 3. 核心判据：为什么用 mtime 是靠谱的

`stat()` 拿到的 `st_mtime` 是文件**最后一次被修改写入**的时间。本设计里：

- **`.meta`**：每收到一个数据块就调用 `WriteMeta()`，内部是「写 `.meta.tmp` → `rename` 成 `.meta`」。POSIX 下 `rename` 会刷新目标文件的 mtime，所以 `.meta` 的 mtime = 最近一次收块时刻。
- **`.part`**：用 `fopen(..., "ab"/"wb")` + `fwrite` 追加数据，写入操作本身就会刷新 mtime。

结论：**`.part` / `.meta` 的 mtime 就是「最后一次活跃（收数据）时间」**。一旦超过 7 天没被写，说明对应上传早已放弃/断连未续传，可以安全判定为孤儿。

> 成品 `.flv` 不会被本扫描触碰（见 4.2 后缀白名单），所以不存在误删风险。

---

## 4. 实现要点

### 4.1 目录遍历结构
```
RootPath + "flv/"                  // = /home/colin/video/flv/
   └─ <每个用户名子目录>/
         └─ 遍历该目录下所有条目
```
只下钻**一层**（用户目录），在用户目录内枚举文件。用户名目录由注册时 `mkdir(flv/<user>/)` 创建。

### 4.2 后缀白名单（防误删的硬约束）
只处理以 `.meta` / `.part` 结尾的文件，**其他一律跳过**：
```cpp
bool isMeta = (nl > 5 && strcmp(name + nl - 5, ".meta") == 0);
bool isPart = (nl > 5 && strcmp(name + nl - 5, ".part") == 0);
if (!isMeta && !isPart) continue;   // 跳过 .flv / .gif / 其他任何文件
```
这一行是整个功能的安全底线：即使目录里混入了重要成品，也绝不会被本函数删除。

### 4.3 配对删除（不留半个孤儿）
`.part` 和 `.meta` 是成对出现的。删其中一个时，必须连带删同名的另一个，否则会留下「半个孤儿」：
```cpp
static void TryRemoveSibling(const char* path, bool isMeta)
{
    std::string s(path);
    if (s.size() <= 5) return;
    s.resize(s.size() - 5);                  // 去掉 ".meta" / ".part"
    s += (isMeta ? ".part" : ".meta");       // 换上兄弟后缀
    if (access(s.c_str(), F_OK) == 0) {
        if (ORPHAN_DRYRUN) { /* 只报告 */ }
        else if (remove(s.c_str()) == 0) { /* 真删 */ }
    }
}
```
遍历到 `.meta` 时删 `.meta` + 同名 `.part`；遍历到 `.part` 时删 `.part` + 同名 `.meta`。两者用 `access()` 互检存在性，删过的不会重复删、不存在的不会报错炸日志。

### 4.4 阈值与 dry-run 安全开关
```cpp
#ifndef ORPHAN_TTL_DAYS
#define ORPHAN_TTL_DAYS 7      // 超 7 天未活跃 = 孤儿
#endif
#ifndef ORPHAN_DRYRUN
#define ORPHAN_DRYRUN 1        // 1 = 只统计不删（默认安全）；改为 0 才真删
#endif
```
- 阈值：7 天 = `7 * 24 * 3600 = 604800` 秒。
- **默认值 `ORPHAN_DRYRUN 1`**：首次上线只打印「会删哪些」，不动任何文件。你确认日志列出的都是该删的孤儿后，把宏改成 `0` 重新编译，才真正删除。这是防手抖误删的双保险。

---

## 5. 关键知识点（POSIX / 文件系统）

| API | 头文件 | 作用 |
|-----|--------|------|
| `DIR*`, `opendir()`, `readdir()`, `closedir()` | `<dirent.h>` | 打开/遍历/关闭目录；`readdir` 返回 `struct dirent`，其中 `d_name` 是文件名 |
| `struct stat`, `stat(path, &st)` | `<sys/stat.h>` | 取文件元数据；`st.st_mtime` 是最后修改时间（epoch 秒） |
| `access(path, F_OK)` | `<unistd.h>` | 判断文件是否存在 |
| `remove(path)` | `<stdio.h>`（`<unistd.h>` 也声明） | 删除文件（内部 `unlink`） |
| `time(NULL)` | `<time.h>` | 当前 epoch 秒 |

**几个易错/易混的点：**

1. **epoch 秒无时区坑**：`time(NULL)` 和 `stat().st_mtime` 都是「自 1970-01-01 UTC 起的秒数」，相减即得年龄，不涉及时区转换，不会算错。
2. **`rename` 会刷新 mtime**：这是 `.meta` 判据成立的前提。如果是「原地覆盖写」而不是 rename，mtime 同样会刷新，结论不变。
3. **追加写（append/`fwrite`）会刷新 mtime**：这是 `.part` 判据成立的前提。
4. **只删明确后缀**：永远用白名单（`*.meta`/`*.part`），绝不用黑名单（「删除了 `.flv` 之外的」），黑名单一旦漏判就会误删。
5. **跳过 `.` 和 `..`**：`readdir` 会把当前目录（`.`）和上级目录（`..`）也枚举出来，必须以 `d_name[0] == '.'` 跳过，否则可能越界到别的目录。
6. **`DIR*` 必须 `closedir`**：每个 `opendir` 都要配对 `closedir`，否则 fd 泄漏。

---

## 6. 如何启用

1. 用 patch 应用到 Linux 源码（见文末）。
2. **第一阶段（确认）**：保持 `ORPHAN_DRYRUN 1`，编译运行，看启动日志 `[ORPHAN-CLEAN]`，确认 `stale` 列出的都是真孤儿。
3. **第二阶段（生效）**：把 `clogic.cpp` 里的 `ORPHAN_DRYRUN` 改成 `0`，重新编译，真正删除。
4. 阈值想调（比如改 3 天 / 15 天），改 `ORPHAN_TTL_DAYS` 宏即可。

---

## 7. 如何验证

### 7.1 造现场（在 Linux 服务端）
```bash
cd /home/colin/video/flv/<随便一个用户目录>/

# 造 3 个 200 天前的（预期被删）
touch -d "2026-01-01 00:00:00" zz_stale1.meta
touch -d "2026-01-01 00:00:00" zz_stale1.part
touch -d "2026-01-01 00:00:00" zz_stale2.meta

# 造 1 个现在的（预期保留）
touch zz_fresh1.meta

# 旁边放个正常成品，验证绝不被误删
touch zz_normal.flv

# 重启 MediaServer，看日志，再 ls 确认：
ls zz_*
# 预期：zz_stale1.* 和 zz_stale2.meta 消失；zz_fresh1.meta、zz_normal.flv 还在
```

### 7.2 日志解读（DRY-RUN 阶段）
```
[ORPHAN-CLEAN] start, dir=/home/colin/video/flv/, threshold=7 days (DRY-RUN)
[ORPHAN-CLEAN] stale(meta, age=195 d): /home/colin/video/flv/alice/zz_stale1.meta
[ORPHAN-CLEAN]   (dry-run) sibling would be removed: /home/colin/video/flv/alice/zz_stale1.part
[ORPHAN-CLEAN] stale(part, age=195 d): /home/colin/video/flv/alice/zz_stale1.part
[ORPHAN-CLEAN] done. candidates=5, stale=3, kept=2, removed=0 (DRY-RUN, nothing deleted)
```
`stale=3` 且都是 `zz_stale*`、`kept=2` 是 `zz_fresh1.meta` + `zz_normal.flv`，说明判据正确、且 `.flv` 不在删除范围。把 `ORPHAN_DRYRUN` 改成 `0` 后，`removed` 会变成 3。

---

## 8. 与 v2 断点续传的关系

- **运行中**的上传：活跃文件的 mtime 是几秒前，永远不会被本扫描判为 stale；而且本扫描**只在启动时跑一次**，运行期间新产生的 `.part` 不在扫描范围内（如需周期性清理，可后续挂定时线程，本版未做）。
- **断连未续传**：`OnClientDisconnect` 保留 `.part/.meta` → 若 7 天内客户端回来续传，正常继续；若超过 7 天没回来，下次启动被本扫描清理。
- 二者配合形成闭环：**断连可续传、久不续传自动回收**。

---

## 9. 边界与注意

- 仅启动时执行一次，没有定时线程（如需 24h 周期清理可另加 `std::thread` + `sleep`，不在本次范围）。
- 只下钻一层用户目录；用户名目录不存在/无权限时 `opendir` 失败会被 `continue` 跳过，不影响其他用户。
- `RootPath` 宏当前为 `/home/colin/video/`（来自 `clogic.cpp` 第 115 行），若你换部署路径需同步修改此处，扫描自动跟随。
- 删除用 `remove()`（内部 `unlink`），只删普通文件，不会误删目录。

---

## 10. 应用 patch

把 `orphan_cleanup.patch` 拷到 Linux 的 `MediaServer/` 源码根目录（即包含 `src/`、`include/` 的那一层），执行：

```bash
cd <MediaServer 源码根目录>
patch -p1 < orphan_cleanup.patch
# 确认无 rejected 后编译
g++ -std=c++11 ...   # 沿用你现有的编译命令
```

`patch -p1` 会把 `a/src/clogic.cpp` → `src/clogic.cpp` 应用。应用后先以 `ORPHAN_DRYRUN 1` 跑一次验证，再决定是否开 `0`。

---

## 11. 关联决策记录（2026-07-15）

- **秒传写库（instant 路径写 `t_VideoInfo`）：经用户决策不做。** 原因为避免"多用户传同一文件→磁盘只存一份、双方列表可见"的跨用户共享语义，优先保持权限边界简单。
- 已知边界（用户已接受）：同一用户重传自己已传过的文件时，秒传命中后其"我的视频"列表不出现新条目、最新上架榜不因该次秒传刷新。
- 至此断点续传 v2 全部收尾：busy(4) 断连锁泄漏修复（已验证 PASS）+ 客户端 DEBUG 临时改动回退 + 孤儿 TTL 清理（本文件）。
