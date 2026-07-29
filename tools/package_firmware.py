#!/usr/bin/env python3
"""编译并打包所有 DeskBurn 展示版本的可烧录固件。

每个页面输出两种文件：

* ``*-app.bin``：只写应用分区，适合已经运行 DeskBurn 的设备快速切换页面，
  不覆盖 NVS 中保存的最后一次数据。
* ``*-full.bin``：合并 bootloader、分区表、boot_app0 和应用，可从 0x0 一次写入
  全新或已擦除的 ESP32-C3。

用法：
    .venv/bin/python tools/package_firmware.py
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = PROJECT_ROOT / ".pio" / "build"
RELEASE_DIR = PROJECT_ROOT / "firmware" / "releases"

VARIANTS = {
    "deskburn": "DeskBurn-Classic",
    "deskburn_swiss": "DeskBurn-Swiss-Poster",
}


def platformio_python() -> Path:
    """从 pio 启动脚本读取其 Python，确保 esptool 的依赖环境完整。"""
    executable = shutil.which("pio")
    if executable is None:
        raise RuntimeError("找不到 pio，请先安装 PlatformIO Core")
    first_line = Path(executable).read_text().splitlines()[0]
    if not first_line.startswith("#!"):
        raise RuntimeError(f"无法从 {executable} 读取 Python shebang")
    return Path(first_line[2:])


def preferred_package_path(pattern: str) -> Path:
    """查找 PlatformIO 包文件，并优先选择带锁定版本后缀的目录。"""
    candidates = list((Path.home() / ".platformio" / "packages").glob(pattern))
    if not candidates:
        raise RuntimeError(f"PlatformIO 包中找不到 {pattern}")
    candidates.sort(key=lambda path: ("@" not in str(path), str(path)))
    return candidates[0]


def build_all() -> None:
    """一次构建所有页面环境，任何一个失败都停止打包。"""
    command = ["pio", "run"]
    for environment in VARIANTS:
        command.extend(["-e", environment])
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def merge_full_image(environment: str, output: Path) -> None:
    """按照 PlatformIO 上传偏移合并一个可从 0x0 写入的完整镜像。"""
    build_dir = BUILD_ROOT / environment
    esptool = preferred_package_path("tool-esptoolpy*/esptool.py")
    boot_app0 = preferred_package_path(
        "framework-arduinoespressif32*/tools/partitions/boot_app0.bin"
    )

    subprocess.run(
        [
            str(platformio_python()),
            str(esptool),
            "--chip",
            "esp32c3",
            "merge_bin",
            "-o",
            str(output),
            "--flash_mode",
            "dio",
            "--flash_freq",
            "80m",
            "--flash_size",
            "4MB",
            "0x0000",
            str(build_dir / "bootloader.bin"),
            "0x8000",
            str(build_dir / "partitions.bin"),
            "0xe000",
            str(boot_app0),
            "0x10000",
            str(build_dir / "firmware.bin"),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def sha256(path: Path) -> str:
    """计算发布文件的 SHA-256，供下载后检查完整性。"""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_all() -> None:
    """复制应用镜像、生成完整镜像并写出统一校验文件。"""
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    packaged: list[Path] = []

    for environment, release_name in VARIANTS.items():
        app_output = RELEASE_DIR / f"{release_name}-app.bin"
        full_output = RELEASE_DIR / f"{release_name}-full.bin"
        shutil.copy2(BUILD_ROOT / environment / "firmware.bin", app_output)
        merge_full_image(environment, full_output)
        packaged.extend([app_output, full_output])

    checksum_lines = [
        f"{sha256(path)}  {path.name}" for path in sorted(packaged)
    ]
    (RELEASE_DIR / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n")


def main() -> None:
    """构建并生成 GitHub 可分发固件。"""
    build_all()
    package_all()
    print(f"packaged firmware in {RELEASE_DIR}")


if __name__ == "__main__":
    main()
