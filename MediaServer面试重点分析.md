# MediaServer-live / MediaPlay-live 面试重点分析（面试官视角）

> 阅读范围：服务端 `MediaServer-live`（Linux，epoll + 线程池 + MySQL）全部源码；客户端 `MediaPlay-live`（Qt + FFmpeg + SDL）网络层与解码管线。
> 结论先行：**服务端是面试金矿**。并发模型、协议边界、安全性、DB 瓶颈每一层都有"能让你眼睛一亮的问题"。下面按"危险度 / 含金量"排序。

---

## 一、Top 追问清单（按优先级）

> ⚠️ 2026-07 更新（第二轮全量代码走查）：标注【已修】的项已经在代码里修复，面试时从"发现→修复"角度讲，含金量更高；未标注的仍是真实存在的弱点，如实说明。
> 本轮新增了 4 个原文档漏掉、但**杀伤力很高**的问题（SIGPIPE 崩服务、大文件同步发送拖垮线程池、登录重复发包、老路径断连不清理），并修正了两处与代码对不上的口径（客户端 4 字节长度头其实**没**循环读满；"收不到包"的元类型结论已证伪）。

| 优先级 | 位置 | 一句话问题 | 严重程度 |
|---|---|---|---|
| 🔴 P0 | `block_epoll_net.cpp` SendData / main | `send()` **无 `MSG_NOSIGNAL`**，进程也没 `signal(SIGPIPE,SIG_IGN)`。客户端下载中途关窗，服务端下一次 send 收到 SIGPIPE → **默认动作终止进程，整个服务器挂掉**。（原文档完全没提，实际最容易复现的"必崩"点） | 进程崩溃（可远程触发） |
| 🔴 P0 | `clogic.cpp` DownloadRq / block_epoll_net recv_task | 大文件在 **worker 线程里同步 `fread`+`SendData` 一路发完**（`Buffer_Deal` 同步调用，`Producer_add` 被注释）。线程池最小才 10 线程，**10 个人同时下载就占满全部工作线程**，登录/心跳全部排队卡死。 | 并发被拖垮 / 伪死锁 |
| 🔴 P0 | `clogic.cpp` 各 Handler | 登录/上传/下载/直播请求只带 client 给的 `userId`，**无任何鉴权 / 会话**，能冒充任意用户。 | 越权 / 安全问题 |
| 🔴 P0 | `clogic.cpp` | `sprintf` 把用户名/文件名/直播标题直接拼进 SQL（**SQL 注入**，直播标题是最易被注入的自由文本）；`_DEF_SQLIEN` 只有 400 字节，长文件名拼 INSERT 有**栈溢出**风险。 | 安全 / 内存破坏 |
| 🔴 P0 | `clogic.cpp` 裸 map | 多线程下裸 `std::map`（`m_mapFileIDToFileInfo` / `m_mapIDToUserFD`）无锁，不同 fd 的上传/登录并发改红黑树结构 → 偶发崩溃。（v2 断点续传的新 map 都有 `m_v2Mutex`，是修复后的对照样板；v1 老 map 仍裸奔） | 并发崩溃 / 数据竞争 |
| 🟠 P1【已修】 | `clogic.cpp` LoginRq | ~~登录成功时响应包被发两次~~（成功分支内发一次 + 函数末尾无条件再发一次）→ 客户端弹**两次**"登录成功"。已删成功分支内的重复 `SendData`，统一由末尾发一次。**这是"handler 内部逻辑正确性"类 bug 的典型，值得作为"我怎么定位一个诡异现象"的故事讲。** | 协议逻辑 / 用户可见 |
| 🟠 P1 | `clogic.cpp` OnClientDisconnect | 断连清理只处理了 v2 的 `m_mapTaskIdToFileInfo`，**完全没清 v1 老路径的 `m_mapFileIDToFileInfo`** → 老上传传一半断线，`FileInfo` + 打开的 `FILE*` 永久泄漏。 | 资源泄漏 |
| 🟠 P1 | `Mysql.cpp` | 整个服务器只有**一个 `MYSQL*` 连接 + 一把全局锁**，所有 SQL 串行化，慢查询卡死全服务，无连接超时/重连（MySQL 8 小时空闲断开后全挂）。 | 性能瓶颈 |
| 🟠 P1 | `block_epoll_net.cpp` SendData | `send()` 不循环、不处理 `EAGAIN`、且**无锁**，注释却写"不可分割 避免多线程并发"，实际并没有锁；返回值是 `send` 原始返回值，调用方 `if(ret<0)` 判断配合阻塞 socket 基本不成立。 | 协议错乱 / 部分写丢数据 |
| 🟠 P1【已修】 | `block_epoll_net.cpp` recv_task | ~~服务端读 4 字节长度前缀无上界校验、无循环读满~~ → 已加：循环读满 4 字节 + `0 < nPackSize <= 10MB` 校验 + 半包丢弃。⚠️**口径修正：客户端 `TcpClient.cpp:164` 的 4 字节长度头其实是单次 `recv`、没循环读满**，只有包体循环读满 + 上界校验；别再说"客户端比服务端严谨"，准确说法是"两端各有各的短板"。 | 远程 OOM / 崩溃（DoS） |
| 🟠 P1【已修】 | `clogic.cpp` 各 Handler | ~~只有 DownloadRq 校验了 nlen~~ → 已加：`TCPKernel::DealData` 分发前按包类型查最小长度表（`GetMinPackLen`），短包/未注册类型统一丢弃。 | 远程内存破坏 |
| 🟡 P2 | `clogic.cpp` UploadBlockRq（v1 老路径） | `fwrite(rq->m_szFileContent, 1, rq->m_nBlockLen, pFile)` —— `m_nBlockLen` 客户端给的，谎报 > `_DEF_CONTENT_SIZE` 即堆越界读；且 `fopen` 返回值没检查，失败后 `fwrite(NULL)` 崩溃。（v2 路径 `UploadBlockRqV2` 有 `m_nLen` 校验，是对照） | 内存安全 |
| 🟡 P2 | `Thread_pool.cpp` | `thread_shutdown` 命名反直觉（TRUE=运行）；`pool_destroy` 只声明未实现；Manager 扩容 `pthread_create` 失败仍 `++thread_alive`；`(float)busy/alive` 有除零隐患。 | 设计 / 资源回收 |
| 🟢 P3 | 客户端 `videoplayer.cpp` | 解码用 `avcodec_decode_video2` / `avpicture_*` / `av_register_all` 等**已废弃 FFmpeg API**；每次音频帧都 `swr_alloc_set_opts`+`swr_init`+`swr_free`（应复用）。 | 技术陈旧 / 性能 |
| 🟢 P3 | `TCPKernel` 构造/析构 | 单例成员构造未初始化，`Open()` 失败路径下 `Close()` 会访问野指针 `m_logic`。 | 资源/生命周期 |

---

## 二、每个重点的展开（面试官会怎么问 + 期望回答）

### 0-A. SIGPIPE：一次 send 就能杀死整个服务器（P0，原文档漏项）
`main.cpp` 里没有 `signal(SIGPIPE, SIG_IGN)`，`block_epoll_net.cpp:275` 的 `send()` 也没带 `MSG_NOSIGNAL`。
- **失败场景**：客户端在下载视频/GIF 途中直接关窗（最常见的用户操作）。对端已发 RST/FIN，服务端还在 `DownloadRq` 的循环里往这个 socket `send`。向已关闭的连接写数据，内核给进程投递 `SIGPIPE`，**默认动作是终止进程**——不是这条连接断开，是**整个服务器进程死掉**，所有其他在线用户一起掉线。
- **为什么比"裸 map data race"更该优先讲**：data race 是偶发、依赖调度时序；SIGPIPE 是**确定性必现**，一个用户关个窗就能触发，属于"demo 现场就会炸"的级别。
- **修复**：`main` 开头 `signal(SIGPIPE, SIG_IGN)`（让 send 改为返回 -1 / errno=EPIPE，由代码自己处理断连），或每次 `send` 带 `MSG_NOSIGNAL` 标志。两者一起上最稳。
> 面试话术："我这个服务端有个当时没意识到的致命点——没屏蔽 SIGPIPE。客户端下载中途关窗，服务端往断开的连接 send 会收到 SIGPIPE 直接把进程杀了。这是'服务端要对客户端的任意断开免疫'的反例，正确做法是 IGN 掉 SIGPIPE 加 send 带 MSG_NOSIGNAL，把它降级成一个普通的 EPIPE 错误码来处理。"

### 0-B. 大文件同步发送把线程池"假死"（P0，原文档漏项）
`recv_task` 里 `Buffer_Deal((void*)buffer)` 是**同步调用**（本该二次投递线程池的 `Producer_add` 那行被注释掉了）。而 `DownloadRq`/`UploadHistoryRq` 是**在这个 worker 线程里同步把整个文件 `fread`+`SendData` 发完**才返回。
- **失败场景**：线程池 `Pool_create(200,10,50000)`——最小只有 **10** 个常驻线程。一个大视频阻塞发送可能耗时数秒~数十秒，占住一个 worker。**10 个用户同时点下载/刷首页，10 个最小线程全被文件传输占满**，此时新来的登录、直播信令、心跳全部在任务队列里排队，表现为"服务器没崩但所有人都卡住了"。Manager 线程 10 秒才扩容一次，救不及时。
- **修复**：把"读 socket 收包"和"业务处理（尤其大文件发送）"彻底拆开；文件发送要么走独立的发送线程/发送队列 + EPOLLOUT 驱动，要么至少给下载类任务单独的线程池，不能和信令类请求抢同一批 worker。
> 这题和下面第 7 节"接收/处理分离名不副实"是同一个根，但**后果要讲到"信令被文件传输饿死"这一层**，才算讲透。

### 0-C. 登录重复发包：一个"诡异现象→定位→修复"的完整故事（P1，已修）
**现象**：客户端每次登录成功都弹**两次**"登录成功"，但密码错误只弹一次。
**定位过程**（这个思考链本身就是加分项）：
1. 先怀疑客户端信号连重了（`connect` 被调了两次）——排查 `onlinedialog.cpp:88` 只连了一次，排除。
2. 再看是不是同一个包被处理两次——`slot_ReadyData` 里 `LOGIN_RS` 只分发一次，排除。
3. **关键观察：只有"成功"弹两次，"失败"弹一次**。这个不对称说明问题在**服务端按结果分支走了不同的发送次数**。
4. 翻 `clogic.cpp` 的 `LoginRq`：成功分支里 `SendData` 了一次，函数末尾又**无条件** `SendData` 了一次 → 成功走两次、失败只走末尾一次。完全吻合。
**修复**：删掉成功分支里的重复 `SendData`，让所有结果统一由函数末尾发一次。
> 面试价值：这类"响应包在多个 return/分支里发送次数不一致"的 bug，比架构级问题更能体现你**读代码的细致度和二分定位能力**。话术："我是从'为什么只有成功弹两次、失败不会'这个不对称现象反推的——不对称一定来自分支逻辑，最后定位到服务端成功分支多发了一个包。"

### 1. 并发模型：裸 map 是最大雷（P0）
`clogic.cpp` 里：
- `m_mapFileIDToFileInfo`（`std::map<int,FileInfo*>`）在 `UploadRq` 插入、`UploadBlockRq` 查找/删除；
- `m_mapIDToUserFD` 在 `LoginRq` 写入。

这些操作跑在**线程池 worker 线程**里（多个连接 → 多个线程）。`std::map` 的 insert/erase/find **不是线程安全的**——即使 key 不同，红黑树 rebalance 也会并发改结构，结果就是偶发 crash / 数据错乱。EPOLLONESHOT 只保证**同一个 fd** 不被两个线程同时处理，但**不同 fd 的不同上传**会并发改同一个 map。

> 面试官想听的：你立刻意识到这是 data race；改进是给 map 加 `mutex`，或把 `FileInfo` 生命周期放到 per-connection 对象里（fd→FileInfo 局部管理），或上传状态用 fd 维度隔离。

### 2. 分包边界：曾经服务端比客户端更"裸"（P0，已修复——面试讲"发现→修复"）
`recv_task` 原来的问题：
```cpp
nRelReadNum = read(ev->fd,&nPackSize,sizeof(nPackSize));   // 单次 read，没循环读满
...
pSzBuf = new char[nPackSize];                               // 没有上界检查
```
两处问题：
- **短读**：`read` 可能只返回 1~3 字节（信号中断 / 粘包），此时 `nPackSize` 只被部分填充，后面 `new char[半截值]` + 按错误长度 `recv` 全部乱掉。
- **无上界**：`nPackSize` 是客户端控制的 `int`，负数 → `new` 抛 `std::bad_alloc`；超大正数 → OOM 把服务器打挂。**客户端 `TcpClient::RecvData` 有 `if(nPackSize>_DEF_MAX_PACK_SIZE)` 校验，服务端却没有**——"服务端必须零信任客户端"的反例。

**修复后**（现状代码）：长度头循环读满 4 字节；`nPackSize` 校验 `(0, 10MB]`（`_DEF_MAX_PACK_SIZE` 与客户端一致），非法直接断连；包体没收满（对端中途断开的半包）不再送业务层。面试时可以完整讲这个"客户端有防护、服务端裸奔→对称加固"的排查与修复，比单纯背"应该怎么做"有说服力得多。仍可再进一步的点：长度字段没做 `ntohl`（当前两端同字节序，跨平台部署时要补）。

### 3. 安全：无鉴权 + 注入 + 溢出 + 路径穿越（P0/P1）
- **无鉴权**：`LiveStartRq` 用 `rq->m_nUserId` 直接当本人开播，`streamKey` 还是可预测的 `"user_%d"`；下载/上传同理。任何客户端都能冒充别人。
- **SQL 注入**：`sprintf(sqlBuf,"... where name='%s'", rq->user)` —— 用户名带单引号即可注入。正确做法：参数化 / `mysql_real_escape_string` / 白名单。
- **缓冲区溢出**：`sprintf(info->m_szRtmp,"//%s/%s", userName, gifName)` —— `m_szRtmp` 是 260，`userName(≤40)+"//"+gifName(≤260)` 明显可能溢出；`strcpy(info->m_szFileName, rq->m_szFileName)` 虽等长但属于"碰巧安全"。
- **路径穿越**：`fileName` 来自客户端，拼成 `/home/colin/video/flv/<user>/<fileName>`，发 `../../etc/xxx` 即可写任意路径。
- **弱哈希**：`sha256(salt+password)` 无迭代，DB 泄露可暴力。

> 期望回答：服务端把客户端输入当不可信；鉴权用登录后下发的 token/session；文件名用白名单 + 服务端重命名（UUID）；SQL 用预编译/转义。

### 4. 数据库：单连接串行化（P1）
`Mysql.cpp` 只有 `conn` 一个连接，所有 `SelectMysql/UpdataMysql` 都抢同一把 `m_lock`。这意味着**全服务器同时只能跑一条 SQL**。好处是线程安全，代价是：
- 200 个 worker 线程在 DB 前排大队，DB 成为绝对瓶颈；
- 一条慢查询 / 网络抖动会阻塞所有业务线程；
- 没有连接超时 / 断线重连，MySQL 8 小时空闲断开后全挂。

> 期望回答：引入**连接池 + 每线程/每任务借还连接**，或读写分离；给 `mysql_query` 设 `mysql_options(MYSQL_OPT_CONNECT_TIMEOUT / READ_TIMEOUT)`；连接失效做重连。

### 5. SendData：无锁 + 不处理部分写（P1）
```cpp
int res = send( fd, buf, nPackSize, 0 );   // 不循环，无 EAGAIN 处理，无锁
```
- 阻塞 socket 下大包可能只发一部分（`res < nPackSize`），剩余字节丢失 → 对端解包错位。
- 注释写"不可分割 避免多线程并发"，但**实际没有加锁**。两个 worker 给同一 client 并发 `send`（比如直播列表 + 其它回包）会字节交织，协议直接废。

> 期望回答：要么每个 fd 配一把发送锁（或单生产者发送线程/队列），要么把发送收敛到单一线程；`send` 要循环到发完或 `EAGAIN` 后挂 EPOLLOUT 续发。

### 6. 协议/强转越界（P1，已修复）
原问题：`DealData` 直接把 body 交给 handler，handler 里 `STRU_REGISTER_RQ* rq=(STRU_REGISTER_RQ*)szbuf;` 然后访问字段，只有 `DownloadRq` 校验了 `nlen`。

**修复后**：`TCPKernel.cpp` 新增 `GetMinPackLen(type)` 最小长度表，`DealData` 分发前统一校验 `nlen >= 最小长度`，短包/未注册的包类型（含客户端伪造 RS 类型）直接丢弃并打日志。变长包 `UPLOAD_BLOCK` 只校验定长头，payload 长度由 handler 里 `m_nLen` 自查兜底。遗留的深一层问题：长度够但**字段值造假**仍未全面拦截（如 v1 `UploadBlockRq` 的 `m_nBlockLen` 谎报越界读，见 P2 项），v2 路径已有字段校验，v1 老路径保留原样——面试可以讲"结构完整性校验（已做）"与"字段语义校验（v2 已做、v1 未做）"是两层不同的防线。

### 7. recv_task 的"接收/处理分离"名不副实（P2）
`recv_event` 用线程池派发 `recv_task`，但 `recv_task` 里：
```cpp
Buffer_Deal((void*) buffer);   // 直接同步调用，没有再丢给线程池
```
而 `Buffer_Deal` 里跑的正是你的业务逻辑（DB 查询、文件读写）。所以**读取和处理都在同一个 worker 线程里串行完成**，注释说的"接收和处理分离"实际没做到。后果：处理慢（大文件下载、慢 SQL）会占住这个 worker，epoll 线程只负责 accept/分发，但 worker 池被慢任务占满后新连接的处理会排队。

> 期望回答：把"读 socket"和"解包+业务"拆成两段，读完后把完整包投到**业务线程池**处理，读线程立刻 re-arm EPOLLONESHOT 去收下一个；或者确认当前模型下 worker 数够用、慢任务不会拖垮整体。

### 8. 线程池（P2）
- `thread_shutdown` 初始化为 `TRUE` 表示"运行中"，语义反直觉，容易读错。
- `pool_destroy()` 在头文件声明但 `.cpp` 没实现；`Close()` 也不清理线程池、不 `delete m_tcp/m_sql` → 无优雅退出。
- Manager 线程的扩容是 `for j<min { for i<max { 找空位创建 } }`，一次可能激增 `min`(=10) 个线程；且 `pthread_create` 失败后仍然 `++thread_alive` → 状态不一致。
- 缩容靠 `thread_wait` 计数器 + `not_empty` 信号，逻辑能跑但边界条件多。

> 期望回答：命名改 `m_running`；实现 `pool_destroy`（广播退出 + `pthread_join` 所有线程 + 释放队列/锁）；扩容失败回滚计数；或者直接换成成熟的 `std::async` / `boost::asio` / `libevent` 线程模型。

### 9. 其它值得提一嘴的
- `TCPKernel` 构造里 `m_sql/m_tcp/m_logic` 未初始化；`Open()` 若 `ConnectMysql` 失败就 `return FALSE`，之后 `main` 调 `Close()` → `m_logic->close()` 访问野指针 → 崩溃。
- `GetFileList` 把"推荐算法 + 写库（曝光 hotdegree+1、插 t_UserRecv）"塞进一个下载列表请求里，读操作有副作用、非幂等；并发两个下载同一用户会重复插 `t_UserRecv`。
- `RegisterRq` 注册成功但 `select id` 失败时 `id=0` 仍返回 `register_success`，且不再建目录 → 后续逻辑靠 id 的地方会错。

---

## 三、客户端侧值得问的（作为对比 / 加分项）

1. **网络层：部分比服务端稳，但别夸大**：`TcpClient::RecvData` 有 `nPackSize` 上界校验（`_DEF_MAX_PACK_SIZE`）、**包体**半包循环读、`nRes<=0` 判断线，这几点确实比服务端早做。但⚠️**口径修正**：客户端 4 字节**长度头**是 `TcpClient.cpp:164` 单次 `recv`、**没有循环读满**，和服务端已修的对称加固不一样。所以准确说法是"两端各有短板：服务端长度头补了、客户端没补；客户端包体上界早做了、服务端后补的"，别再一句"客户端比服务端严谨"。
   - ⚠️**另一处证伪**：曾有分析说"客户端跨线程信号传 `char*` 未 `qRegisterMetaType` → 收不到包"。**实际证据是能正常登录收包**（弹窗为证），说明 `char*` 的队列投递在当前 Qt 构建下能跑通。它只是"可移植性/健壮性待加固（跨 Qt 版本可能告警）"，**不是链路失效**。面试别把它当严重 bug 讲，否则一问"那你怎么还能登录"就露馅。
2. **解码管线用的是废弃 API**：`avcodec_decode_video2` / `avcodec_decode_audio4` / `av_register_all` / `avpicture_*` / `av_free_packet` 在 FFmpeg 4.x 已废弃，应升级到 `avcodec_send_packet` / `avcodec_receive_frame` + `av_packet_unref`。升级代价主要在 API 调用方式和错误处理。
3. **音视频同步策略**：以**音频时钟为主**，视频帧 `video_pts <= audio_pts` 才渲染（追音频），是标准做法；`seek` 时用 `FLUSH_DATA` 清空解码器/队列缓存解决花屏——这块设计是亮点，面试官大概率会让你讲一遍。
4. **性能点**：`audio_decode_frame` 里每帧都 `swr_alloc_set_opts`+`swr_init`+`swr_free`，应复用 `SwrContext`；`videoplayer.cpp` 多条路径 `malloc` 后出错直接 `return` 不释放 `pFormatCtx`/`packet`（资源泄漏）。
5. **线程模型**：读线程（生产者）+ 视频线程（SDL）+ 音频回调（SDL 音频线程）+ Qt 主线程（显示），跨线程用 `QImage` 信号传图（隐式共享 + copy 后安全）。可以讲清楚"为什么视频不走 SDL 窗口而走 QImage 上 Qt 控件"。
6. **客户端也有必现的生命周期 bug（新增）**：`VideoPlayer::run()` 多条错误 return 不复位 `readThreadFinished`，导致打开坏 URL 后再点停止会在 GUI 线程 `while(!readThreadFinished)` 死循环冻界面；无音频流视频走 SDL 定时器路径同样卡死 + 停止后定时器回调 UAF。这些是"每条异常路径都要走统一 cleanup 出口"的经典反例，可作为客户端侧的深挖点。

---

## 四、如果被问"你会怎么改进"，照这个清单说

> 前 2 条已经落地（可讲"已修"），其余是仍待做的方向。

1. **零信任分包【已做】**：长度前缀循环读满 + `(0,10MB]` 校验 + 半包丢弃；`DealData` 按包类型最小长度表校验。还差：`ntohl` 字节序归一。
2. **并发安全【部分已做】**：v2 断点续传的所有共享 map 都有 `m_v2Mutex`，推荐"换一批"的 `m_mapLastPushed` 有 `m_recMutex`；v1 老 map（`m_mapFileIDToFileInfo`/`m_mapIDToUserFD`）仍无锁；发送按 fd 加锁或单发送线程仍未做。
2.5. **进程存活性（新增，最该先补）**：`signal(SIGPIPE, SIG_IGN)` + `send` 带 `MSG_NOSIGNAL`，否则客户端断连时一次 send 就杀进程；大文件发送从 worker 线程剥离，避免信令被文件传输饿死。
3. **鉴权**：登录下发 token（如随机 session id 存服务端 map，带超时），所有业务包校验 token→userId，杜绝冒充；`streamKey` 用不可预测随机串。
4. **注入/溢出**：SQL 参数化或 `mysql_real_escape_string`；`_DEF_SQLIEN` 从 400 提到足够大或改 `snprintf` 防栈溢出；文件名白名单 + 服务端重命名（UUID）；路径在 `RootPath` 内做 `realpath` 归一化防穿越。
5. **DB**：连接池 + 超时 + 重连；慢查询监控。
6. **健壮性**：`Open()` 失败路径不调 `Close()` 或 `Close()` 判空；实现 `pool_destroy` 优雅退出；`fopen` 返回值必检；`OnClientDisconnect` 补上 v1 老路径 `m_mapFileIDToFileInfo` 的清理；handler 里响应包发送路径统一（登录重复发包同源问题）。
7. **可维护**：协议解析用显式字段拷贝代替裸强转；推荐/写库逻辑拆出独立服务或至少与"下载列表"解耦，保证读操作幂等。
8. **客户端**：升级 FFmpeg 新解码 API；复用 `SwrContext`；统一错误路径释放资源；`VideoPlayer::run()` 所有异常 return 走统一 cleanup 出口（复位 finished 标志 + 状态），根治停止时 GUI 死循环。

---

## 五、面试开场建议（你被问"讲讲你的项目"时）

先一句话定位：**"一个运行在 Linux 上的 C++ 高并发媒体服务器，epoll + 线程池处理上万连接，负责注册/登录、音视频上传下载、直播信令与基于标签匹配的推荐；客户端是 Qt + FFmpeg + SDL 实现的播放器。"** 然后主动挑一个你最有把握的点（推荐说**并发模型 + epoll ONESHOT** 或 **音视频同步**）展开，把上面这些"踩过的坑 / 待改进"作为反思带出来——面试官最爱"自己知道哪里有问题"的候选人。
