# M7 真实宿主 Shadow 短时重复运行

- 日期：2026-11-11
- 宿主：AstrBot 4.29.3 / testbed
- 状态：**5 次连续真实宿主链路全部成功；不是 72h soak**

## 1. 结果

| 项 | 值 |
|---|---:|
| runs | 5 |
| failures | **0** |
| 每次 plugin loaded | true |
| 每次 memory prefix | true |
| shadow events | 24 |
| shadow sources | 24 |
| shadow scopes | 6 |
| shadow observations | 18 |

## 1.1 第二批 5 次

| 项 | 值 |
|---|---:|
| successful runs | **10** |
| failures | 0 |
| shadow events | 52 |
| shadow sources | 52 |
| shadow scopes | 13 |
| observations | 39 |
| each injection prefix | true |

操作陷阱：有 5 次未设置 `DIGITAL_LIFE_DEV` 的运行，V1 正常注入但 shadow 插件导入失败、DB 不增长；这类运行不能计入 shadow soak。

## 2. 判读

- 连续 5 次启动真实 AstrBot、加载 V1 + shadow、发送合成 WebChat 消息；
- V1 注入每次成功；
- shadow 每次观察并写入独立 DB/log；
- 没有一次失败；
- 仍未覆盖 72h、真实 QQ/NapCat、断电。

## 3. 边界

- testbed mock provider；
- 合成用户/文本；
- 5 次约数分钟；
- 没有回退演练；
- 不是 R1 验收。
