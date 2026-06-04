# platform-realtek-ameba

[English](README.md) | [中文](README_zh.md)

PlatformIO 平台，支持 **Realtek Ameba** 系列 Wi-Fi + Bluetooth 低功耗 IoT
SoC，基于官方 `ameba-rtos` SDK。

## 设计思路

本平台是一层薄胶水代码（约 600 行），构建在上游 `ameba.py` 驱动之上。
CMake、Ninja、asdk/vsdk 工具链以及每个 SoC 的构建逻辑全部留在
`ameba-rtos` 内部 —— PlatformIO 调用 `ameba.py build` 和
`ameba.py flash`，把生成的 `app.bin` 复制到标准的 `.pio/build/<env>/`
位置。

这种方式让平台始终和 SDK 自带的逻辑保持一致，而不是另起炉灶重新实现
构建系统。

## 支持的开发板

| 开发板 | SoC | 规格 | 状态 |
|---|---|---|---|
| **PKE8721DAF-C13-F10** | RTL8721Dx  | Cortex-M33 双核 (KM4 345 MHz + KM0)，Wi-Fi 4 + BLE 5.0 | ✅ 编译/烧录/串口已验证 |
| **PKE8710ECF-C53-F20** | RTL8710E   | Cortex-M33 (400 MHz) + KR4，Wi-Fi 6 + BLE 5.2 | 🟡 板级配置存在，硬件未实测 |
| **PKE8713ECM-VA4-N43** | RTL8713E   | HiFi5 + Cortex-M33 + KR4，Wi-Fi 6 + BLE 5.2 + 音频 DSP | ✅ 编译/烧录/串口已验证 |

## 快速开始（全新机器）

从一台干净的 Ubuntu/WSL2 机器到烧录板子的完整步骤。

### 1. 安装 PlatformIO Core

如果你还没装 `pio`：

```bash
# 安装 PIO Core
curl -fsSL -o get-platformio.py \
    https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py
python3 get-platformio.py

# 让 `pio` 在任意 shell 都能用（PIO 官方推荐方式）
mkdir -p ~/.local/bin
ln -sf ~/.platformio/penv/bin/pio ~/.local/bin/pio
ln -sf ~/.platformio/penv/bin/platformio ~/.local/bin/platformio

# 验证（你的 shell 需要把 ~/.local/bin 加进 PATH，必要时重启 shell）
pio --version
```

### 2. 安装本平台

从源码安装（registry 发布尚在筹备中）：

```bash
git clone https://github.com/raulcxw/platform-realtek-ameba.git
pio pkg install -g -p "file://$(pwd)/platform-realtek-ameba"
```

> `-g` 参数表示全局安装到 `~/.platformio/platforms/`。
> Ameba SDK（`framework-ameba-rtos`）此时**还没下载** —— 它会在第一次
> `pio run` 时按需拉取。

### 3. 创建项目

```bash
mkdir my-ameba-app && cd my-ameba-app
cat > platformio.ini <<'EOF'
[env:pke8721daf-c13-f10]
platform     = realtek-ameba
framework    = ameba-rtos
board        = pke8721daf-c13-f10

; ── 串口设置（烧录 / 监视器必填）─────────────────────────────────
; 如果只接了一块板子，PIO 会自动检测。
; 多板或者非 /dev/ttyUSB0 的情况下要显式设置：
;
;   Linux/WSL: /dev/ttyUSB0   (Prolific PL2303，常见的 Ameba 开发套件)
;              /dev/ttyACM0   (CDC-ACM，例如 CMSIS-DAP USB)
;   macOS:     /dev/cu.usbserial-XXXXX
;   Windows:   COM3, COM4, ...
;
; upload_port  = /dev/ttyUSB0
; monitor_port = /dev/ttyUSB0
EOF
```

> **可选板子**（替换上面的 `pke8721daf-c13-f10`）：
> - `pke8721daf-c13-f10` — RTL8721Dx（最常见的开发板）
> - `pke8710ecf-c53-f20` — RTL8710E
> - `pke8713ecm-va4-n43` — RTL8713E

### 4. 编译

```bash
pio run
```

首次编译会下载：
- Ameba **基础 SDK**（约 30 MB shallow clone —— Wi-Fi + BT，不含子模块）
- asdk/vsdk 工具链（每族约 280 MB）到 `~/rtk-toolchain/`

冷启动首次编译：约 5 分钟。之后增量编译：约 15 秒。

需要 **XDK** 高阶功能（AI 语音、TensorFlow Lite、UI/LVGL、音频）？
在**首次编译前**设置 `AMEBA_SDK_EDITION=xdk` —— 详见下方
[SDK 版本（基础 SDK vs XDK）](#sdk-版本基础-sdk-vs-xdk)。

首次编译还会自动生成：
- `src/main.c`（hello-world 模板，演示正确的 `xTaskCreate` 用法）
- SDK 必需的 `app_example/` 目录

### 5. 连接板子，确认串口

把开发板插上 USB，然后：

```bash
pio device list
# 找到板子对应的串口。常见条目：
#   /dev/ttyUSB0  ← Prolific PL2303（常见的 Ameba LogUART 桥接）
#   /dev/ttyACM0  ← CMSIS-DAP 板载调试器
```

如果 `pio device list` 显示多个串口，请在 `platformio.ini` 里显式设置
`upload_port` 和 `monitor_port`（参考第 3 步的注释段）。

### 6. 烧录

```bash
pio run -t upload
```

如果看到 "board not in download mode" 类报错，启动 `pio run -t upload`
后立刻按一下板子的 RESET 键。没有 DTR/RTS 自动复位线的 USB-UART 芯片
（如 PL2303）需要手动这么做；CP2102 / CH340 / CMSIS-DAP 一般会自动
复位。

### 7. 串口监视

```bash
pio device monitor
```

默认波特率 1.5 Mbps（Ameba LogUART 约定值）已写在板级 manifest 里。
按 `Ctrl-C` 退出。

## SDK 版本（基础 SDK vs XDK）

Realtek 把 ameba-rtos 分成两个版本：

| 版本 | 内容 | 首次下载 |
|---|---|---|
| **SDK**（默认） | Wi-Fi、蓝牙 —— 基础开发平台 | 约 30 MB |
| **XDK**（扩展） | 在 SDK 基础上**额外包含** AI 语音、TensorFlow Lite（tflite_micro）、UI（LVGL）、音频 | 约 1.1 GB |

大部分项目只需基础 SDK，所以默认就拉它。需要扩展版的话，**在首次编译前**
设置环境变量（该选择只在 SDK 首次 clone 时生效一次）：

```bash
# Linux / macOS
AMEBA_SDK_EDITION=xdk pio run

# Windows (PowerShell)
$env:AMEBA_SDK_EDITION="xdk"; pio run
```

```yaml
# CI (GitHub Actions)
- run: pio run
  env:
    AMEBA_SDK_EDITION: xdk
```

**已经拉了基础 SDK，后来想单独加某个组件？** 由于 edition 变量只在首次
clone 时生效，直接补对应子模块即可（浅克隆）：

```bash
cd ~/.platformio/packages/framework-ameba-rtos
git submodule update --init --depth 1 component/audio    # 或 component/ui 等
```

想把整个包从 SDK 整体切到 XDK，则重装：

```bash
pio pkg uninstall -g -p framework-ameba-rtos
AMEBA_SDK_EDITION=xdk pio run
```

> 用 `$AMEBA_SDK_DIR` 指向本地 checkout 时，edition 变量被忽略 ——
> 子模块由你在那个 checkout 里自行管理。

## 升级 SDK

```bash
pio pkg update -p framework-ameba-rtos
```

就这一条 —— 下次 `pio run` 会自动同步 SDK 新加入的 Python 依赖。
不用 `source env.sh`、不用手动 `pip install`。本平台用
`tools/requirements.txt` 的内容哈希追踪 SDK venv 状态，文件一变就
自动 resync。

如果需要强制重建 venv（例如手动 `pip uninstall` 之后破坏了 venv 状态）：

```bash
rm ~/.platformio/packages/framework-ameba-rtos/.venv/.pio_requirements_sha256
pio run
```

## PlatformIO 命令支持情况

| 命令 | 支持 | 备注 |
|---|:---:|---|
| `pio run`（编译） | ✅ | 委托给 `ameba.py build` |
| `pio run -t upload` | ✅ | 委托给 `ameba.py flash` |
| `pio run -t erase` | ✅ | 全片擦除 SPI Flash + 重新烧录当前项目 |
| `pio run -t clean` | ✅ | 同时清理 `.pio/build/<env>/` 和 `<PROJECT>/build_<SOC>/` |
| `pio run -t buildfs` | ✅ | 把 `data/` 打包成 LittleFS 镜像，大小匹配 VFS1 分区 |
| `pio run -t uploadfs` | ✅ | 烧录 LittleFS 镜像到 VFS1 区（不影响 app/boot） |
| `pio run -t menuconfig` | ✅ | 调用 `ameba.py menuconfig <SOC>` curses UI |
| `pio run -t size` | ✅ | 多核程序尺寸（KM4 + KM0/KR4 汇总） |
| `pio device monitor` | ✅ | PIO 标准 miniterm，默认 1.5 Mbps LogUART |
| `pio check` | ✅ | cppcheck / clang-tidy 能解析 SDK 头文件 |
| VSCode IntelliSense | ✅ | 自动从 SDK 构建导出 `compile_commands.json` |
| 多 env 并行（`pio run -e a -e b`） | ✅ | 每个 env 独立 `TARGET_SOC`，无共享状态竞争 |
| `pio debug`（GDB） | 🟡 | OpenOCD/J-Link 已在 board manifest 里配置，硬件未实测 |
| `pio test`（unity） | 🔴 | 计划中 |
| OTA 烧录 | 🔴 | 计划中 |
| `pio lib install` / `lib_deps` | ❌ | 设计上不支持 —— Ameba 组件在 SDK 源码树里，不是 PIO library |

## 目录结构

```
platform.json              PIO 平台 manifest
platform.py                RealtekamebaPlatform(PlatformBase) — 拉取 SDK + venv，注册调试工具
builder/main.py            SCons 入口；调用 ameba.py，把 build_<SOC>/app.bin 复制到 PIO BUILD_DIR
builder/frameworks/        Framework 发现
boards/<board>.json        每个板子的 manifest（MCU 系列、调试、烧录默认值）
tests/                     回归测试（lint + unit + integration）
.github/workflows/ci.yml   GitHub Actions：每次 push 跑 lint + unit + integration
```

## 系统要求

- Linux（Ubuntu / WSL2 已测试）
- Python 3.9+
- PlatformIO Core 6.x
- `ameba-rtos` 仓库会在首次编译时自动克隆

## 测试

回归测试在 `tests/` 目录下（lint + unit + integration + 硬件 smoke）。
本地运行方式见 [`tests/README.md`](tests/README.md)；GitHub Actions
在每次 push 时跑 lint + unit + integration。

## 许可证

Apache-2.0（与 PlatformIO 生态系统对齐）。
