"""BLE 链路：Mac 作 central，主动连接 ESP32 并周期推送聚合数据。

由 Mac 主动连是刻意的选择：企业 Wi-Fi 往往要求 802.1X 认证，而且客户端隔离
可能直接封死设备到 Mac 的路径。BLE 不依赖局域网，也不经过公网。

设计上把「取数」和「发数」解耦：读库失败不影响已建立的连接，连接断了也不影响
下一轮取数。任何一环出问题都只是这一轮没送到，屏幕自己会在超时后切 OFFLINE。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from bleak import BleakClient, BleakScanner

from . import protocol
from .usage import DEFAULT_DB_PATH, UsageSnapshot, read_snapshot

logger = logging.getLogger(__name__)

PUSH_INTERVAL_SECONDS = 30

# 数值长时间不变时，每这么多轮打一行心跳。20 轮 = 10 分钟，既能确认服务还活着，
# 又不会让日志淹没在重复行里。
_HEARTBEAT_EVERY_ROUNDS = 20

# 扫描单次超时。设备就在手边，10 秒够了；太长会让重连迟迟不返回。
SCAN_TIMEOUT_SECONDS = 10.0

# 重连退避：连不上时逐步拉长间隔，避免设备断电期间 Mac 一直空转扫描。
_BACKOFF_START_SECONDS = 2.0
_BACKOFF_MAX_SECONDS = 60.0


async def _find_device(name: str = protocol.DEVICE_NAME):
    """按广播名查找设备。

    不用地址匹配：macOS 出于隐私会把外设 MAC 换成本机生成的 UUID，换 Mac 或
    重装系统后同一块板子的地址就变了，写死地址会莫名失效。
    """
    return await BleakScanner.find_device_by_name(
        name, timeout=SCAN_TIMEOUT_SECONDS
    )


async def _push_loop(client: BleakClient, db_path: Path) -> None:
    """在一条已建立的连接上周期推送，直到连接断开。

    读库异常在这里吞掉：CC Switch 写库时可能短暂锁住，跳过这一轮就行，
    没必要把连接也断掉重建。
    """
    # 数值不变就不打日志，只每 kHeartbeatEvery 轮打一次心跳。空闲时段每 30 秒
    # 一行完全相同的记录没有信息量，却会把真正有价值的状态变化淹掉。
    last_line: str | None = None
    quiet_rounds = 0

    while client.is_connected:
        try:
            snapshot = read_snapshot(db_path)
        except Exception as error:
            logger.warning("read failed, skipping this round: %s", error)
        else:
            await client.write_gatt_char(
                protocol.USAGE_CHAR_UUID,
                protocol.encode(snapshot),
                response=False,
            )
            line = (f"today=${snapshot.today_cost_usd:.2f} "
                    f"tokens={snapshot.today_tokens} "
                    f"week=${snapshot.week_cost_usd:.2f} "
                    f"month=${snapshot.month_cost_usd:.2f} "
                    f"total=${snapshot.total_cost_usd:.2f}")
            if line != last_line:
                logger.info("pushed %s", line)
                last_line = line
                quiet_rounds = 0
            else:
                quiet_rounds += 1
                # 心跳的作用是区分「没变化」和「服务已经死了」。
                if quiet_rounds >= _HEARTBEAT_EVERY_ROUNDS:
                    logger.info("unchanged for %d rounds, still pushing %s",
                                quiet_rounds, line)
                    quiet_rounds = 0
        await asyncio.sleep(PUSH_INTERVAL_SECONDS)


async def run(db_path: Path | str = DEFAULT_DB_PATH,
              *, fake: bool = False) -> None:
    """连接、推送、断线重连，永不返回。

    整个函数就是一个「扫描 → 连接 → 推送到断开 → 退避 → 重来」的循环。
    launchd 会在进程意外退出时重启它，但正常情况下这里自己就能扛住
    设备断电、蓝牙关闭、Mac 睡眠唤醒这些常见中断。
    """
    backoff = _BACKOFF_START_SECONDS

    while True:
        try:
            device = await _find_device()
            if device is None:
                logger.info("device not found, retrying in %.0fs", backoff)
            else:
                async with BleakClient(device) as client:
                    logger.info("connected to %s", device.address)
                    # 连上就重置退避：这次的中断与上次的原因无关。
                    backoff = _BACKOFF_START_SECONDS
                    if fake:
                        await _push_fake(client)
                    else:
                        await _push_loop(client, Path(db_path))
                logger.info("disconnected")
        except Exception as error:
            logger.warning("link error: %s", error)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _BACKOFF_MAX_SECONDS)


async def _push_fake(client: BleakClient) -> None:
    """推固定假数据，用来在不碰数据库的情况下验证链路和屏幕刷新。"""
    snapshot = protocol.fake_snapshot()
    while client.is_connected:
        await client.write_gatt_char(
            protocol.USAGE_CHAR_UUID, protocol.encode(snapshot), response=False
        )
        logger.info("pushed fake today=$%.2f", snapshot.today_cost_usd)
        # 每轮微调今日金额，便于确认屏幕真的在刷新而不是停在首帧。
        snapshot = UsageSnapshot(
            today_cost_usd=snapshot.today_cost_usd + 0.37,
            today_tokens=snapshot.today_tokens + 285_000,
            week_cost_usd=snapshot.week_cost_usd,
            month_cost_usd=snapshot.month_cost_usd,
            total_cost_usd=snapshot.total_cost_usd + 0.37,
            updated_at=snapshot.updated_at,
        )
        await asyncio.sleep(PUSH_INTERVAL_SECONDS)
