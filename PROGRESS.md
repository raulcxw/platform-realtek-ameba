# platform-amebartos 进度报告

> **当前版本**：v0.1.0-dev
> **首次端到端跑通**：2026-05-27 21:54 UTC+8
> **维护者**：raul_chen @ Realtek

---

## 🎯 任务定义

为 **Realtek Ameba RTOS SDK**（amebagreen2 / amebasmart / amebaz2 等系列）做 PlatformIO 平台接入，目标是社区可用的 `platform-amebartos` 仓库，最终能进 PIO Registry。

第一阶段重点验证：**RTL8721F (AmebaGreen2 双核 KM4TZ + KM4NS)** 在 PIO 下能从 `pio run` 跑到固件产物。

---

## ✅ 已完成

### Step 0 · 调研（2026-05-26）
- PlatformIO 项目深度介绍（17K）
- Ameba 接入 PIO 完整调研报告（20K）
- 关键发现：
  - **8devices `platform-realtek-ameba`** 是考古样本（bintray 死、SDK 链断、PIO v3 过时）
  - **LibreTiny**（524★，2026-04 在维护）支持 RTL8710BN/AmebaZ2，但不跟踪 Realtek 上游 SDK，无路径接 RTL8721F
  - **ivankravets**（PIO 创始人）态度：门开着不主动，走 PR 进 Registry 路线可行

### Step 1 · SDK build smoke test（2026-05-27 21:26 → 21:44）
- 验证 `ameba-rtos master @ 69b9a86` 能在本地 WSL 完整编通 RTL8721F
- 18 分钟（含 372MB toolchain 下载 + 解压 + 双核编译）
- 产物：`build_RTL8721F/{app.bin, boot.bin, ota_all.bin}`

### Step 2 · platform-amebartos/ 骨架（5 个文件，~250 LoC）

```
platform-amebartos/
├── platform.json              # PIO 平台清单
├── platform.py                # AmebartosPlatform(PlatformBase)
├── builder/
│   ├── main.py                # SCons 入口，调 ameba.py build/flash
│   └── frameworks/ambsdk.py   # framework 发现 stub
├── boards/
│   └── rtl8721f.json          # AmebaGreen2 板子定义
├── examples/ameba-blink/      # 验证用最小工程
├── README.md
├── PROGRESS.md                # 本文件
└── .gitignore
```

### Step 3 · `pio run` 端到端跑通（2026-05-27 21:54）

```
======================== [SUCCESS] Took 276.73 seconds ========================
.pio/build/rtl8721f/firmware.bin   ← PIO 标准位置
```

完整证据链：

```
Processing rtl8721f (platform: amebartos; framework: ambsdk; board: rtl8721f)
[ambsdk] $ python ameba.py soc RTL8721F        ← PIO 调 SDK
[ambsdk] $ python ameba.py build               ← PIO 调 SDK
asdk-12.3.1-linux-newlib-build-4600-x86_64.tar.bz2 100% [371576545/371576545]  ← 自动下 372MB
Toolchain Version Matched
Python3 found: .../ameba-rtos/.venv/bin/python3.11
[INFOR] soc project : amebagreen2, soc type: amebagreen2|AmebaGreen2
[710/710] generate build_info.h                ← 710 个目标全过
========== Image app generate end ==========
[ambsdk] copied build_RTL8721F/app.bin → .pio/build/rtl8721f/firmware.bin
[SUCCESS]
```

### Step 4 · `pio run -t upload` 烧录链路跑通（2026-05-27 22:25）

```
======================== [SUCCESS] Took 290.55 seconds ========================
```

**真实硬件验证**：RTL8721F EVB，DID 0x7005，16MB NOR，WiFi MAC `00:E0:4C:00:14:1A`，远程串口服务器 127.0.0.1:58916，COM40。

```
[ambsdk] uploading SoC=RTL8721F, opts={'port': 'COM40', 'remote-server': '127.0.0.1', 'remote-password': '87654321'}
[ambsdk] $ python3 ameba.py flash --port COM40 --remote-server 127.0.0.1 --remote-password 87654321
[COM40]boot.bin download done: 29KB / 479.0ms / 495.0Kbps
[COM40]app.bin download done:  565KB / 6375.0ms / 726.0Kbps
[COM40]Finished PASS
```

PIO upload_* 选项透传矩阵：

| `platformio.ini` 配置 | `ameba.py flash` 参数 | 备注 |
|---|---|---|
| `upload_port = COM40` | `-p COM40` | 必需 |
| `upload_speed = 1500000` | `-b 1500000` | 默认 1.5Mbps |
| `board_upload.remote_server = 127.0.0.1` | `--remote-server 127.0.0.1` | 远程串口（开发机不直连板子时用）|
| `board_upload.remote_password = 87654321` | `--remote-password 87654321` | 远程串口密码 |
| `board_upload.memory_type = nor` | `--memory-type nor` | nor / nand / ram |
| `board_upload.chip_erase = yes` | `--chip-erase` | 全片擦除 |

---

## 🏗️ 关键设计决策

| 决策 | 选择 | 替代方案 | 理由 |
|---|---|---|---|
| **CMake 链路** | 不重写，shell 调 `ameba.py build` | 仿 espidf.py 重做 cmake 包装 | espidf.py 是 ~2300 LoC 的怪物；LibreTiny 重做 cmake 几个 SoC 后维护爆炸；Ameba 上游 cmake/ninja 已经版本锁死，重做没价值 |
| **Toolchain 管理** | SDK 自管，PIO 不声明 toolchain 包 | 把 asdk-12.3.1 / asdk-10.3.1 注册到 PIO Registry | Realtek 没在 PIO Registry 发布；许可证不允许我们镜像；`ameba.py` 已经按 SoC 锁定版本 |
| **SDK 包分发** | v0.1: 不声明 framework 包，靠 `AMEBA_SDK_DIR` 环境变量或默认路径找 | 做成 `framework-ambsdk` git 包从 Registry 拉 | v0.1 跑通验证更重要；Step 6 再上 |
| **端到端入口** | `subprocess.call([venv_python, "ameba.py", "build"])` | SCons Builder + Action graph | SCons 跟 ameba.py 内部的 cmake 树两套依赖系统并存会冲突；shell 调最干净 |
| **Glue 代码量** | ~250 LoC | espidf.py 的 ~2300 LoC | 9x 精简，全靠"不重写上游 cmake" |

---

## 🐞 这次踩的 4 个坑（已在 skill 沉淀）

### 坑 1: PIO 不认 symlink 当作 framework 包

**现象**：`pio run` 看到 `~/.platformio/packages/framework-ambsdk` 是 symlink 不是目录里的 `package.json`，判定"未安装"，去 git clone 那个不存在的 `main` 分支。

**修法**：① 给 SDK 仓库根加 `package.json`，PIO 才认为它"已安装"；② 或者干脆 platform.json 不声明这个包，用环境变量找。

我们用了②，因为 SDK 不应该污染 ameba-rtos 上游。

### 坑 2: `pio pkg uninstall` 会清掉 packages 下我们手建的 symlink

每次重装 platform 都得重建 symlink。最终方案是 platform.json 里完全不声明这两个包。

### 坑 3: PIO 把源码 cp 到 `~/.platformio/platforms/<name>/` 跑

改 `builder/main.py` 后必须重装 platform，不然跑的还是上一版。**这个最隐蔽**——我修了 `_ameba_python()` 看起来没生效，diff 才发现 PIO 缓存在用旧版本。

### 坑 4: Ameba CMake 硬编码 `python` 不是 `python3`

`ameba-rtos/cmake/common.cmake:132` 写：
```cmake
COMMAND python ${c_BASEDIR}/tools/scripts/menuconfig.py
```

这意味着 PATH 第一个 `python` 必须能 `import json5`。修法：`builder/main.py` 在子进程 env 里把 SDK venv 的 `bin/` 放在 PATH 最前。

---

## ⏳ 待完成

| Step | 内容 | 需要硬件？ | 预计 |
|---|---|---|---|
| **5** | 多 SoC 板子已加（RTL8730E / RTL8721Dx / RTL8720E）✅ | ❌ | done |
| **6** | framework-ambsdk 自分发（解决本地 symlink hack）| ❌ | 1-2h |
| **7** | PR 进 PIO Registry | ❌ | 看反馈 |

---

## 📦 工件位置

| 类别 | 路径 |
|---|---|
| 源码（git 仓库）| `~/projects/ameba-platformio-research/platform-amebartos/` |
| Ameba SDK 镜像 | `~/projects/ameba-platformio-research/repos/ameba-rtos/` |
| Toolchain（asdk-12.3.1）| `~/.platformio/platforms/amebartos/.cache/rtk-toolchain/asdk-12.3.1-4600/` |
| Prebuilts（cmake/ninja）| `~/rtk-toolchain/prebuilts-linux-1.0.3/` |
| PIO 用户目录 | `~/.platformio/` |
| 固件产物 | `examples/ameba-blink/.pio/build/rtl8721f/firmware.bin` |

---

## 🔗 与 Ameba 团队工作的相关性

这次工作产出三件 IoT 团队可能关心的事：

1. **Ameba SDK 已经能被 PIO 自动消费**——意味着任何用 PlatformIO（VSCode + PIO 插件、CLion + PIO 插件）的开发者，都能 `pio run` 编 RTL8721F，不需要他们装 ameba-rtos 仓库 + 跑 env.sh。
2. **维护成本极低**：上游 ameba-rtos 怎么改 cmake 都不影响 platform-amebartos，因为我们只调 `ameba.py` 公共 CLI。Realtek 团队继续维护 ameba.py 接口稳定即可。
3. **路径打开**：将来要做"小智 AI on AmebaGreen2"、"Ameba 跑 Tailscale"、"Matter on Ameba" 这类社区 demo，开发者第一行就是 `pio run`，跟 ESP32 用户体验一致。

---

*最后更新：2026-05-27 22:05 UTC+8*
