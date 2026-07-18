# 视频直播功能实现概览

## 功能说明

基于现有的视频录制功能，实现了完整的视频直播系统。主播端可以开启直播，画面通过网络实时同步到其他客户端屏幕上。

## 架构设计

直播系统采用 **RTMP 推流/拉流 + 自定义 TCP 信令** 的双层架构：

```
主播端                    信令服务端              RTMP服务器              观众端
(MediaPlay)              (MediaServer)          (nginx-rtmp)           (MediaPlay)
    |                        |                      |                      |
    |--- LIVESTART_RQ ----->|                      |                      |
    |<-- LIVESTART_RS ------|                      |                      |
    |                        |                      |                      |
    |===== RTMP推流 =========|=====================>|                      |
    |   (FLV/H.264/AAC)      |                      |                      |
    |                        |                      |                      |
    |                        |<--- LIVELIST_RQ -----|<---------------------|
    |                        |---- LIVELIST_RS ---->|--------------------->|
    |                        |                      |                      |
    |                        |                      |<== RTMP拉流 <========|
    |                        |                      |==== 视频流 =========>|
```

### 设计要点
1. **视频传输用 RTMP**：复用已有的 FLV 编码（SaveVideoFileThread 已用 FLV 格式"为未来推流RTMP预留"）和 RTMP 播放能力（VideoPlayer 已支持 RTMP 解码）
2. **信令传输用现有 TCP 协议**：直播开始/停止/列表查询走现有的自定义二进制 TCP 协议（4字节长度前缀 + 结构体）
3. **零侵入录制代码**：SaveVideoFileThread 只需加 `m_bIsLive` 标志和 `avformat_network_init()` 调用，FLV→RTMP 推流由 FFmpeg 自动处理

## 修改/新增文件清单

### 服务端 (MediaServer)

| 文件 | 操作 | 说明 |
|------|------|------|
| `include/packdef.h` | 修改 | 添加 6 个直播协议宏定义 + 6 个协议结构体 + LiveRoom 内部结构体 |
| `include/clogic.h` | 修改 | 添加 LiveRoom 结构体、3 个直播处理函数声明、m_mapLiveIdToRoom 映射 |
| `src/clogic.cpp` | 修改 | 实现 LiveStartRq/LiveStopRq/LiveListRq，注册到 setNetPackMap，close() 中清理直播间 |
| `nginx-rtmp.conf` | 新增 | RTMP 服务器配置（live + vod 应用），含安装说明 |

### 客户端 (MediaPlay)

| 文件 | 操作 | 说明 |
|------|------|------|
| `netapi/net/packdef.h` | 修改 | 添加与服务端对应的 6 个协议宏定义 + 6 个结构体 |
| `savevideofilethread.h` | 修改 | 添加 `m_bIsLive` 成员和 `setLiveMode()` 方法 |
| `savevideofilethread.cpp` | 修改 | 构造函数初始化 `m_bIsLive=false`，slot_setInfo 中直播模式调用 `avformat_network_init()` |
| `livedialog.h` | 新增 | 直播界面声明（主播端） |
| `livedialog.cpp` | 新增 | 直播界面实现：标题输入、开始/停止直播、视频预览、RTMP 推流 |
| `onlinedialog.h` | 修改 | 添加直播信令槽函数、直播列表对话框成员、m_tcp/m_id 改为 public |
| `onlinedialog.cpp` | 修改 | 添加直播信令处理、直播列表显示、"查看直播"按钮 |
| `playerdialog.h` | 修改 | 添加 LiveDialog 成员和 slot_openLive 槽 |
| `playerdialog.cpp` | 修改 | 创建 LiveDialog、"开始直播"按钮（纯代码创建） |
| `MediaPlay.pro` | 修改 | 添加 livedialog.h/cpp |

## 新增协议定义

| 协议类型 | 值 | 方向 | 用途 |
|----------|-----|------|------|
| `_DEF_PACK_LIVESTART_RQ` | 10011 | 主播→服务端 | 开始直播请求（含标题） |
| `_DEF_PACK_LIVESTART_RS` | 10012 | 服务端→主播 | 开始直播回复（含推流地址） |
| `_DEF_PACK_LIVESTOP_RQ` | 10013 | 主播→服务端 | 停止直播请求 |
| `_DEF_PACK_LIVESTOP_RS` | 10014 | 服务端→主播 | 停止直播回复 |
| `_DEF_PACK_LIVELIST_RQ` | 10015 | 观众→服务端 | 获取直播列表请求 |
| `_DEF_PACK_LIVELIST_RS` | 10016 | 服务端→观众 | 直播列表项（每个直播间一个包） |

## 部署步骤

### 1. 服务端 (Linux)
```bash
# 编译 nginx-rtmp
sudo apt-get install build-essential libpcre3 libpcre3-dev libssl-dev zlib1g-dev
wget http://nginx.org/download/nginx-1.18.0.tar.gz
tar zxvf nginx-1.18.0.tar.gz
git clone https://github.com/arut/nginx-rtmp-module.git
cd nginx-1.18.0
./configure --with-http_ssl_module --add-module=../nginx-rtmp-module
make && sudo make install

# 复制配置
sudo cp nginx-rtmp.conf /usr/local/nginx/conf/nginx.conf

# 启动
sudo /usr/local/nginx/sbin/nginx

# 重新编译 MediaServer
cd MediaServer
qmake && make
./MediaServer 8001
```

### 2. 客户端 (Windows)
- 用 Qt Creator 打开 `MediaPlay.pro`，编译运行
- 主界面新增"开始直播"按钮
- 在线界面新增"查看直播"按钮
- 主播：点"开始直播" → 输入标题 → 开始推流
- 观众：登录 → 点"查看直播" → 双击直播间 → 观看

## 关键技术点

1. **FLV→RTMP 无缝切换**：SaveVideoFileThread 的 `slot_setInfo` 中，当 fileName 为 RTMP URL 时，FFmpeg 的 `avio_open` 自动使用 RTMP 协议处理器，无需改编码逻辑
2. **直播信令复用现有 TCP**：主播端通过 OnlineDialog 的共享 TCP 连接收发信令，OnlineDialog 转发响应给 LiveDialog
3. **直播间ID = 用户ID**：简化管理，一个用户同时只能有一个直播间
4. **RTMP 地址拼接**：服务端返回相对路径 `/<liveId>`，客户端拼接 `rtmp://<服务器IP>:1935/live/<liveId>`
