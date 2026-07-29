# DeskBurn 固件成品

这里保存 Classic、Swiss Poster 和 Midnight Buddy 三版可烧录固件，便于从
GitHub 下载后快速切换。

| 文件 | 用途 | 写入地址 |
|---|---|---:|
| `DeskBurn-Classic-app.bin` | 已安装 DeskBurn 的设备切回经典页面，保留 NVS | `0x10000` |
| `DeskBurn-Classic-full.bin` | 全新/已擦除设备完整安装经典页面 | `0x0` |
| `DeskBurn-Swiss-Poster-app.bin` | 已安装 DeskBurn 的设备切换到瑞士海报页面，保留 NVS | `0x10000` |
| `DeskBurn-Swiss-Poster-full.bin` | 全新/已擦除设备完整安装瑞士海报页面 | `0x0` |
| `DeskBurn-Midnight-Buddy-app.bin` | 已安装 DeskBurn 的设备切换到深色可爱页面，保留 NVS | `0x10000` |
| `DeskBurn-Midnight-Buddy-full.bin` | 全新/已擦除设备完整安装深色可爱页面 | `0x0` |

## 推荐：从源代码直接烧录

```bash
# 经典版
pio run -e deskburn -t upload

# Swiss Poster 版
pio run -e deskburn_swiss -t upload

# Midnight Buddy 版
pio run -e deskburn_buddy -t upload
```

## 烧录成品 bin

先安装 `esptool`，然后把端口替换成实际串口：

```bash
# 已安装过 DeskBurn：快速换页面，不改分区表和 NVS
esptool.py --chip esp32c3 --port /dev/cu.usbmodemXXXX \
  write_flash 0x10000 DeskBurn-Swiss-Poster-app.bin

# 全新设备：烧录完整镜像
esptool.py --chip esp32c3 --port /dev/cu.usbmodemXXXX \
  write_flash 0x0 DeskBurn-Swiss-Poster-full.bin
```

完整镜像会覆盖 `0x0` 到应用末尾之间的区域，其中包含 NVS；切换已有设备时优先
使用 `app.bin` 或 PlatformIO 的对应环境。下载后可用 `SHA256SUMS` 校验文件完整性。

## 重新生成

```bash
.venv/bin/python tools/package_firmware.py
```

打包脚本会重新编译三个页面，并更新六个 bin 和校验文件。
