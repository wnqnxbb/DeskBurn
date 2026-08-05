"""DeskBurn 设备绑定的本地持久化。

绑定保存固件广播的稳定名称，不保存 Bleak 在 macOS 上返回的随机化地址。配置里
没有密钥或消费数据，但仍使用原子替换，避免进程中断留下半个 JSON 文件。
"""

from __future__ import annotations

import json
from pathlib import Path

from .protocol import is_device_name

DEFAULT_BINDING_PATH = (
    Path.home() / "Library" / "Application Support" / "DeskBurn" /
    "device.json"
)


class BindingError(ValueError):
    """绑定配置不存在、损坏或设备名无效。"""


class MultipleDevicesError(BindingError):
    """首次扫描发现多台设备，不能安全地自动选择。"""


def choose_initial_binding(device_names: list[str]) -> str | None:
    """首次扫描只在候选唯一时自动选择，无设备时等待、多设备时报错。"""
    names = sorted(set(device_names))
    if not names:
        return None
    if len(names) > 1:
        raise MultipleDevicesError(
            f"发现多台未绑定设备：{', '.join(names)}；请运行 --bind DEVICE"
        )
    return names[0]


def load_binding(path: Path = DEFAULT_BINDING_PATH) -> str | None:
    """读取已绑定的广播名；尚未绑定时返回 ``None``。"""
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        device_name = value["device_name"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise BindingError(
            f"设备绑定配置损坏：{path}；请运行 --forget-device 后重新绑定"
        ) from error
    if not isinstance(device_name, str) or not is_device_name(device_name):
        raise BindingError(
            f"设备绑定名称无效：{device_name!r}；请运行 --forget-device 后重新绑定"
        )
    return device_name


def save_binding(device_name: str,
                 path: Path = DEFAULT_BINDING_PATH) -> None:
    """原子保存一个经过格式校验的稳定设备名。"""
    if not is_device_name(device_name):
        raise BindingError(f"不是有效的 DeskBurn 设备名：{device_name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"device_name": device_name}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def clear_binding(path: Path = DEFAULT_BINDING_PATH) -> bool:
    """删除绑定；返回此前是否存在配置。"""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
