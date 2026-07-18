/*
 * stress_test_client.cpp — 断点续传 v2 自动化测试（手搓裸 socket，无需 Qt）
 * ----------------------------------------------------------------------------
 * 它自动完成：生成随机测试文件 → 握手(全新) → 发 50% → 直接 close socket（模拟
 * 客户端被强杀/网络中断，完全不需要人工卡时间点）→ 重连发同一 MD5 → 断言服务端
 * 回“断点续传”且偏移≈一半 → 续传剩余 → 发 END → 断言服务端终校成功(=1)。
 *
 * 关键：END 返回 1 意味着服务端把落盘文件重新算 MD5 并与你发的指纹比对通过，
 *      即“续传后文件字节完全一致”的强证明，无需再去读服务端磁盘。
 *
 * 编译（在 Linux 服务端机器上，需 OpenSSL 开发头文件；服务端本身就用 OpenSSL）：
 *     g++ -O2 stress_test_client.cpp -o stress_test_client -lcrypto
 *
 * 运行：
 *     ./stress_test_client [server_ip] [server_port] [userid]
 *     默认 server_ip=192.168.129.137  port=8001  userid=1
 *
 * 每次运行用唯一文件名，因此第一次握手必为 new(1)；最终再握一次同 MD5 顺带探测
 * 秒传(instant)。断言失败会打印具体原因，方便定位是“未释放占用锁”还是“断开即删 .part”。
 * ----------------------------------------------------------------------------
 */
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <ctime>
#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <openssl/md5.h>

// ===== 复刻服务端 packdef.h 的 v2 协议（必须逐字节一致） =====
#pragma pack(push,1)
typedef int PackType;

struct STRU_UPLOAD_V2_RQ {
    PackType m_nType;
    int      m_UserId;
    int64_t  m_nFileSize;
    char     m_szMd5[33];
    char     m_szHobby[8];
    char     m_szFileName[260];
    char     m_szGifName[260];
    char     m_szFileType[40];
};

struct STRU_UPLOAD_V2_RS {
    PackType m_nType;
    int      m_nResult;     // new=1 / resume=2 / instant=3 / busy=4 / fail=0
    int      m_nTaskId;
    int64_t  m_nResumeFrom; // 断点续传起点（已收字节数）
};

struct STRU_UPLOAD_BLOCK {
    PackType m_nType;
    int      m_nTaskId;
    int64_t  m_nOffset;
    int      m_nLen;
};

struct STRU_UPLOAD_END_RQ {
    PackType m_nType;
    int      m_nTaskId;
    char     m_szMd5[33];
};

struct STRU_UPLOAD_END_RS {
    PackType m_nType;
    int      m_nResult;     // 0=校验失败需重传 1=成功
};
#pragma pack(pop)

// v2 包类型常量（_DEF_PACK_BASE=10000）
static const PackType T_UPLOAD_V2_RQ  = 10020;
static const PackType T_UPLOAD_V2_RS  = 10021;
static const PackType T_UPLOAD_BLOCK  = 10022;
static const PackType T_UPLOAD_END_RQ = 10023;
static const PackType T_UPLOAD_END_RS = 10024;

enum { R_NEW = 1, R_RESUME = 2, R_INSTANT = 3, R_BUSY = 4, R_FAIL = 0 };

static int g_sock = -1;

// 帧格式与服务端 SendData 对齐：[4字节 host 序长度][结构体/块体]
static bool send_frame(const void* body, int bodyLen) {
    uint32_t len = (uint32_t)bodyLen; // host order，不使用 htonl
    if (send(g_sock, &len, 4, 0) != 4) return false;
    if (send(g_sock, body, bodyLen, 0) != bodyLen) return false;
    return true;
}

// 读一帧：先读 4 字节长度，再按长度读正文；返回正文长度，失败 < 0
static int recv_frame(void* buf, int maxLen) {
    uint32_t len = 0;
    if (recv(g_sock, &len, 4, MSG_WAITALL) != 4) return -1;
    if ((int)len > maxLen) return -2;
    int got = 0;
    while (got < (int)len) {
        int r = recv(g_sock, (char*)buf + got, len - got, MSG_WAITALL);
        if (r <= 0) return -1;
        got += r;
    }
    return (int)len;
}

static bool connect_server(const char* ip, int port) {
    g_sock = socket(AF_INET, SOCK_STREAM, 0);
    if (g_sock < 0) { perror("socket"); return false; }
    struct sockaddr_in a; memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port   = htons((uint16_t)port);
    if (inet_pton(AF_INET, ip, &a.sin_addr) != 1) { perror("inet_pton"); return false; }
    if (connect(g_sock, (struct sockaddr*)&a, sizeof a) != 0) { perror("connect"); return false; }
    return true;
}

static void md5_file(const char* path, char out[33]) {
    MD5_CTX c; MD5_Init(&c);
    FILE* f = fopen(path, "rb");
    unsigned char buf[65536]; size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) MD5_Update(&c, buf, n);
    fclose(f);
    unsigned char d[16]; MD5_Final(d, &c);
    for (int i = 0; i < 16; i++) sprintf(out + i * 2, "%02x", d[i]);
    out[32] = 0;
}

static void gen_random_file(const char* path, int64_t size) {
    FILE* f = fopen(path, "wb");
    unsigned char buf[65536];
    int64_t left = size;
    while (left > 0) {
        size_t chunk = (size_t)(left < (int64_t)sizeof buf ? left : sizeof buf);
        for (size_t i = 0; i < chunk; i++) buf[i] = (unsigned char)(rand() * 256 / RAND_MAX);
        fwrite(buf, 1, chunk, f);
        left -= chunk;
    }
    fclose(f);
}

// 从 startOff（含）发到 endOff（不含），返回实际发送字节数
static int64_t send_blocks(int taskId, const char* path, int64_t startOff, int64_t endOff, int chunk) {
    FILE* f = fopen(path, "rb");
    int64_t off = startOff;
    unsigned char* payload = new unsigned char[chunk];
    STRU_UPLOAD_BLOCK blk; memset(&blk, 0, sizeof blk);
    blk.m_nType   = T_UPLOAD_BLOCK;
    blk.m_nTaskId = taskId;
    while (off < endOff) {
        int64_t remain = endOff - off;
        int len = (int)(remain < chunk ? remain : chunk);
        fseek(f, (long)off, SEEK_SET);
        size_t rd = fread(payload, 1, len, f);
        if (rd <= 0) break;
        blk.m_nOffset = off;
        blk.m_nLen    = (int)rd;
        int wireLen = (int)(sizeof(STRU_UPLOAD_BLOCK) + rd);
        unsigned char* wire = new unsigned char[wireLen];
        memcpy(wire, &blk, sizeof blk);
        memcpy(wire + sizeof blk, payload, rd);
        send_frame(wire, wireLen);
        delete[] wire;
        off += (int64_t)rd;
    }
    delete[] payload;
    fclose(f);
    return off - startOff;
}

int main(int argc, char** argv) {
    const char* ip     = (argc > 1) ? argv[1] : "192.168.129.137";
    int         port   = (argc > 2) ? atoi(argv[2]) : 8001;
    int         userid = (argc > 3) ? atoi(argv[3]) : 1;

    srand((unsigned)time(0));

    char path[512];
    snprintf(path, sizeof path, "/tmp/stress_resume_%d_%ld.bin", (int)getpid(), (long)time(0));
    int64_t fileSize = 1 * 1024 * 1024; // 1MB 测试文件
    gen_random_file(path, fileSize);
    char md5[33]; md5_file(path, md5);

    char fname[260];
    snprintf(fname, sizeof fname, "stest_%d_%ld.bin", (int)getpid(), (long)time(0));

    printf("[*] test file=%s  size=%lld  md5=%s\n", path, (long long)fileSize, md5);
    printf("[*] server=%s:%d  userid=%d\n\n", ip, port, userid);

    // ---------- 第一次连接：全新上传，发 50% 后强杀 ----------
    if (!connect_server(ip, port)) return 1;

    STRU_UPLOAD_V2_RQ rq; memset(&rq, 0, sizeof rq);
    rq.m_nType     = T_UPLOAD_V2_RQ;
    rq.m_UserId    = userid;
    rq.m_nFileSize = fileSize;
    memcpy(rq.m_szMd5, md5, 32); rq.m_szMd5[32] = 0;
    memcpy(rq.m_szFileName, fname, strlen(fname) + 1);
    snprintf(rq.m_szFileType, sizeof rq.m_szFileType, "bin");
    send_frame(&rq, sizeof rq);

    STRU_UPLOAD_V2_RS hs;
    int n = recv_frame(&hs, sizeof hs);
    if (n != (int)sizeof hs) { printf("[!] handshake#1 接收失败 n=%d\n", n); return 1; }
    printf("[*] handshake#1 result=%d taskId=%d resumeFrom=%lld\n",
           hs.m_nResult, hs.m_nTaskId, (long long)hs.m_nResumeFrom);
    if (hs.m_nResult != R_NEW) {
        printf("[FAIL] handshake#1 期望 new(1)，实际 %d（可能上次残留？换文件名重试）\n", hs.m_nResult);
        return 1;
    }
    int taskId = hs.m_nTaskId;

    int64_t half = fileSize / 2;
    send_blocks(taskId, path, 0, half, 65536);
    printf("[*] 已发送前 50%% (%lld bytes)\n", (long long)half);

    usleep(300000); // 排空在途块，确保服务端已落盘到一半
    printf("[*] 模拟客户端被强杀：直接 close socket（不发 END）\n");
    close(g_sock); g_sock = -1;

    // ---------- 重连：同 MD5 应触发断点续传 ----------
    sleep(1); // 等服务端感知断开并清理内存态（保留 .part）
    if (!connect_server(ip, port)) return 1;

    STRU_UPLOAD_V2_RQ rq2; memset(&rq2, 0, sizeof rq2);
    rq2.m_nType     = T_UPLOAD_V2_RQ;
    rq2.m_UserId    = userid;
    rq2.m_nFileSize = fileSize;
    memcpy(rq2.m_szMd5, md5, 32); rq2.m_szMd5[32] = 0;
    memcpy(rq2.m_szFileName, fname, strlen(fname) + 1);
    snprintf(rq2.m_szFileType, sizeof rq2.m_szFileType, "bin");
    send_frame(&rq2, sizeof rq2);

    STRU_UPLOAD_V2_RS hs2;
    n = recv_frame(&hs2, sizeof hs2);
    if (n != (int)sizeof hs2) { printf("[!] handshake#2 接收失败 n=%d\n", n); return 1; }
    printf("[*] handshake#2 result=%d taskId=%d resumeFrom=%lld\n",
           hs2.m_nResult, hs2.m_nTaskId, (long long)hs2.m_nResumeFrom);

    if (hs2.m_nResult == R_BUSY) {
        printf("[FAIL] 返回 busy(4)：服务端未在连接断开时释放 (userId,md5) 占用锁，"
               "断点续传被永久阻塞 —— 需要在 close() 里清理 m_mapBusy。\n");
        return 1;
    }
    if (hs2.m_nResult != R_RESUME) {
        printf("[FAIL] handshake#2 期望 resume(2)，实际 %d（若返回 new(1) 说明上次中断的 "
               ".part 被服务端删除了，续传未生效）。\n", hs2.m_nResult);
        return 1;
    }
    if (hs2.m_nResumeFrom <= 0 || hs2.m_nResumeFrom >= fileSize) {
        printf("[FAIL] resumeFrom=%lld 不在 (0, %lld) 区间，不是有效的部分续传点。\n",
               (long long)hs2.m_nResumeFrom, (long long)fileSize);
        return 1;
    }
    if (hs2.m_nResumeFrom != half) {
        printf("[warn] resumeFrom=%lld 与精确一半 %lld 略有偏差（在途块容差内，不影响正确性）。\n",
               (long long)hs2.m_nResumeFrom, (long long)half);
    }
    printf("[OK] 服务端确认断点续传，偏移=%lld\n", (long long)hs2.m_nResumeFrom);
    int taskId2 = hs2.m_nTaskId;

    // ---------- 续传剩余 ----------
    send_blocks(taskId2, path, hs2.m_nResumeFrom, fileSize, 65536);
    printf("[*] 续传完成，发送 END 触发终校\n");

    // ---------- END ----------
    STRU_UPLOAD_END_RQ endrq; memset(&endrq, 0, sizeof endrq);
    endrq.m_nType   = T_UPLOAD_END_RQ;
    endrq.m_nTaskId = taskId2;
    memcpy(endrq.m_szMd5, md5, 32); endrq.m_szMd5[32] = 0;
    send_frame(&endrq, sizeof endrq);

    STRU_UPLOAD_END_RS endrs;
    n = recv_frame(&endrs, sizeof endrs);
    if (n != (int)sizeof endrs) { printf("[!] END 接收失败 n=%d\n", n); return 1; }
    printf("[*] END result=%d\n", endrs.m_nResult);
    if (endrs.m_nResult != 1) {
        printf("[FAIL] 终校失败 result=%d（服务端读回文件算 MD5 与你发的不一致，续传数据错位/损坏）。\n",
               endrs.m_nResult);
        return 1;
    }
    printf("[OK] 服务端整文件 MD5 终校通过 = 续传后文件字节完全一致\n");

    // ---------- 额外：同 MD5 再握手应为秒传(instant) ----------
    sleep(1);
    STRU_UPLOAD_V2_RQ rq3; memset(&rq3, 0, sizeof rq3);
    rq3.m_nType     = T_UPLOAD_V2_RQ;
    rq3.m_UserId    = userid;
    rq3.m_nFileSize = fileSize;
    memcpy(rq3.m_szMd5, md5, 32); rq3.m_szMd5[32] = 0;
    memcpy(rq3.m_szFileName, fname, strlen(fname) + 1);
    snprintf(rq3.m_szFileType, sizeof rq3.m_szFileType, "bin");
    send_frame(&rq3, sizeof rq3);
    STRU_UPLOAD_V2_RS hs3;
    n = recv_frame(&hs3, sizeof hs3);
    if (n == (int)sizeof hs3) {
        printf("[*] handshake#3(秒传探测) result=%d\n", hs3.m_nResult);
        if (hs3.m_nResult == R_INSTANT) printf("[OK] 秒传探测通过\n");
        else printf("[info] 秒传探测返回 %d（取决于服务端秒传判定依据，非阻断项）。\n", hs3.m_nResult);
    }

    close(g_sock);
    printf("\n===== PASS：断点续传端到端验证成功 =====\n");
    return 0;
}
