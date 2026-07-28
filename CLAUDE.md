# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

DeskBurn 是放在工位上的 AI Token 费用实时显示屏：Mac 端 Python 服务只读 CC Switch 的 SQLite 数据库（`~/.cc-switch/cc-switch.db`）聚合费用，每 30 秒经 BLE 推送给 ESP32-C3，固件驱动 3.5 寸 TFT（横屏 320×240）局部刷新显示。全链路无云服务，不传输 Prompt、回复正文或 API Key。

仓库所有文档和代码注释使用中文，新代码保持一致。注释记录的是实测约束（引脚、聚合口径、版本兼容性），不是装饰；修改相关代码前先读 `docs/踩坑记录.md` 确认约束仍然成立。

## 常用命令

### Python 侧（Mac 采集端）

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 只有 bleak，仅 --serve 需要

# 测试在 tools/ 目录下运行；不依赖 bleak，系统 python3 也能跑
cd tools
python3 -m unittest discover -s tests                    # 全部测试
python3 -m unittest tests.test_usage                     # 单个文件
python3 -m unittest tests.test_protocol_parity.ProtocolParityTest.test_crc_implementations_agree   # 单个用例

# agent CLI（tools/ 目录下）
../.venv/bin/python -m ccswitch_agent                    # 读一次数据库，人类可读输出（与 CC Switch 对账用）
../.venv/bin/python -m ccswitch_agent --json             # JSON；--watch 每 30 秒采样；--db 指定其他库
../.venv/bin/python -m ccswitch_agent --serve --fake     # BLE 推固定假数据，不碰数据库，单独验证链路和屏幕
../.venv/bin/python -m ccswitch_agent --serve            # BLE 推真实数据
../.venv/bin/python -m ccswitch_agent --status           # launchd 服务状态 + 最近日志；--logs 跟踪日志

# launchd 登录自启（仓库根目录）
.venv/bin/python tools/install_launchd.py install    # 或 uninstall
```

跨语言协议测试（`test_protocol_parity`）需要宿主机 C++ 编译器（Xcode CLT），缺编译器时会跳过——跳过不能当作协议一致性通过。

### 固件侧（PlatformIO）

```bash
pio run -e deskburn                # 编译正式固件
pio run -e deskburn -t upload      # 烧录；多串口设备时加 --upload-port /dev/cu.usbmodemXXXX
pio run -e pin_scanner             # 换屏排障用的引脚 / 初始化序列扫描固件
pio device monitor --baud 115200   # 串口监视
```

烧录报 `No serial data received` 时手工进下载模式：按住 BOOT → 点按 RST → 松开 BOOT → 重试。验收固件升级要看设备串口的 `[ble] accepted ...`，Mac 端 `pushed` 日志不能证明设备收下了包。

### 资源生成（仅修改图标、中文标签或金额字形时）

```bash
.venv/bin/pip install -r requirements-assets.txt
.venv/bin/python tools/generate_assets.py    # 输出 firmware/deskburn/assets.h，依赖 macOS 系统字体
```

`assets.h` 是生成产物，不要手工修改；普通固件构建直接用仓库里已提交的版本。

## 架构

数据链路：CC Switch SQLite（只读）→ `ccswitch_agent` 聚合 → BLE 每 30 秒推送 → ESP32-C3 校验并存 NVS → TFT 局部刷新。设计哲学是任何一环失败只丢当前轮：设备保留最后一次有效值，90 秒无有效包后显示 OFFLINE，宁可数字旧一轮也不显示损坏的数。

### BLE 协议是两份手写实现，必须同步修改

线格式（固定 42 字节小端 packed 包 + CRC-16/CCITT-FALSE，金额用千分之一美元的 u32 整数、Token 用千 token 的 u32 整数，都是为了避开浮点并塞进 32 位）在两处各实现一次：

- `tools/ccswitch_agent/protocol.py` —— Mac 编码端
- `firmware/deskburn/link_protocol.h` —— 设备解码端；只依赖 stdint/string，可在宿主机编译

Token 不传原始计数是实测约束：总计已到 18 亿且每月涨约 15 亿，u32 会溢出并被夹在 4.29B（一个看着合理却不再变化的数）；改 u64 又会破坏设备端 `State` 依赖的「32 位读写原子」假设，导致主循环读到半个包的乱数。

改动字段的完整流程：同步改两边 → 升 `PROTOCOL_VERSION` / `kProtocolVersion` → 跑全部测试 → 同一维护窗口内烧固件并重启 agent。只改一边时设备按版本号整包拒收、屏幕停在 OFFLINE，这是刻意的安全退化。`tests/test_protocol_parity.py` 用宿主机编译器编译真实的 `link_protocol.h` 去解 Python 编出的包，能抓出字节序、偏移和 CRC 不一致。

### Python 采集端（tools/ccswitch_agent/）

- `usage.py` —— 只读聚合（SQLite URI `mode=ro`）。SQL 口径全部是拿 model_pricing 反算验证过的实测规则，不符合直觉但不能"修正"：Token 累加按 `app_type` 区分（codex 的 input 已含缓存、claude 四项互斥；`input_token_semantics` 字段不可靠）；必须跨 `data_source` 去重（proxy 与会话导入日志成对重复，指纹必须含 `created_at`）；库不是 WAL，必须带 `busy_timeout`；总计 = `proxy_request_logs`（近 30 天明细）+ `usage_daily_rollups`（归档层），两表按日期互斥相加；本周一用星期序号手工回退（SQLite `weekday 1` 在周一当天有坑）。
- `link.py` —— BLE central：按广播名 `DeskBurn` 扫描（不按地址，macOS 会随机化外设地址）→ 连接 → 推送循环 → 指数退避重连。取数与发数解耦：读库失败跳过本轮，不掐连接。
- `__main__.py` —— CLI 入口；读库失败不终止长跑进程（CC Switch 写库时会短暂锁住）。

### 固件（firmware/）

两个 PlatformIO env 共用 `src_dir = firmware`，靠 `build_src_filter` 隔离；统一用 `.cpp`（`.ino` 会被 PlatformIO 合并，两个固件的 setup/loop 会重复定义）。

- `deskburn/deskburn.cpp` —— 渲染。局部刷新核心规则：每个 TextSlot 记住上一次内容，只擦旧包围盒再画新内容，绝不整行清屏（会把同高度的图标标签一起擦掉）。`Layout` 里的 Y 坐标是按各元素真实高度排满 240px 的，周期金额与周期 Token 之间只剩 2px 空隙，改坐标前先重算擦除带（擦除会在包围盒外多留 2px）。推送 alpha 位图期间要 `setSwapBytes(true)`（RGB565 字节序）。渲染在主循环每秒轮询，不在 BLE 回调里刷屏——SPI 会拖住蓝牙栈，且 OFFLINE 切换是超时驱动、没有回调可挂。
- `deskburn/link_ble.h` —— NimBLE 从机（比 Bluedroid 省约 100KB RAM），Mac 作 central 主动连。校验通过才更新 `g_state`；仅数值变化时写 NVS（flash 擦写寿命有限）；上电从 NVS 恢复历史值但标 OFFLINE。未启用配对加密是有意取舍（包里只有聚合数字）。
- `deskburn/assets.h` —— 生成产物：SVG 图标、中文标签、金额字形的 8 位 alpha 蒙版，运行时上色。
- `pin_scanner/` —— 自带最小 SPI 驱动的排障固件，一次烧录内运行时轮换引脚和初始化序列假设，换屏时先跑它。

### 版本锁定是实测约束，不要随手升级

`platformio.ini` 的每个 pin 都有注释说明原因：`espressif32@6.3.0`（对齐出厂固件的 Arduino core 2.0.9）；NimBLE-Arduino 锁 1.4.x（2.x 要求 Arduino core 3.x，不能混用）；TFT_eSPI 2.5.0；C++17（`link_ble.h` 的 inline 变量）。屏幕的全部配置也在 build_flags 里：ST7789 序列、BGR 色序、原生 240×320、SCLK=GPIO2（不是 GPIO10，那是板载 SPI 的 MISO）——都是 pin_scanner 实测结果。验证新面板的颜色配置必须显示 R/B 不对称的颜色（灰阶暴露不了 RGB/BGR 接反）。

## 文档

- `docs/踩坑记录.md` —— 当前代码依赖的实测约束（屏幕/SPI、局部刷新、聚合口径、BLE、烧录），改相关代码前必读。
- `docs/构建流程.md` —— 从零环境到自启部署的完整流程，含日志轮转。
- `docs/硬件说明.md` —— 板卡、屏幕参数与引脚。
