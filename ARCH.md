# 架构边界与能力对比

> **目的**：给未来贡献者、code review、PIO Registry 审稿人一份诚实的能力盘点，说清楚 platform-amebartos 在 PIO 生态里能干什么、不能干什么、以及为什么这么取舍。
>
> **基准对比对象**：[`platform-espressif32`](https://github.com/platformio/platform-espressif32)（PIO 官方维护的 ESP-IDF 平台），是同类"vendor SDK 接入 PIO"任务里最成熟的样本。

---

## 1. 设计哲学的根本差异

| 维度 | platform-amebartos（本项目） | platform-espressif32（参照系） |
|---|---|---|
| **设计哲学** | **黑盒委托**：subprocess 调 `ameba.py build/flash` | **白盒解析**：CMake File API + PIO 接管编译图 |
| **PIO 与编译过程的关系** | 完全不参与，只 `subprocess.call` 然后等结果 | 解析每个组件的 includes/defines/flags，自己 dispatch |
| **代码量** | 590 行 | 6,847 行（其中 `espidf.py` 单文件 2,373 行）|
| **SDK 改动** | 0 | 0（但 ESP-IDF 设计上为 PIO 接入做了 `idf-as-component` 模式支持）|
| **跟随上游升级的成本** | 几乎为零（`ameba.py` CLI 稳定即可）| 高（CMake 树/组件系统每次大改要适配）|
| **维护者技能要求** | 会 PIO + 会读 `ameba.py` 输出即可 | 必须深度理解 ESP-IDF + CMake File API + SCons |

**这一条决定了下面所有差异**：我们选择"PIO 不知道编译细节"换取"代码精简 + 跟随上游零成本"，代价是失去若干依赖元数据的 PIO 高级功能。

---

## 2. PIO 标准功能可用性矩阵

🟢 = 已支持并验证 / 🟡 = 部分/待完善 / 🔴 = 不支持

### 2.1 核心构建-烧录链路

| 功能 | espressif32 | amebartos v0.1 | amebartos v0.2 | 备注 |
|---|---|---|---|---|
| `pio run`（编译） | 🟢 | 🟢 | 🟢 | RTL8721F 真编通，`firmware.bin` 在 `.pio/build/<env>/` |
| `pio run -t upload`（烧录） | 🟢 | 🟢 | 🟢 | 含 `--remote-server`/`--remote-password`/`--port`/`--baudrate`/`--memory-type`/`--chip-erase` 透传 |
| `pio run -t clean` | 🟢 | 🟡 | 🟢 | v0.2 通过 `pio run -t ambsdk-clean` 同步清 SDK + `.pio/` + `compile_commands.json` |
| 增量编译 | 🟢 智能 | 🟢 但靠上游 | 🟢 同 v0.1 | SDK 内部用 ninja，PIO 没有可见性，全量/增量决策权在 ameba.py 手里 |

### 2.2 开发体验功能

| 功能 | espressif32 | amebartos v0.1 | amebartos v0.2 | 影响面 |
|---|---|---|---|---|
| **VSCode IntelliSense**（写代码补全/跳转） | 🟢 强 | 🔴 **无** | 🟢 **支持** | v0.2 自动导出 cmake 生成的 `compile_commands.json`（627 条目，7.1 MB）到工程根 + `.pio/build/<env>/` |
| `pio device monitor`（串口监视器） | 🟢 | 🔴 暂不支持 | 🟢 `pio run -t monitor_ambsdk` | 透传 `--remote-server`/`--remote-password`/`--port`/`--baudrate`，已对真硬件 COM40 验证连接 |
| `pio run -t menuconfig`（图形化 SDK 配置） | 🟢 | 🔴 暂不支持 | 🟢 | v0.2 注册为 PIO custom target，调 `ameba.py menuconfig <SOC>`，curses UI 直通用户终端 |
| `pio check`（cppcheck/clang-tidy） | 🟢 | 🔴 | 🟡 应该可用 | v0.2 已生成 `compile_commands.json`，PIO check 应该能消费，但未真验证 |
| `pio debug`（GDB） | 🟢 | 🟡 占位 | 🟡 占位 | platform.py 里写了 OpenOCD/JLink stub，未真验证 |

### 2.3 PIO 生态深度集成

| 功能 | espressif32 | amebartos v0.1 | amebartos v0.2 | 是否计划做 |
|---|---|---|---|---|
| `pio lib install` PIO 库管理 | 🟢 | 🔴 **不支持** | 🔴 **不支持** | 🟡 v1.0 不做（哲学冲突），v1.1+ 视社区需求开 git URL + portable 库支持，详见 §3.4 |
| `lib_deps` 自动拉外部库 | 🟢 | 🔴 | 🔴 | 🟡 同上 |
| `pio test`（unity 单元测试） | 🟢 | 🔴 | 🔴 | 🟡 v1.0+ 看需求 |
| 文件系统镜像 `pio run -t buildfs`（SPIFFS/LittleFS） | 🟢 | 🔴 | 🔴 | 🟡 v0.3+，Ameba 有 LittleFS 但没接 |
| OTA 烧录 | 🟢 | 🔴 | 🔴 | 🟡 v0.3 计划 |
| **多 env 并行**（`pio run -e a -e b`） | 🟢 | 🟡 **危险** | 🟢 | v0.2 用 `TARGET_SOC` 环境变量绕开 `soc_info.json`，多 env 之间不再竞争 |

---

## 3. 重点差距详解

### 3.1 IntelliSense / `compile_commands.json` —— ✅ v0.2 已修复

**v0.1 时的痛点**（保留作为历史记录）：

```c
// platform-amebartos v0.1 🔴
#include "wifi_api.h"      // 红波浪线："找不到此文件"（实际编译没问题）
int x = wifi_init();        // 没有补全、没有签名提示
```

**v0.2 修法**：cmake 默认就在 `build_<SOC>/build/compile_commands.json` 生成完整编译命令数据库（627 条编译条目，含每个 `.c` 的所有 `-I -D -f`）。我们只需要把它**拷到 PIO 工程根 + `.pio/build/<env>/`**。

```python
# builder/main.py（v0.2 新增 ~25 行）
def _export_compile_commands():
    src = join(SDK_DIR, f"build_{SOC}", "build", "compile_commands.json")
    if not os.path.isfile(src):
        return
    project_dir = env.subst("$PROJECT_DIR")
    for dst in [
        join(PROJECT_BUILD_DIR, "compile_commands.json"),  # PIO 标准
        join(project_dir, "compile_commands.json"),        # VSCode 自动发现
    ]:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
```

每次 `pio run` 后端的 `build_firmware()` 自动调一次。**用户体验**：

```c
// platform-amebartos v0.2 ✅
#include "wifi_api.h"      // 自动补全可用，跳转定义可用
int x = wifi_init();        // 函数签名提示
```

**已验证**：在 RTL8721F 工程跑 `pio run` 后，`compile_commands.json` 准确出现在两个位置，体积 7.1 MB（627 条目），编译器路径 `.platformio/platforms/amebartos/.cache/rtk-toolchain/asdk-12.3.1-4600/.../arm-none-eabi-gcc` 正确（VSCode/clangd 直接能用）。

### 3.2 `pio device monitor` —— ✅ v0.2 已支持（含双向交互）

每次烧完固件都要看串口输出，v0.1 时用户得手动开第二个终端跑 `python ameba.py monitor`。

`ameba.py monitor` 接受跟 flash 相同的 `--remote-server`/`--port` 参数，并提供 **`-reset`**（软重启 + 抓 `ROM:[` 之后的 boot log）和 **`--no-console`**（非交互模式从 stdin 读命令喂给板子）两个关键开关。v0.2 在 `builder/main.py` 注册了 SCons custom target：

```python
env.AddCustomTarget(
    name="monitor_ambsdk",
    dependencies=None,
    actions=serial_monitor,
    title="Serial Monitor (ambsdk)",
)
```

**用法**：

```bash
pio run -t monitor_ambsdk          # 走 [env] 里 board_upload.* / upload_port

# 在 platformio.ini 里加 custom_monitor_reset = yes 可触发软重启抓 boot log
# 在 platformio.ini 里加 custom_monitor_no_console = yes 强制非交互
# stdin 不是 TTY 时（CI/log capture）会自动启用 no-console
```

**已端到端验证（真硬件 RTL8721F EVB / COM40 / 127.0.0.1:58916）**：

1. **接收 boot log**：触发软重启后捕获 57 行带时间戳的真实串口输出，从 `ROM:[V1.0]` 到 `[WLAN-A] IPS in`，包含 KM4TZ + KM4NS 双核启动、PSRAM/Flash ID、Wi-Fi 双频初始化全过程。`Build Time: May 27 2026 22:03:38` 与我们烧入的固件编译时间戳吻合。

2. **发送命令**：通过 stdin 喂 `?` 到 monitor，板子回复内置 shell 的完整帮助（`DW`/`EW`/`REBOOT`/`EFUSE`/`TICKPS` 五条命令）。再发 `DW 0 4` 读 0x00000000 处 4 个 word，板子返回 `30001000 00000021 00000031 00000035`（30001000 是 ARM MSP 栈顶，符合 Cortex-M 启动结构）。

也就是说**完全等同于本地串口体验**：能看 boot log、能发命令、能读内存。

> **为什么不直接接 PIO 内置的 `pio device monitor`?**  
> PIO 的 monitor 走 pyserial 打开本机串口，**不支持 Realtek 远程串口服务器**（一种 socket 协议）。所以我们注册了独立的 `monitor_ambsdk` target 调上游 `ameba.py monitor`，它原生懂这个协议。

> **v0.2 关键波特率默认值**：Ameba SDK 的 LogUART 全 SoC 硬编码 `LOGUART_BAUDRATE=1500000`（搜 `component/at_cmd/atcmd_bt_mp.c:25`），不是 PIO 默认的 9600，也不是常见的 115200。`builder/main.py` 把 `monitor_speed` 默认值改成 1500000，避免新手用户连接看到一片乱码就以为坏了。

### 3.3 多 env 并行 —— ✅ v0.2 已修复

**v0.1 风险**：`pio run -e rtl8721f -e rtl8730e` 会同时跑两份编译。两个进程都会写 SDK 根目录的 `soc_info.json`，互相覆盖。

**v0.2 修法**：研究 `tools/scripts/ameba_soc_utils.py` 发现 `parse_soc_info()` **优先读 `$TARGET_SOC` 环境变量**，找不到才读 `soc_info.json`。

```python
# tools/scripts/ameba_soc_utils.py:57
env_soc = os.environ.get('TARGET_SOC')
if env_soc:
    soc_name = env_soc
elif self.info_file and os.path.exists(self.info_file):
    # ... read soc_info.json
```

`builder/main.py` 在 `_make_sdk_env()` 里加了一行：

```python
sdk_env["TARGET_SOC"] = SOC   # SOC 已经是从 board.get("build.soc") 读出来的
```

每个 env 的 subprocess 拿到自己独立的 TARGET_SOC，互相不踩。`soc_info.json` 仍会被 `ameba.py soc` 写一次（无害，多个进程写同一个值），但**实际决定 SOC 的不再是这个文件**。

**已验证**：rtl8721f → rtl8730e 顺序编译，两个 SoC 各自得到 `build_RTL8721F/` 和 `build_RTL8730E/`，互不污染。

### 3.4 `lib_deps` / PIO Lib Registry —— v1.0 不做，未来视情况

#### 真相 1：espidf.py 真的接了 PIO Lib Builder

之前版本本节写"espidf.py 解析组件树**模拟**成 PIO 库管理器看得懂的格式"——**这是事实错误**。实际方向反过来：

```python
# platform-espressif32/builder/frameworks/espidf.py:46
from platformio.builder.tools.piolib import ProjectAsLibBuilder

# espidf.py:114-128
def get_project_lib_includes(env):
    project = ProjectAsLibBuilder(env, "$PROJECT_DIR")
    project.install_dependencies()
    project.search_deps_recursive()
    for lb in env.GetLibBuilders():
        ...  # 把 PIO lib 的 include 塞进 IDF 的 cmake CPPPATH
```

ESP-IDF 用户写 `lib_deps = bblanchon/ArduinoJson` 时，espidf.py 走 `ProjectAsLibBuilder` 把库装进 `.pio/libdeps/`，再把 include + 源码塞进 IDF 的 cmake build graph（通过 `__LIB_DEPS` 链路 + `find_lib_deps` 解析）。**ESP-IDF + PIO 是真支持 lib_deps 的**。

#### 真相 2：但能编通的库**远远少于 17000**

PIO Lib Registry 有 ~17000 个库，但兼容性是**库自己声明**的。看 `library.json`：

```json
{
  "name": "ArduinoJson",
  "platforms": ["espressif32", "atmelsam", "ststm32", ...],
  "frameworks": ["arduino"]
}
```

`piolib.py:1070 IsCompatibleLibBuilder` 严格检查：

```python
if compat_mode == "strict" and not lb.is_platforms_compatible(env["PIOPLATFORM"]):
    return False
if not lb.is_frameworks_compatible(env.get("PIOFRAMEWORK")):
    return False
```

→ `platform = amebartos` + `framework = ambsdk` 来调任何库时，**默认全跳过**，因为没有库声明兼容这个平台/框架。

实际能编的库需满足：
1. **声明 `framework: ["*"]` 或不声明**（纯 portable C/C++ 库），且
2. **源码不依赖 Arduino API**（`<Arduino.h>`、`WiFi.h`、`Wire.h` 等都用不了）

PIO Lib Registry **绝大多数热门库（ArduinoJson 除外）都是 Arduino 生态专用的**——FastLED、Adafruit_GFX、PubSubClient、AsyncTCP……即使强制 `lib_compat_mode = off`，源码 include `<Arduino.h>` 也根本编不通。

→ 真正可用的库估计在 **~50-200 个**（纯模板/纯 C 算法库），比如 ArduinoJson、nlohmann/json、protobuf-c、tinycbor、FFT 库等。

#### 真相 3：RTOS SDK 的真实生态在 SDK 内部

Realtek IoT 客户写 RTL8721F 项目时实际依赖什么：

| 依赖类型 | 来源 |
|---|---|
| WiFi/BLE/TCP-IP 驱动 | **SDK 内置** (`component/wifi`, `component/lwip`, `component/bluetooth`) |
| FreeRTOS | **SDK 内置** |
| mbedTLS | **SDK 内置** |
| HTTP / MQTT / WebSocket / CoAP | **SDK 内置或客户自移植** |
| OTA / 文件系统 / NVS | **SDK 内置** |
| AI / 语音 | Realtek `aivoice` / `tflite_micro`（SDK submodule） |
| 厂商驱动（屏幕/传感器） | 厂商提供 SDK，或客户自写 |

**RTOS 生态的底层假设**——SDK 是封闭主战场，外部库是辅助。客户买 RTL8721F 不是冲着"能用 PIO 17000 库"来的，是冲着 Realtek WiFi 6 + SDK 完整度。

#### 真相 4：portable 库其实**有路径**支持

PIO 的 `lib_deps` 也支持 git URL 直链：

```ini
lib_deps =
    https://github.com/nlohmann/json.git
    https://github.com/h2non/farmhash-cpp.git
```

PIO 拉到 `.pio/libdeps/`，**只要源码不依赖 Arduino API**，platform-amebartos 的 builder 理论上能编——因为 v0.3 已经有 `EXTRA_CFLAGS` 透传机制，把库源码 + include 写进一个 `_pio_lib_deps_fragment.cmake` 让 SDK 的 `app_example/CMakeLists.txt` include 即可。

技术路径**已经铺好**，没启用而已。

#### 决策：v1.0 不做，理由如下

1. **ESP-IDF 本身也只是名义支持**：尽管 espidf.py 接了 PIO Lib Builder，但 ESP-IDF 用户的真实生态在 [components.espressif.com](https://components.espressif.com/)（IDF Component Registry），不是 PIO Lib Registry。社区已经多年接受这个事实，没人喊"缺陷"。
2. **17000 库的 80%+ 对 RTOS 平台天然无效**：因为是 Arduino 专用。
3. **RTOS 用户需求点不在这里**：他们要的是 SDK 完整度、芯片性能、量产稳定性。
4. **真要做时工作量可控**：~150-250 行代码（参考 espidf.py:114-128 + `__LIB_DEPS` 链路），**约 2-3 人天**。
5. **触发条件**：等用户开始问"我能不能用 ArduinoJson"，3 个真实 issue 后再做不晚。

#### 哲学

**让 PIO 干 PIO 擅长的事（构建编排+烧录+IDE 集成），让 SDK 干 SDK 擅长的事（组件管理+硬件抽象）**。

`pio lib install` 不做 = 这个哲学的延伸。SDK 的 Kconfig 体系是更准确的组件管理范式（依赖关系、互斥项、子选项树），强行把它压扁成 PIO 的 `lib_deps` 列表是范式倒退。

---

## 4. 路线图

### v0.2（已完成 — 2026-05-27）

| 功能 | 实际代码量 | 验证 |
|---|---|---|
| `compile_commands.json` 导出（IntelliSense）| ~25 行 | ✅ 7.1 MB / 627 条目，工程根 + `.pio/build/<env>/` 双份 |
| `pio run -t monitor_ambsdk` 接入 | ~40 行 | ✅ 真硬件 COM40 连接成功 |
| `pio run -t menuconfig` 接入 | ~20 行 | ✅ Kconfig 加载到 curses UI 入口（终端中可交互）|
| `pio run -t ambsdk-clean` 完整化 | ~25 行 | ✅ 同步清 SDK build_<SOC>/ + .pio/ + 工程根 compile_commands.json |
| 多 env 并行隔离（`TARGET_SOC` env var）| ~5 行（核心 1 行）| ✅ rtl8721f 与 rtl8730e 串行编互不干扰 |
| **总计 v0.2 增量** | **~120 行**（少于原估计 300）| 全部 ✅ |

### v0.3 计划（暂未排期）

| 优先级 | 功能 | 难度 | 代码量 |
|---|---|---|---|
| 中 | OTA 烧录支持 | 🟡 中 | ~80 行 |
| 中 | `pio debug` 真验证（OpenOCD config） | 🟡 中 | 验证为主 |
| 中 | 文件系统镜像 `pio run -t buildfs`（LittleFS） | 🟡 中 | ~100 行 |
| 低 | `pio test`（unity） | 🔴 难 | ~300 行 |
| 🟡 v1.1+ | `lib_deps` git URL + portable 库支持 | 🟡 中（参考 espidf.py:114-128 + ProjectAsLibBuilder） | ~150-250 行（约 2-3 人天） |
| ❌ 不做 | `lib_deps` 全 17000 PIO 库（含 Arduino 库） | — | 范式冲突，详见 §3.4 |

---

## 5. 给上游用户的诚实陈述

### 适合什么场景

- ✅ 已有 ameba-rtos SDK 工作流，想用 PIO 做**统一的构建/烧录入口**（CLion + PIO 插件、VSCode + PIO 插件、CI/CD `pio run`）
- ✅ 团队多人/多机，想要"clone 工程 → `pio run` → 编出固件"的一致体验
- ✅ 同时维护多个 Ameba SoC 项目（多 env 配置在一份 platformio.ini，v0.2 起多 env 并行安全）
- ✅ Realtek 内部 demo 一致化（统一 `[env:rtl8721f]` `[env:rtl8730e]` 命名）
- ✅ **v0.2 起也适合** 想要 VSCode IntelliSense / pio monitor / pio menuconfig 的开发者

### 不适合什么场景

- ❌ Arduino 风格"装个库就用"开发者（请用 LibreTiny，已支持 RTL8710BN/AmebaZ2）
- ❌ 需要 `pio test` 单元测试体系（v1.0+ 看需求）
- ❌ 需要 Espressif 那种"PIO 库管理器统一管所有依赖"
- ❌ 需要 OTA 升级 / 文件系统镜像 PIO 集成（v0.3+）

### v0.2 的真实定位

**v0.2 是 PIO 集成的"开发态体验完成品"**：构建/烧录/监视器/配置/IntelliSense/多 env 全部到位。  
跟 platform-espressif32 比，剩下的差距集中在**生态深度集成**（pio test、buildfs、OTA、lib_deps），其中 `lib_deps` 是**v1.0 主动延后**的取舍（不是缺陷，详见 §3.4 — RTOS 生态不在 PIO Lib Registry，且 ESP-IDF 名义支持但实际用户也不在那个 registry），其余在 v0.3+ 路线图。

实际开发者从 v0.1 升 v0.2 体验差距：
- 写代码：从"红波浪线一片"到"VSCode 智能提示完整可用"
- 烧录后看输出：从"开第二个终端 + 手敲 ameba.py monitor"到"`pio run -t monitor_ambsdk`"
- 改 SDK 配置：从"只能命令行 ameba.py menuconfig"到"`pio run -t menuconfig`"
- 多 SoC 工程：从"一会儿改对一会儿改错"到"完全可靠"

---

## 6. 参考阅读

- [`platform-espressif32/builder/frameworks/espidf.py`](https://github.com/platformio/platform-espressif32/blob/develop/builder/frameworks/espidf.py)（2,373 行）——白盒接入参考
- [`LibreTiny`](https://github.com/libretiny-eu/libretiny)——RTL8710BN 黑盒接入参考（自重写 cmake，跟我们路线相反）
- [PIO Platform Specification](https://docs.platformio.org/en/latest/platforms/creating_platform.html)
- [PIO CompileDB / IntelliSense Guide](https://docs.platformio.org/en/latest/integration/compile_commands.html)

---

*最后更新：2026-05-27 23:50 UTC+8 (v0.2.0-dev)*
