# 断点续传 v2 — 客户端实现自检报告（IS_PASS）

> 本次会话：**客户端** `MediaPlay-live/MediaPlay/onlinedialog.cpp` + `onlinedialog.h` 的 v2 上传改造。
> 服务端 `MediaServer-live` 的 v2 逻辑（packdef.h / clogic.h / clogic.cpp）已于上一会话落地，本会话仅做契约对齐复核。

## 一、本次改了什么（客户端）

| 文件 | 改动 |
|------|------|
| `onlinedialog.h` | 增加 `#include <QMutex>/<QWaitCondition>/<atomic>`；信号 `SIG_UploadMessage`；槽 `slot_ShowUploadMessage`；`CancelUpload()`；私有成员 `m_v2Mutex / m_v2HsCond / m_v2HsPending / m_v2HsRs / m_v2EndCond / m_v2EndPending / m_v2EndRs / m_uploadCanceled` |
| `onlinedialog.cpp` | 新增静态 `ComputeFileContentMd5()`（算整文件原始 MD5，不带盐）；构造函数连接 `SIG_UploadMessage`；实现 `slot_ShowUploadMessage` / `CancelUpload`；**重写 `UploadFile` 为 v2 流程**；`slot_ReadyData` 增加 `UPLOAD_V2_RS` / `UPLOAD_END_RS` 两个分支 |

> 旧的上传路径（`STRU_UPLOAD_RQ` / `STRU_FILEBLOCK_RQ` 发送）已**完全移除**；仅下载收包 `slot_fileblockrq` 仍保留 `STRU_FILEBLOCK_RQ`（与上传无关）。

## 二、新 `UploadFile` 流程（v2）

1. 校验文件非空、`m_id>0`。
2. `ComputeFileContentMd5(filePath)` → 32 位小写 hex（**整文件**，与续传偏移无关）。
3. 填 `STRU_UPLOAD_V2_RQ`（m_UserId / m_nFileSize / m_szMd5 / 文件名 / gif名 / 扩展名 / hobby），**在 `m_v2Mutex` 锁内置位 pending 并发送握手**（杜绝 RS 早于 wait 的丢唤醒）。
4. `QWaitCondition::wait(15000ms)` 等 GUI 线程 `slot_ReadyData` 唤醒 → 取 `STRU_UPLOAD_V2_RS`：
   - `fail` → 提示；`busy` → 提示；`instant`(秒传) → 进度满 + 提示返回。
   - `new`/`resume` → 取 `taskId` 与 `resumeFrom`（续传偏移）。
5. 打开源文件 `seek(resumeFrom)`，循环 `read ≤ 64KB` → 变长发送 `[STRU_UPLOAD_BLOCK 头][payload]`；每块 `offset/len` 正确；`SendData<=0` 判网络中断即停（不发 END，服务端保留 `.part` 续传）。
6. 发 `STRU_UPLOAD_END_RQ`（锁内发送）→ 等 `UPLOAD_END_RS`(30s)。
7. `result==1` → 上传成功；否则 → 校验失败提示。

## 三、协议两端对齐核对（IS_PASS 关键项）

| 项 | 客户端 | 服务端 | 结论 |
|----|--------|--------|------|
| 握手校验 | `strlen(m_szMd5)==32`、小写 hex | `UploadV2Rq` 要求 `m_UserId>0 && m_nFileSize>0 && strlen(m_szMd5)==32` | ✅ 一致 |
| 块长度 | `0 < m_nLen ≤ 64KB` | `UploadBlockRqV2` 拒绝 `m_nLen<=0 || > _DEF_CONTENT_SIZE` | ✅ 一致（最后一块必 >0，因 fileSize>0） |
| 变长线上格式 | `[STRU_UPLOAD_BLOCK 头][payload]` | `payload = szbuf + sizeof(STRU_UPLOAD_BLOCK)` | ✅ 一致（SendData 自动加 4 字节长度前缀） |
| 续传偏移 | `seek(rs.m_nResumeFrom)` | `resumeFrom = m_nReceived`（meta 权威） | ✅ 一致 |
| offset 校验 | 从 `resumeFrom` 顺序发 | 仅拒 `offset > received`，允许 `≤` 幂等覆盖 | ✅ 一致（重传不翻倍） |
| 终验 | 发 `END_RQ`(带 md5) → 等 `END_RS` | `FinalizeUpload` 对整份 `.part` 算 MD5 比对存储指纹 | ✅ 一致 |
| 结构体 packing | `#pragma pack(push,1)` 内定义 | 同 | ✅ 二进制布局一致 |
| 结果宏 | `upload_v2_new/resume/instant/busy/fail` | 同宏 | ✅ 一致 |

## 四、竞态 / 边界正确性自检

- **丢唤醒竞态**：`m_v2HsPending=true` 与 `SendData` 同处 `m_v2Mutex` 临界区，`wait` 释放锁；`slot_ReadyData` 须持同锁才能写入并 `wakeOne` → 不存在 RS 早于 wait 的丢失。
- **线程模型**：`UploadFile` 在 `UploadWork` worker 线程；收包在 GUI 线程经 `SIG_ReadyData`(QueuedConnection) 投递 → worker 阻塞等待、GUI 唤醒，模型成立。
- **串行上传**：`slot_UploadFile` 顺序调用 `UploadFile(封面)` 再 `UploadFile(视频)`，worker 内一次只跑一个 → `m_v2HsPending / m_v2EndPending` 单旗不会并发撞。
- **GUI 安全**：worker 内禁止直接 `QMessageBox`，统一经 `SIG_UploadMessage` 转 GUI 线程弹窗。
- **原子取消**：`m_uploadCanceled` 为 `std::atomic<bool>`，`CancelUpload()` 置位即中断且不发 END → 服务端保留 `.part`/`.meta`，下次同 md5 续传。
- **网络中断**：`SendData<=0` 即停、不发 END，避免死 socket 上自旋 30s。
- **Hobby 拷贝**：`Hobby` 为 8×char（`sizeof==8`），`memcpy(...,sizeof(hy))` 写入 `m_szHobby[8]` 无溢出（与旧代码一致）。
- **MD5 函数**：`GetMD5` 带盐仅用于密码；文件指纹用新 `ComputeFileContentMd5`（无盐、整文件、小写），不与密码哈希混淆。

## 五、服务端侧已完成项（recap，供编译 Linux 时一并带走）

- `packdef.h`（服务端）：`_DEF_CONTENT_SIZE→64*1024`；`FileInfo` 扩展 v2 字段；v2 包类型与结构体（与客户端字节级对齐）。
- `clogic.h`：`mutex/map/方法声明/v2 字段`。
- `clogic.cpp`：`Md5OfFile/WriteMeta/ReadMeta/CopyFileRaw`；v2 分发注册；`UploadV2Rq / UploadBlockRqV2 / UploadEndRqV2 / FinalizeUpload / CleanupTask`；`close()` 清理新 map。

## 六、编译 / 验证建议

- **客户端（Windows / MSVC）**：`MediaPlay-live` 直接打开 `.pro`/`.sln` 编译。重点验证：上传进度条、断网后续传（同文件二次上传应 `resume`）、秒传（同 md5 二次上传）、取消后保留续传。
- **服务端（Linux）**：把 `MediaServer-live` 三个文件拷到 Linux 编译运行。
- **端到端自测脚本（建议）**：上传 50% → 杀客户端 → 重新上传同文件 → 断言服务端终校 MD5 一致、文件完整。

## 七、已知限制 / 后续项

- 客户端无"取消"按钮 UI，`CancelUpload()` 已就绪，待接入上传对话框按钮或关闭事件。
- 孤儿 `.part/.meta` 的 7 天 TTL 启动扫描清理尚未在客户端/服务端补齐（服务端有 30min 占用超时释放，已覆盖主要场景）。
- 压测客户端 `stress_test_client.cpp`（50%→kill→续传→断言 MD5）尚未编写，建议下个任务补齐。
