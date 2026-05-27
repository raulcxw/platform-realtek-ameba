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

| 功能 | espressif32 | amebartos v0.1 | 备注 |
|---|---|---|---|
| `pio run`（编译） | 🟢 | 🟢 | RTL8721F 真编通，`firmware.bin` 在 `.pio/build/<env>/` |
| `pio run -t upload`（烧录） | 🟢 | 🟢 | 含 `--remote-server`/`--remote-password`/`--port`/`--baudrate`/`--memory-type`/`--chip-erase` 透传 |
| `pio run -t clean` | 🟢 | 🟡 | 调 `ameba.py clean SOC`，但 PIO 自己的 `.pio/` 缓存不联动 |
| 增量编译 | 🟢 智能 | 🟢 但靠上游 | SDK 内部用 ninja，PIO 没有可见性，全量/增量决策权在 ameba.py 手里 |

### 2.2 开发体验功能

| 功能 | espressif32 | amebartos v0.1 | 影响面 |
|---|---|---|---|
| **VSCode IntelliSense**（写代码补全/跳转） | 🟢 强 | 🔴 **无** | ⚠️ **最大真实差距**（详见 §3.1）|
| `pio device monitor`（串口监视器） | 🟢 | 🔴 暂不支持 | ⚠️ 高频痛点 — `ameba.py monitor` 已存在，只需 ~50 行 builder 接入 |
| `pio run -t menuconfig`（图形化 SDK 配置） | 🟢 | 🔴 暂不支持 | `ameba.py menuconfig` 已存在，~30 行接入 |
| `pio check`（cppcheck/clang-tidy） | 🟢 | 🔴 | 跟 IntelliSense 同根因（缺 `compile_commands.json`） |
| `pio debug`（GDB） | 🟢 | 🟡 占位 | platform.py 里写了 OpenOCD/JLink stub，未真验证 |

### 2.3 PIO 生态深度集成

| 功能 | espressif32 | amebartos v0.1 | 是否计划做 |
|---|---|---|---|
| `pio lib install` PIO 库管理 | 🟢 | 🔴 **不支持** | ❌ **永远不做** — 哲学冲突，详见 §3.4 |
| `lib_deps` 自动拉外部库 | 🟢 | 🔴 | ❌ 同上 |
| `pio test`（unity 单元测试） | 🟢 | 🔴 | 🟡 v1.0+ 看需求 |
| 文件系统镜像 `pio run -t buildfs`（SPIFFS/LittleFS） | 🟢 | 🔴 | 🟡 v1.0+，Ameba 有 LittleFS 但没接 |
| OTA 烧录 | 🟢 | 🔴 | 🟡 v0.3 计划 |
| 多 env 并行（`pio run -e a -e b`） | 🟢 | 🟡 **危险** | ⚠️ 同 SDK 根目录共用 `soc_info.json` 会互相覆盖 — v0.2 修 |

---

## 3. 重点差距详解

### 3.1 IntelliSense / `compile_commands.json` —— 最大真实差距

**用户感受**：

```c
// 同一段代码，两边的开发体验对比

// platform-espressif32 ✅
#include "wifi_provisioning/manager.h"   // 自动补全可用
wifi_prov_mgr_config_t config = {         // 类型悬浮、跳转定义
    .scheme = wifi_prov_scheme_softap,    // 字段补全
};
wifi_prov_mgr_init(&config);              // 函数签名提示

// platform-amebartos 🔴
#include "wifi_api.h"      // 红波浪线："找不到此文件"（实际编译没问题）
int x = wifi_init();        // 没有补全、没有签名提示
```

**根因**：

espidf.py 里的这些函数（`grep "^def " espidf.py | head -10`）：

```python
def get_app_includes(app_config):     # 解析 cmake 给每个 .c 的 -I 列表
def get_app_defines(app_config):      # 解析 -D 宏定义
def get_app_flags(...):                # 解析 -f 编译选项
def extract_link_args(target_config): # 解析链接参数
def load_target_configurations(...):   # 通过 CMake File API 读组件图
```

它们把信息 dump 给 PIO，PIO 写到 `.pio/build/<env>/compile_commands.json`，VSCode/CLion 读这个文件就能正确高亮、跳转、补全。

**我们的 `builder/main.py` 完全没干这事**：

```python
def build_firmware(*_args, **_kwargs):
    subprocess.call([py, "ameba.py", "build"], ...)   # 黑盒
    shutil.copyfile(SDK/build_RTL8721F/app.bin, .pio/firmware.bin)
    # ↑ PIO 完全不知道这次编译用了哪些 -I -D -f
```

**修复路径**（已规划，~50 行代码）：

cmake 默认就会生成 `compile_commands.json`（在 `build_<SOC>/build/compile_commands.json`），我们只需要：

```python
def export_compile_commands():
    """ameba.py build 后把 cmake 自动生成的 compile_commands.json 拷到 .pio/"""
    src = join(SDK_DIR, f"build_{SOC}", "build", "compile_commands.json")
    dst = join(PROJECT_BUILD_DIR, "compile_commands.json")
    if os.path.isfile(src):
        shutil.copyfile(src, dst)
```

外加一些路径修正（cmake 用绝对路径，PIO 期望相对项目）。**计划 v0.2 补上**。

### 3.2 `pio device monitor` —— 高频痛点

每次烧完固件都要看串口输出，没有这个用户得手动开第二个终端跑 `python ameba.py monitor`。

`ameba.py monitor` 接受跟 flash 相同的 `--remote-server`/`--port` 参数，只需在 `builder/main.py` 注册一个 SCons target：

```python
def serial_monitor(*_args, **_kwargs):
    cmd = [_ameba_python(), join(SDK_DIR, "ameba.py"), "monitor"]
    if upload_port:
        cmd.extend(["-p", upload_port])
    if remote_server:
        cmd.extend(["--remote-server", remote_server])
    subprocess.call(cmd, env=_make_sdk_env())

env.AddCustomTarget("monitor", None, serial_monitor, title="Monitor", description="...")
```

**计划 v0.2 补上**（~50 行）。

### 3.3 多 env 并行 —— 数据竞争风险

`pio run -e rtl8721f -e rtl8730e` 会同时跑两份编译。我们当前的 `builder/main.py` 让两个 env **共用同一份 SDK checkout**，两个进程都会写 `SDK/soc_info.json` 互相覆盖，编译产物也会写到同一个 `build_<SOC>/` 目录（虽然按 SOC 区分了，但中间状态可能踩到）。

**修复思路**：把每个 env 的 SDK build 隔离到 PIO 的 `BUILD_DIR/sdk-checkout/`（用 hard link 或 worktree）。**计划 v0.2 补上**。

### 3.4 `pio lib install` —— 永远不做

PIO 库管理器（lib_deps、lib_extra_dirs）是为 Arduino 风格的"独立小库"设计的：每个库是一个独立 git 仓库，PIO 帮你下载到 `.pio/libdeps/`，编译时自动加 `-I` 和源文件。

Ameba SDK 的组件系统是**完全不同的范式**：组件在 `component/` 目录里，`Kconfig` 配置开关，cmake 在 SDK 内部编进 `lib_*.a` 静态库。两者**强行调和会混乱**。

**Espressif 的解法**：espidf.py 解析 ESP-IDF 组件树，**模拟**成 PIO 库管理器看得懂的格式。这部分代码占了 espidf.py 一半篇幅，且每次 ESP-IDF 更新就要修一次。

**我们的解法**：直接用 SDK 原生组件系统。用户在 `platformio.ini` 里通过 `board_build.ambsdk.menuconfig = ...` 走 Ameba 自己的 Kconfig 流程开关组件，**不假装支持 `lib_deps`**。

哲学：**让 PIO 干 PIO 擅长的事（构建编排+烧录+IDE 集成），让 SDK 干 SDK 擅长的事（组件管理+硬件抽象）**。

---

## 4. 路线图（修复优先级）

| 优先级 | 功能 | 难度 | 代码量 | 目标版本 |
|---|---|---|---|---|
| 🔥 高 | `compile_commands.json` 导出（IntelliSense） | 🟡 中 | ~150 行 | **v0.2** |
| 🔥 高 | `pio device monitor` 接入 | 🟢 易 | ~50 行 | **v0.2** |
| 中 | `pio run -t menuconfig` 接入 | 🟢 易 | ~30 行 | v0.2 |
| 中 | `pio run -t clean` 完整化（清 .pio/） | 🟢 易 | ~20 行 | v0.2 |
| 中 | 多 env 并行隔离 BUILD_DIR | 🟡 中 | ~60 行 | v0.2 |
| 中 | OTA 烧录支持 | 🟡 中 | ~80 行 | v0.3 |
| 低 | `pio debug` 真验证（OpenOCD config） | 🟡 中 | 验证为主 | v0.3 |
| 低 | `pio test`（unity） | 🔴 难 | ~300 行 | v1.0 |
| ❌ 不做 | `pio lib install` / `lib_deps` | 🔴 极难 | ~800 行 | 永不 |

---

## 5. 给上游用户的诚实陈述

### 适合什么场景

- ✅ 已有 ameba-rtos SDK 工作流，想用 PIO 做**统一的构建/烧录入口**（CLion + PIO 插件、VSCode + PIO 插件、CI/CD `pio run`）
- ✅ 团队多人/多机，想要"clone 工程 → `pio run` → 编出固件"的一致体验
- ✅ 同时维护多个 Ameba SoC 项目（多 env 配置在一份 platformio.ini）
- ✅ Realtek 内部 demo 一致化（统一 `[env:rtl8721f]` `[env:rtl8730e]` 命名）

### 不适合什么场景

- ❌ Arduino 风格"装个库就用"开发者（请用 LibreTiny，已支持 RTL8710BN/AmebaZ2）
- ❌ 需要 PIO 完整 IDE 体验（v0.2 之前 IntelliSense 不可用 — 暂时建议在 VSCode 里**直接打开 ameba-rtos 仓库**而不是 PIO 工程目录，cmake 自带的 `compile_commands.json` 还是有效的）
- ❌ 需要 `pio test` 单元测试体系
- ❌ 需要 Espressif 那种"PIO 库管理器统一管所有依赖"

### v0.1 的真实定位

**v0.1 是"build/upload 工作流跑通"的最小可用版本**，不是 PIO 集成的完成品。  
跟 platform-espressif32 比，差距集中在**开发态体验**（IntelliSense、monitor、menuconfig），**而不是构建/烧录正确性**。  
v0.2 的 ~300 行增量代码会把上述高优先级缺失全部补上，届时差距将主要剩"PIO 库管理"——而这是**有意不做**的设计选择。

---

## 6. 参考阅读

- [`platform-espressif32/builder/frameworks/espidf.py`](https://github.com/platformio/platform-espressif32/blob/develop/builder/frameworks/espidf.py)（2,373 行）——白盒接入参考
- [`LibreTiny`](https://github.com/libretiny-eu/libretiny)——RTL8710BN 黑盒接入参考（自重写 cmake，跟我们路线相反）
- [PIO Platform Specification](https://docs.platformio.org/en/latest/platforms/creating_platform.html)
- [PIO CompileDB / IntelliSense Guide](https://docs.platformio.org/en/latest/integration/compile_commands.html)

---

*最后更新：2026-05-27 23:25 UTC+8*
