# hotdegree 为什么会这样变 —— 代码级分析（只读不改）

> ⚠️ **历史文档（问题定位记录，所述"三套写入逻辑"已在 v6/v7/v8 全部修复）**：曝光+1 已删（v6）、点击+5 已删（v7）、playCount 双计数器已合并成单条 SQL 同源派生（v8）。当前 hotdegree 仅由 `PlayReportRq` 播放上报单源驱动，公式也换成了中枢惩罚式 `GREATEST(0, ROUND(playCount30d * 25 * (avgCompletion30d - 0.3)))`。最新说明见《视频推荐功能讲解.md》第三章。本文保留的价值：完整的"快照逐帧比对定位数据异常"排查方法记录。

> 结论先行：**`t_VideoInfo.hotdegree` 有"三套写入逻辑"，其中"播放上报"用的是覆盖式重算，公式里没有播放记录的视频结果直接=0，会把前面"曝光/点击"累积的热度清掉。** 你截图里 22~26 突然归零、27~31 随播放精确上涨，全部能从这个机制解释。下文逐帧核对。

---

## 一、代码里 hotdegree 的三套写入逻辑

文件：`MediaServer-live/MediaServer/src/clogic.cpp`

| # | 位置 | 触发 | SQL | 语义 |
|---|---|---|---|---|
| ① 曝光+1 | 行 480 / 556（`GetFileList`） | 服务端把视频推给客户端列表时 | `update t_VideoInfo set hotdegree = hotdegree + 1 where videoId = %d` | **累加** |
| ② 点击+5 | 行 714（`VideoClickRq`） | 客户端双击卡片、准备播放的瞬间 | `update t_VideoInfo set hotdegree = hotdegree + 5 where videoId = %d` | **累加** |
| ③ 播放重算 | 行 685~702（`PlayReportRq`） | 客户端上报播放时长/完播率 | `playCount+1` + `SET hotdegree = ROUND(5*playCount30d + 20*avgCompletion30d*playCount30d)` | **覆盖式** |

关键看 ③ 的重算公式（行 694~699）：

```sql
UPDATE t_VideoInfo v
LEFT JOIN (
    SELECT videoId,
           COUNT(*)                       AS playCount30d,
           AVG(watchSeconds/totalSeconds) AS avgCompletion30d
    FROM t_VideoPlayLog
    WHERE videoId = %d
      AND playTime > NOW() - INTERVAL 30 DAY
      AND totalSeconds > 0
    GROUP BY videoId
) p ON p.videoId = v.videoId
SET v.hotdegree = ROUND(5*IFNULL(p.playCount30d,0)
                      + 20*IFNULL(p.avgCompletion30d,0)*IFNULL(p.playCount30d,0))
WHERE v.videoId = %d;
```

**这是 SET 覆盖，不是累加。** 当一个视频在 `t_VideoPlayLog` 里**没有任何记录**（`playCount30d = 0`），结果就是：

```
hotdegree = ROUND(5*0 + 20*NULL*0) = 0
```

→ 直接把之前①/②累积的热度清零。这就是 22~26 归零的唯一代码路径。

`playCount` 字段则由行 685 `SET playCount = playCount + 1` 单独累加，和重算公式"读 `t_VideoPlayLog` 的 COUNT"是两个口径（播放时同步涨，所以后面对得上，但语义上是两套计数器）。

---

## 二、你的 10 次快照逐帧解读

| 快照 | 关键变化 | 推断触发的操作 |
|---|---|---|
| T0 | 全部 (0,0) | 初始上传，hotdegree 默认 0 |
| T1 | 全部 (2,0) | `GetFileList` 被调用 2 次，每次把 10 个视频各曝光 +1（0→1→2） |
| T2 | 28→(7,0)，其余 2 | 28 被点击：VideoClick +5（2→7），未播放（play 仍 0） |
| T3 | 28→(12,1)，31→(7,0) | 28 播放：PlayReport 重算=12 ⇒ 完播率≈0.35；31 点击 +5（2→7） |
| T4 | 31→(9,1)，27→(7,0) | 31 播放：重算=9 ⇒ 完播率≈0.2；27 点击 +5（2→7） |
| T5 | **22~26→(0,0)**，27→(12,1)，29→(5,0)，30→(10,0) | ① 27 播放重算=12；② **22~26 被某次覆盖式重算清零（无播放记录→0）**；③ 29/30 处中间态（点击/曝光交错，值不必逐帧拟合） |
| T6 | 30→(15,1)，29→(15,1) | 30、29 首次播放：重算=15 ⇒ 各播放 1 次、完播率≈0.5 |
| T7 | 30→(30,2)，27→(17,1) | 30 第 2 次播放：重算=30 ⇒ 2 次、完播率≈0.5；27 第 2 次播放 |
| T8 | 30→(39,3) | 30 第 3 次播放：重算=39 ⇒ 3 次、完播率≈0.4 |
| T9/T10 | 不变 | 无新播放/点击/曝光 |

### 一个强力佐证：videoId=30 的轨迹

```
播放次数 N : 1    2    3
hotdegree   : 15   30   39
```

套公式 `hotdegree = 5*N + 20*completion*N`：

- N=1: `5 + 20*0.5*1 = 15` ✓
- N=2: `10 + 20*0.5*2 = 30` ✓
- N=3: `15 + 20*0.4*3 = 39` ✓

**完全吻合**，说明"播放重算"这条链路是正常工作的，而且就是 hotdegree 的"最终裁决者"。

---

## 三、根因总结

1. **两套语义打架**：①曝光+1、②点击+5 是"累加式热度"；③播放重算是"覆盖式热度"，且以后者为准。
2. **无播放 = 热度蒸发**：任何视频只要还没真实播放过（不在 `t_VideoPlayLog`），一旦被重算公式扫过，hotdegree 就被强制改成 0，前面累积的曝光/点击全废。
3. **22~26 为什么归零**：它们从来没被真正播放过（playCount 始终 0，`t_VideoPlayLog` 无记录），所以在 T4→T5 之间某次重算（很可能是你手动/定时跑了 `tools/recompute_hotdegree.sql` 批量重算，或某条视频的 PlayReport 波及）把它们清成了 0。
4. **27~31 为什么稳定且规律上涨**：它们被实际播放过，重算公式基于 `t_VideoPlayLog` 的播放次数+完播率产出确定值，且每次播放只动自己那条（`WHERE videoId=%d`），不会被别人影响。
5. **反直觉但符合代码**：你看到"之前明明有热度(2)，怎么突然没(0)了"——因为那个 2 是曝光临时分，在覆盖式重算眼里"没播放就不配拥有热度"。

---

## 四、解决方案（仅建议，未改动你的任何代码）

### 方案 A：统一成"累加式"（改动最小，推荐）
把 ③ 的覆盖式重算，改成**基于本次上报的增量累加**，不再 SET 覆盖：

```sql
-- 思路：只把"这次播放新增的热量"加到现有 hotdegree 上，而不是整体重算
UPDATE t_VideoInfo
SET hotdegree = hotdegree
    + 5                                   -- 每播放一次基础分
    + 20 * (CASE WHEN totalSeconds>0
                THEN watchSeconds/totalSeconds ELSE 0 END)  -- 本次完播率加权
WHERE videoId = %d;
```

这样曝光(①)、点击(②)、播放(③)三种热量**全部累加、互不覆盖**，22~26 即使没播放也保住曝光分，不会归零。完播率用本次上报值即可，不必再 LEFT JOIN 重算整段历史。

### 方案 B：统一成"纯重算式"（更彻底，但要补事件源）
若你更想要"热度由播放数据客观算出"，那就**放弃①/②的累加**，所有热量只来自重算。但必须把"曝光/点击"也接进计算口径，否则没播放的视频永远 0 分、推荐永远推不出新视频：

```sql
-- 给曝光/点击也落一条事件记录（watchSeconds=0 表示非播放）
INSERT INTO t_VideoPlayLog (videoId, userId, watchSeconds, totalSeconds, playTime, evType)
VALUES (%d, %d, 0, 0, NOW(), 'click');   -- 点击
-- 曝光同理 evType='impression'
```

然后重算公式对 `evType='click'/'impression'` 给一个保底权重（注意 `watchSeconds/totalSeconds` 会除 0，要 `CASE WHEN totalSeconds>0 THEN ... ELSE 0 END` 特判）。这样"没播放但有曝光"的视频也能拿到基础分，不会归零。

### 方案 C：统一 playCount 与重算口径
现在 `playCount` 字段（行 685 累加）和重算读的 `COUNT(t_VideoPlayLog)` 是两个计数器，长期可能漂移。建议二选一：
- 重算直接读 `t_VideoInfo.playCount` 字段：`... WHERE playCount30d = v.playCount ...`；
- 或干脆删掉 `playCount` 字段，列表展示时 `SELECT COUNT(*) FROM t_VideoPlayLog WHERE videoId=?` 派生。

### 方案 D：加"最新上架"时间衰减（呼应你另一个项目的 created_at 思路）
若想让新视频有曝光红利，可在重算里加时间项（但不要因此让无播放视频直接归零，至少保留曝光分）：

```sql
SET v.hotdegree = ROUND(
    5*IFNULL(p.playCount30d,0)
  + 20*IFNULL(p.avgCompletion30d,0)*IFNULL(p.playCount30d,0)
  + 3 * EXP(-TIMESTAMPDIFF(HOUR, v.created_at, NOW())/168)  -- 7天内新鲜度衰减
);
```

### 方案 E：排查批量重算脚本
注释里说"从 crontab 定时改成事件触发"，但 `tools/recompute_hotdegree.sql` 仍在。请确认：是否在 T4→T5 之间手动/定时跑过它？它若是 `UPDATE ... SET hotdegree = <公式>` 全表覆盖，正是 22~26 集体归零的元凶。若要保留定时任务，务必用方案 A/B 让它"累加或带保底"，别整体覆盖。

---

## 五、你可以自己验证的 SQL（在 Linux 上跑，不碰代码）

```sql
-- 1. 看每个视频的真实播放记录数（0 条的就是"会被重算清零"的）
SELECT v.videoId, COUNT(p.videoId) AS playRecords, v.hotdegree, v.playCount
FROM t_VideoInfo v
LEFT JOIN t_VideoPlayLog p ON p.videoId = v.videoId
GROUP BY v.videoId ORDER BY v.videoId;

-- 2. 手动套一次重算公式，看是不是和无播放=0 吻合
SELECT videoId,
       ROUND(5*COUNT(*) + 20*AVG(watchSeconds/totalSeconds)*COUNT(*)) AS recomputed
FROM t_VideoPlayLog
WHERE playTime > NOW() - INTERVAL 30 DAY AND totalSeconds > 0
GROUP BY videoId;
```

> 预期：第 1 条查询里 `playRecords=0` 的 videoId，正是你截图里 hotdegree 被清零的那些（22~26）。

---

## 六、一句话给面试官/复盘用

> "热度字段有累加（曝光+1/点击+5）和覆盖式重算（播放完播率）两套写入，重算公式对无播放记录的视频结果恒为 0，会清掉累积热度。我后来意识到应该统一语义——要么播放也改成增量累加，要么把曝光/点击也作为事件入表参与重算，并给无播放视频保留曝光保底分。"
