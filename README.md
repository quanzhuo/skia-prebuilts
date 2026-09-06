# Skia Prebuilts

在同一个 `main` 分支上维护 full 和 cpu-only 两套配置。从同一个 Skia
checkout 顺序构建两个独立的 CMake 包，不再需要切换分支或手工拼接包名。

## 快速开始

先准备 Python 3.9+、编译器、GN、Ninja 和已经同步依赖的 Skia 源码，
再在本仓库运行（Windows 使用 x64 Visual Studio 开发者终端）：

```powershell
python .\generate_skia_package.py ..\skia m152-0873ec164a
```

两个位置参数分别是 **Skia 源码目录**和**版本号**。版本号格式为
`m<里程碑>-<7~40 位提交哈希>`，必须与源码的 `HEAD` 和
`include/core/SkMilestone.h` 一致。脚本不会切换提交、同步依赖或自动发布。

以 Windows x64 为例，默认输出：

```text
dist/
├── skia-m152-0873ec164a-win32-x64.zip
└── skia-m152-0873ec164a-cpu-only-win32-x64.zip
```

每个压缩包包含一个与压缩包同名的顶层目录，解压后即可作为 CMake 包使用。
Linux/macOS 输出 `.tar.gz`，平台后缀分别为 `linux-x64`、
`linux-arm64`、`macos-x64`、`macos-arm64`。full 包不额外添加
`-full` 后缀；CPU 包沿用 `-cpu-only` 命名方式。

### 可选参数

| 参数 | 说明 |
|------|------|
| `--profile both\|full\|cpu-only` | 默认 `both`，顺序生成两个包；可只构建一种 |
| `--build-root PATH` | 默认 `<Skia源码>/out/prebuilts`；脚本在其下按平台、profile 隔离 |
| `--output-dir PATH` | 默认当前工作目录下的 `dist` |
| `--target-cpu x64\|arm64` | 默认本机架构；macOS 支持跨架构，Windows/Linux 拒绝非本机架构 |
| `--force-build` | 强制执行 GN + Ninja，仍为增量构建，不清空旧目录 |
| `-j N` / `--jobs N` | 正整数，默认自动检测进程可用的逻辑 CPU 数 |

`x86_64`/`amd64` 和 `aarch64` 也可作为架构别名。
macOS 部署版本遵循当前 Skia 工具链；不再传递已不生效的
`min_macos_version` 参数。

例如：

```powershell
# 只生成 CPU-only 包，并限制并发
python .\generate_skia_package.py ..\skia m152-0873ec164a --profile cpu-only --jobs 8

# 自定义构建根目录和输出目录，目录名字仍由脚本自动派生
python .\generate_skia_package.py ..\skia m152-0873ec164a --build-root D:/skia-builds --output-dir D:/packages
```

```bash
# Linux 本机架构
python generate_skia_package.py ../skia m152-0873ec164a

# macOS 分别构建两个架构，每次生成 full 和 CPU-only 各一个包
python generate_skia_package.py ../skia m152-0873ec164a --target-cpu arm64
python generate_skia_package.py ../skia m152-0873ec164a --target-cpu x64
```

旧的三个位置参数 `<源码目录> <构建目录> <完整包名>` 已移除。
原来的构建目录和压缩包不会被迁移或删除；新目录首次使用需要重新构建。

自动并发优先使用 Python 3.13+ 的 `os.process_cpu_count()`；旧版 Python
优先查询 CPU 亲和性，再回退到 `os.cpu_count()`，无法检测时使用 1。
不按空闲 CPU 百分比或内存动态调节；内存紧张时请设置 `--jobs`。
两个 profile 不同时编译，因此不会把并发数翻倍。

## Profile 边界

| 能力 | full | cpu-only |
|------|------|----------|
| CPU raster、PathOps | 包含 | 包含 |
| PNG 编解码、JPEG 解码 | 包含 | 包含 |
| Windows GPU | Ganesh + ANGLE/EGL/OpenGLES | 不包含 |
| Linux GPU | Ganesh + Vulkan + EGL/GL，不依赖 X11/GLX | 不包含 |
| macOS GPU | Ganesh + Metal | 不包含 |
| Windows ANGLE DLL、导入库、头文件和 CMake targets | 包含 | 不包含 |

这里的 full 指本项目原有的 **CPU + GPU 渲染配置**，不是启用 Skia
所有可选模块：PDF、Skottie、SVG、JPEG 编码、WebP 等仍保持原配置的取舍。
字体栈保持平台策略：Windows DirectWrite、Linux Fontconfig/Freetype、
macOS CoreText。CPU-only 不向消费者传播 `SK_GANESH` 等 GPU 宏。

配置文件全部内置：

```text
configs/
├── full/
│   ├── args.gn.windows
│   ├── args.gn.linux
│   └── args.gn.macos
└── cpu-only/
    ├── args.gn.windows
    ├── args.gn.linux
    └── args.gn.macos
```

CPU-only 配置从原 `cpu-only` 分支迁入。公共打包代码、补丁和 CMake
模板只有一份，不再各维护一个脚本。配置仍需与选定 Skia 提交相匹配；
GN 使用 `--fail-on-unused-args`，不接受静默失效的参数。

## 构建隔离与失败处理

默认构建目录示例：

```text
<Skia源码>/out/prebuilts/
└── win32-x64/
    ├── full/
    └── cpu-only/
```

每个目录有独立的 `args.gn`、对象文件、动态库和成功记录
`.skia-package-build-inputs.sha256`。成功记录包含源码提交、平台、
profile、配置模板和有序补丁内容；生成后的架构参数另与 `args.gn` 比较。
产物缺失、配置变化、提交/补丁变化、无有效成功记录时自动构建。
单独修改并发数不触发重建。

未提交的 Skia 源码修改、编译器/SDK/依赖变化、手动替换二进制不在指纹
覆盖范围内，需使用 `--force-build`。Ninja 仍按自己的依赖关系决定重编译。

脚本在 Skia 的 Git 元数据目录持有操作系统文件锁，拒绝另一个脚本
同时使用同一 checkout；进程退出时锁自动释放，不需要手工删除锁文件。
此锁只协调本脚本，构建期间不要在其他工具中修改源码或切换提交。

构建前使旧成功记录失效，临时应用所需补丁，构建完成后恢复本次应用的
补丁。只有构建与恢复均成功才写入成功记录。调用前已应用的补丁保持不动。
某个补丁恢复失败时仍尝试恢复其余补丁，并报告未恢复的补丁路径。
正常异常会触发恢复；强制终止进程无法保证 Python 清理代码执行，
下次运行前应检查 Skia 工作区。

每个压缩包先在输出目录的临时子目录中完成，再原子替换同名压缩包。
失败不会截断旧压缩包，也不删除无关文件。两个包不是跨文件事务：
full 成功、CPU-only 失败时，保留已生成的 full 包，整次命令返回非零。
修复后重跑会复用已经成功的构建记录。

## PathOps

两种 profile 在所有平台都应用
`patches/0002-include-pathops-in-skia.patch`，将 `:pathops` 加入
`skia_component("skia")` 的 `public_deps`。这不改变算法、不依赖 GPU，
也不启用 PDF/XPS。当前 m152 没有 `skia_enable_pathops` 参数；
单独构建 `pathops` target 不会把它链接进 Skia 动态库。

包内复制完整 `include/`，包含 `include/pathops/SkPathOps.h` 及其
公共/私有头文件依赖，并补齐 `modules/skcms/` 的必要头文件。
消费者只需链接 `Skia::Skia`，没有独立 PathOps 库。
Windows full 额外应用 `0001-expose-angle-runtime-target.patch` 并构建
`skia_angle_runtime`；CPU-only 不应用或构建 ANGLE 的这一补丁/目标。

## CMake 集成

```cmake
find_package(Skia CONFIG REQUIRED
    PATHS "/path/to/extracted/skia-package"
    NO_DEFAULT_PATH)
target_link_libraries(my_app PRIVATE Skia::Skia)
```

`Skia::Skia` 传播必要的公共编译定义和 C++20 要求。
Windows full 的 GPU 消费者额外链接 `Skia::Angle`，该目标引用
`Skia::AngleEGL` 和 `Skia::AngleGLESv2`。
full 和 CPU-only 是二选一的替代包，不能在同一 CMake 工程中同时导入。
同一包可以重复导入；已有可见的 `Skia::Skia` 属于其他包时直接报错，
避免元数据与实际链接的库不一致。版本约束遵循 `SameMajorVersion`，
版本范围同时检查下界、上界以及是否包含上界。

包提供以下元数据：

- `Skia_VERSION`、`Skia_MILESTONE`、`Skia_COMMIT`
- `Skia_PREBUILT_PLATFORM`
- `Skia_BUILD_PROFILE`：`full` 或 `cpu-only`
- `Skia_GPU_BACKENDS`：CPU-only 为空
- `Skia_HAS_PATHOPS`：`TRUE`

`cmake/CMakeLists.example.cmake` 展示了通过 FetchContent 下载并选择 profile
（示例要求 CMake 3.24+）。
下载链接需指向实际发布的版本；本脚本不会上传包。

## 验证

脚本回归测试使用 Python 3.11+、Git、CMake 3.21+ 和 Ninja。
CMake 合同测试仅执行配置、不编译或链接模拟二进制，不依赖编译器：

```bash
python -m unittest discover -s tests -v
```

生成后对两个实际解压包分别运行原生 CMake 消费测试，验证 profile
元数据以及 `Op`、`Simplify`、`AsWinding`、`SkOpBuilder::resolve`
的编译、链接与执行。Windows 在 x64 Visual Studio 开发者终端运行：

```bash
cmake -S tests/pathops -B build/pathops-full -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/path/to/full-package -DSKIA_EXPECT_PROFILE=full
cmake --build build/pathops-full --parallel
ctest --test-dir build/pathops-full --output-on-failure

cmake -S tests/pathops -B build/pathops-cpu -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/path/to/cpu-package -DSKIA_EXPECT_PROFILE=cpu-only
cmake --build build/pathops-cpu --parallel
ctest --test-dir build/pathops-cpu --output-on-failure
```

压缩成功不等于已通过原生测试；各平台实际产物仍须在发布前验证。
