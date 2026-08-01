# Skia Prebuilts

用于生成 Skia 预编译库的 CMake Package 脚本。

## 功能

- 自动检测 Skia 构建产物（Windows: `.dll/.dll.lib`，Linux: `.so`，macOS: `.dylib`）
- 如果构建产物不存在，或构建目录中的 `args.gn` 与当前模板不一致，自动执行 GN + Ninja 构建
- 生成标准 CMake config package，方便其他项目通过 `find_package(Skia CONFIG REQUIRED)` 集成 Skia
- 默认生成 GPU-capable Skia：Windows 使用 Ganesh + Direct3D，Linux 使用 Ganesh + Vulkan + EGL/GL，macOS 使用 Ganesh + Metal

## 使用方法

```bash
python generate_skia_package.py <skia_repo_dir> <build_dir> <output_folder_name>
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `skia_repo_dir` | Skia 仓库目录（包含 `include` 文件夹） |
| `build_dir` | 构建目录（GN/Ninja 的输出目录） |
| `output_folder_name` | 输出文件夹名称 |
| `--target-cpu` | macOS 可选参数，支持 `x64`/`arm64`，默认使用当前机器架构 |
| `--force-build` | 即使构建目录已有 Skia 二进制文件，也强制重新执行 GN + Ninja |

### 示例

**Windows:**
```bash
python generate_skia_package.py C:\skia C:\skia\out\Release skia-m148-46f2e16555-win32-x64
```

**Linux:**
```bash
python generate_skia_package.py /path/to/skia /path/to/skia/out/Release skia-m148-46f2e16555-linux-x64
```

**macOS:**
```bash
python generate_skia_package.py /path/to/skia /path/to/skia/out/Release skia-m148-46f2e16555-macos-arm64 --target-cpu arm64
python generate_skia_package.py /path/to/skia /path/to/skia/out/Release skia-m148-46f2e16555-macos-x64 --target-cpu x64
```

## 输出

脚本会生成一个压缩包（Windows: `.zip`，Linux/macOS: `.tar.gz`），包含：

```
<output_folder_name>/
├── lib/                # 库文件
│   ├── libskia.so      # Linux
│   ├── libskia.dylib   # macOS
│   ├── skia.dll.lib    # Windows
│   └── cmake/Skia/
│       ├── SkiaConfig.cmake
│       ├── SkiaConfigVersion.cmake
│       └── SkiaTargets.cmake
├── bin/                # Windows 专用
│   └── skia.dll
└── include/
    ├── core/           # Skia public headers
    ├── gpu/
    └── ...
```

## 自动构建流程

当构建目录中找不到 Skia 二进制文件时，脚本会自动执行以下步骤：

1. 创建构建目录（如果不存在）
2. 当二进制不存在、`args.gn` 与模板不一致，或指定了 `--force-build` 时，复制 `args.gn` 配置文件到构建目录
3. 执行 `gn gen` 生成构建文件
4. 执行 `ninja` 编译 Skia

## 配置文件

- `args.gn.linux` - Linux 构建配置
- `args.gn.macos` - macOS 构建配置
- `args.gn.windows` - Windows 构建配置
- `cmake/` - CMake config package 模板文件

## CMake 集成方式

预编译包解压后，应通过 config package 消费：

```cmake
find_package(Skia CONFIG REQUIRED PATHS "/path/to/skia-prebuilt" NO_DEFAULT_PATH)
target_link_libraries(my_app PRIVATE Skia::Skia)
```

如果使用 `FetchContent` 下载预编译包，`FetchContent` 只负责下载和解压，随后仍然调用 `find_package`：

```cmake
include(FetchContent)

FetchContent_Declare(
    skia_prebuilt
    URL "https://example.com/skia-m148-46f2e16555-linux-x64.tar.gz"
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    SOURCE_SUBDIR "__skia_prebuilt_no_add_subdirectory"
)
FetchContent_MakeAvailable(skia_prebuilt)

find_package(Skia CONFIG REQUIRED
    PATHS "${skia_prebuilt_SOURCE_DIR}"
    NO_DEFAULT_PATH
)
target_link_libraries(my_app PRIVATE Skia::Skia)
```

### GPU 后端

- Windows: `skia_enable_ganesh = true`, `skia_use_direct3d = true`, `skia_use_gl = false`
- Linux: `skia_enable_ganesh = true`, `skia_use_vulkan = true`, `skia_use_gl = true`, `skia_use_egl = true`, `skia_use_x11 = false`
- macOS: `skia_enable_ganesh = true`, `skia_use_metal = true`, `skia_use_gl = false`
