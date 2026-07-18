# 方案 3 改动对照清单（GetFileList 解耦"推什么"与"热不热"）

> ⚠️ **历史文档（v6 时期，已被 v9 取代）**：本清单描述的"兜底模式（bAllPushed，不清表）+ 推送时写 t_UserRecv"已经不是当前实现。
> 当前版本（v9）：`t_UserRecv` 语义改为**看过**（`VideoClickRq` 点击播放时写入 + `recvTime`），推送不写表；`GetFileList` 改为**四级填坑**（没看过+不在上一批 → 没看过但上一批推过 → 看过的按 recvTime 最早补位 → 全看完清表重开循环），兜底模式已废弃。最新说明见《视频推荐功能讲解.md》。
> 本清单仍保留的价值：它记录了"曝光不再驱动热度"这一决策的完整推导过程（该决策 v9 仍然成立）。

> 目标：彻底修掉"播放一个视频，其他视频热度也涨"。
> 核心思路：曝光不再写 `hotdegree`；"全推完"不再 `DELETE` 去重表，而是进入兜底模式返回全部视频（按热度排序），且不重写去重表、不加分。
> 未修改任何源码，仅给对照。所有改动都在 `MediaServer/src/clogic.cpp` 的 `CLogic::GetFileList`。

---

## 改动 1：用"兜底标志"替换"清空去重表"（原 391–400 行）

**改前**
```cpp
    nCount=atoi(resList.front().c_str());
    cout << "[GetFileList] Video count: " << nCount << endl;
    if(nCount==0)
    {
        cout<<"[GetFileList] No new video, delete t_UserRecv"<<endl;
        sprintf(sqlBuf,"delete from t_UserRecv where userId = %d",userid);
        cout << "[GetFileList] Select SQL: " << sqlBuf << endl;
        if(!m_sql->UpdataMysql(sqlBuf)){
              cout<<"UpdataMysql error:"<<sqlBuf<<endl;
              return;
        }
    }
```

**改后**
```cpp
    nCount=atoi(resList.front().c_str());
    cout << "[GetFileList] Video count: " << nCount << endl;

    // 方案3：不再清空去重表。全推完就进入"兜底模式"，直接展示全部视频
    bool bAllPushed = (nCount == 0);
    if(bAllPushed)
        cout << "[GetFileList] All pushed, fallback to hot-ranked list (no delete, no re-insert)" << endl;
```

**效果**：`t_UserRecv` 永不被清空 → 不再每次刷新 `DELETE + 10×INSERT`，消除了 `out-of-sync` 触发土壤。

---

## 改动 2：前 7 坑位查询——兜底模式不排除已推（原 405–416 行）

**改前**
```cpp
    resList.clear();
    sprintf(sqlBuf,
        "select v.videoId, v.picName, v.picPath, v.rtmp "
        "from t_VideoInfo v, t_UserData u "
        "where u.id = %d "
        "and v.videoId not in (select videoId from t_UserRecv where userId = %d) "
        "order by "
        "((u.food=1 and v.food=1) + (u.funny=1 and v.funny=1) + (u.ennegy=1 and v.ennegy=1) + "
        "(u.dance=1 and v.dance=1) + (u.video=1 and v.video=1) + (u.music=1 and v.music=1) + "
        "(u.outside=1 and v.outside=1) + (u.edu=1 and v.edu=1)) desc, "
        "v.hotdegree desc "
        "limit 0,7;",
        userid, userid);
```

**改后**
```cpp
    resList.clear();
    // 正常模式排除已推；兜底模式不排除，直接按"标签匹配度 + 热度"展示全部
    string recvFilter = bAllPushed ? "" :
        "and v.videoId not in (select videoId from t_UserRecv where userId = " + to_string(userid) + ") ";
    sprintf(sqlBuf,
        "select v.videoId, v.picName, v.picPath, v.rtmp "
        "from t_VideoInfo v, t_UserData u "
        "where u.id = %d "
        "%s"
        "order by "
        "((u.food=1 and v.food=1) + (u.funny=1 and v.funny=1) + (u.ennegy=1 and v.ennegy=1) + "
        "(u.dance=1 and v.dance=1) + (u.video=1 and v.video=1) + (u.music=1 and v.music=1) + "
        "(u.outside=1 and v.outside=1) + (u.edu=1 and v.edu=1)) desc, "
        "v.hotdegree desc "
        "limit 0,7;",
        userid, recvFilter.c_str());
```

---

## 改动 3：前 7 坑位循环——不重写去重表、删除曝光 +1（原 472–482 行）

**改前**
```cpp
        fileList.push_back(info);
        cout << "[GetFileList] Add to fileList, size now: " << fileList.size() << endl;
        sprintf(sqlBuf,"insert into t_UserRecv values(%d ,%d);",userid,info->m_VideoID);
        if(!m_sql->UpdataMysql(sqlBuf)){
              cout<<"UpdataMysql error:"<<sqlBuf<<endl;
              return;
        }
        // 视频被推送=获得一次曝光机会，热度+1（回退到曝光即加分，因为播放上报这条链路一直没能确认打通）
        sprintf(sqlBuf,"update t_VideoInfo set hotdegree = hotdegree + 1 where videoId = %d;",info->m_VideoID);
        m_sql->UpdataMysql(sqlBuf);
```

**改后**
```cpp
        fileList.push_back(info);
        cout << "[GetFileList] Add to fileList, size now: " << fileList.size() << endl;
        // 方案3：曝光不再驱动热度；兜底模式不重写去重表（避免每次刷新清空+重建）
        if(!bAllPushed){
            sprintf(sqlBuf,"insert into t_UserRecv values(%d ,%d);",userid,info->m_VideoID);
            if(!m_sql->UpdataMysql(sqlBuf)){
                  cout<<"UpdataMysql error:"<<sqlBuf<<endl;
                  return;
            }
        }
        // （已删除）update t_VideoInfo set hotdegree = hotdegree + 1 ...
```

---

## 改动 4：后 3 坑位查询——兜底模式不排除已推（原 488–495 行）

**改前**
```cpp
    resList.clear();
    sprintf(sqlBuf,
        "select v.videoId, v.picName, v.picPath, v.rtmp "
        "from t_VideoInfo v "
        "where v.videoId not in (select videoId from t_UserRecv where userId = %d) "
        "and v.videoId not in (%s) "
        "order by v.hotdegree desc "
        "limit 0,3;",
        userid, excludeIds.c_str());
```

**改后**
```cpp
    resList.clear();
    string recvFilter2 = bAllPushed ? "" :
        "and v.videoId not in (select videoId from t_UserRecv where userId = " + to_string(userid) + ") ";
    sprintf(sqlBuf,
        "select v.videoId, v.picName, v.picPath, v.rtmp "
        "from t_VideoInfo v "
        "where 1=1 %s"
        "and v.videoId not in (%s) "
        "order by v.hotdegree desc "
        "limit 0,3;",
        recvFilter2.c_str(), excludeIds.c_str());
```

---

## 改动 5：后 3 坑位循环——同改动 3（原 548–558 行）

**改前**
```cpp
        fileList.push_back(info);
        cout << "[GetFileList] Add to fileList, size now: " << fileList.size() << endl;
        sprintf(sqlBuf,"insert into t_UserRecv values(%d ,%d);",userid,info->m_VideoID);
        if(!m_sql->UpdataMysql(sqlBuf)){
              cout<<"UpdataMysql error:"<<sqlBuf<<endl;
              return;
        }
        // 视频被推送=获得一次曝光机会，热度+1（回退到曝光即加分，因为播放上报这条链路一直没能确认打通）
        sprintf(sqlBuf,"update t_VideoInfo set hotdegree = hotdegree + 1 where videoId = %d;",info->m_VideoID);
        m_sql->UpdataMysql(sqlBuf);
```

**改后**
```cpp
        fileList.push_back(info);
        cout << "[GetFileList] Add to fileList, size now: " << fileList.size() << endl;
        // 方案3：兜底模式不重写去重表；曝光不再驱动热度
        if(!bAllPushed){
            sprintf(sqlBuf,"insert into t_UserRecv values(%d ,%d);",userid,info->m_VideoID);
            if(!m_sql->UpdataMysql(sqlBuf)){
                  cout<<"UpdataMysql error:"<<sqlBuf<<endl;
                  return;
            }
        }
        // （已删除）update t_VideoInfo set hotdegree = hotdegree + 1 ...
```

---

## 改动后行为对照

| 场景 | 方案 2（之前） | 方案 3（本清单） |
|---|---|---|
| 第 1 次拉列表（有未推视频） | 推新视频，重推轮不加分 | 推新视频，**曝光不再 +1** |
| 播放 B 后刷新列表 | 清空重推，跳过 +1（靠标志 hack） | 进入兜底，直接返回全部，**不删不插不加分** |
| 每次刷新对 MySQL 的压力 | `DELETE + 10×INSERT` 全表重建 | 仅 `SELECT`，无写操作 |
| 其他视频热度 | 不涨（但靠 hack 兜住） | 不涨（根因消除） |
| 列表是否会变空白 | 不会 | 不会（兜底永远返回全部） |
| `hotdegree` 语义 | 仍含"被刷到次数" | 只由 `PlayReportRq`（真实播放+完播）决定 |
| 新上传视频能否冒出来 | 能（清空后） | 能（正常模式排除已推，新 videoId 不在表中） |

---

## 可选增强：真正的"轮换"而非"全量重推"

兜底模式现在是"每次返回同样的 7+3（按匹配度+热度固定排序）"。若想让老视频轮换出现，把兜底查询的 `order by` 改成随机或带时间衰减，例如：

```cpp
// 兜底模式用随机轮换（仅兜底时生效，正常模式仍按匹配度）
string orderBy = bAllPushed ? "order by rand() limit 0,7;" : "order by ... v.hotdegree desc limit 0,7;";
```

或按上架时间：`order by created_at desc`（呼应你另一个项目的 `created_at` 思路）。

---

## 验证方式（不碰代码也能先确认方案 2/3 的根因）

```sql
-- 观察两次刷新之间 t_UserRecv 是否被清空又重建（方案 2 会晃；方案 3 稳定只增不减）
SELECT COUNT(*) FROM t_UserRecv WHERE userId = 1;

-- 确认 hotdegree 只随真实播放变化（PlayReportRq 写入 t_VideoPlayLog 后重算）
SELECT videoId, hotdegree, playCount FROM t_VideoInfo ORDER BY hotdegree DESC;
```
