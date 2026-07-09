# Skia Prebuilts

用于生成 Skia 预编译库的 CMake Package 脚本。

## 功能

- 自动检测 Skia 构建产物（Windows: `.dll/.dll.lib`，Linux: `.so`，macOS: `.dylib`）
- 如果构建产物不存在，自动执行 GN + Ninja 构建
- 生成跨平台的 CMake Package，方便其他项目集成 Skia

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

### 示例

**Windows:**
```bash
python generate_skia_package.py C:\skia C:\skia\out\Release skia-win-x64
```

**Linux:**
```bash
python generate_skia_package.py /path/to/skia /path/to/skia/out/Release skia-linux-x64
```

**macOS:**
```bash
python generate_skia_package.py /path/to/skia /path/to/skia/out/Release skia-macos-arm64 --target-cpu arm64
python generate_skia_package.py /path/to/skia /path/to/skia/out/Release skia-macos-x64 --target-cpu x64
```

## 输出

脚本会生成一个压缩包（Windows: `.zip`，Linux/macOS: `.tar.gz`），包含：

```
<output_folder_name>/
├── CMakeLists.txt      # CMake 配置文件
├── lib/                # 库文件
│   └── libskia.so      # Linux
│   └── libskia.dylib   # macOS
│   └── skia.dll.lib    # Windows
├── bin/                # Windows 专用
│   └── skia.dll
└── include/
    └── skia/           # Skia 头文件
```

## 自动构建流程

当构建目录中找不到 Skia 二进制文件时，脚本会自动执行以下步骤：

1. 创建构建目录（如果不存在）
2. 复制 `args.gn` 配置文件到构建目录
3. 执行 `gn gen` 生成构建文件
4. 执行 `ninja` 编译 Skia

## 配置文件

- `args.gn.linux` - Linux 构建配置
- `args.gn.macos` - macOS 构建配置
- `args.gn.windows` - Windows 构建配置
- `cmake/` - CMake Package 配置文件
