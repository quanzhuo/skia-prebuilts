#!/usr/bin/env python3
"""
Skia CMake Package 生成脚本

用于在已构建完成的 Skia 仓库中生成一个 CMake Package，
以便其他项目能够方便地集成和使用 Skia 库。

用法:
    python generate_skia_package.py <skia_repo_dir> <build_dir> <output_folder_name>

参数:
    skia_repo_dir: Skia 仓库目录（包含 include 文件夹）
    build_dir: 构建目录（包含编译产物如 .dll/.so/.dylib 文件）
    output_folder_name: 输出文件夹名称
"""

import os
import sys
import shutil
import platform
import tempfile
import hashlib
import argparse
import subprocess
import re
from typing import Optional


def validate_directory(path: str, name: str) -> None:
    """验证目录是否存在"""
    if not os.path.isdir(path):
        raise ValueError(f"{name} 目录不存在或无效: {path}")


def generate_random_suffix() -> str:
    """生成随机后缀用于临时目录"""
    return hashlib.md5(str(os.urandom(16)).encode()).hexdigest()[:8]


def copy_directory(src: str, dst: str) -> None:
    """复制目录及其内容"""
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def copy_file(src: str, dst: str) -> None:
    """复制文件"""
    dst_dir = os.path.dirname(dst)
    if dst_dir and not os.path.exists(dst_dir):
        os.makedirs(dst_dir)
    shutil.copy2(src, dst)


def get_script_dir() -> str:
    """获取脚本所在目录"""
    return os.path.dirname(os.path.abspath(__file__))


def get_cmake_template_path(template_name: str) -> str:
    """获取 CMake package 模板文件路径"""
    return os.path.join(get_script_dir(), "cmake", template_name)


def read_text_file(path: str) -> str:
    """读取 UTF-8 文本文件"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text_file(path: str, contents: str) -> None:
    """写入 UTF-8 文本文件"""
    dst_dir = os.path.dirname(path)
    if dst_dir and not os.path.exists(dst_dir):
        os.makedirs(dst_dir)

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(contents)


def render_template_file(template_name: str, replacements: dict) -> str:
    """用简单占位符渲染 CMake 模板"""
    template_path = get_cmake_template_path(template_name)
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"CMake package 模板不存在: {template_path}")

    contents = read_text_file(template_path)
    for key, value in replacements.items():
        contents = contents.replace(f"@{key}@", value)

    unresolved = re.findall(r"@[A-Z0-9_]+@", contents)
    if unresolved:
        raise RuntimeError(f"CMake package 模板仍有未替换占位符: {', '.join(sorted(set(unresolved)))}")

    return contents


def get_args_gn_file_path() -> str:
    """获取平台对应的 args.gn 文件路径"""
    script_dir = get_script_dir()
    system = platform.system().lower()

    if system == "windows":
        return os.path.join(script_dir, "args.gn.windows")
    elif system == "linux":
        return os.path.join(script_dir, "args.gn.linux")
    elif system == "darwin":
        return os.path.join(script_dir, "args.gn.macos")
    else:
        raise RuntimeError(f"不支持的平台: {system}")


def parse_package_version(output_folder_name: str) -> dict:
    """从输出包名中解析 Skia milestone 和提交号，生成 CMake 兼容的数字版本"""
    match = re.search(r"m(?P<milestone>\d+)-(?P<commit>[0-9a-fA-F]+)", output_folder_name)
    if not match:
        raise ValueError(
            "output_folder_name 必须包含 Skia 版本片段，例如 skia-m148-46f2e16555-win32-x64"
        )

    milestone_number = match.group("milestone")
    return {
        "version": f"{milestone_number}.0.0",
        "version_major": milestone_number,
        "milestone": f"m{milestone_number}",
        "commit": match.group("commit"),
    }


def normalize_cpu_name(raw_cpu: str) -> str:
    """规范化 CPU 架构名称"""
    cpu = raw_cpu.lower()
    if cpu in ("x86_64", "amd64", "x64"):
        return "x64"
    if cpu in ("aarch64", "arm64"):
        return "arm64"
    raise RuntimeError(f"不支持的 CPU 架构: {raw_cpu}")


def detect_package_platform(system: str, target_cpu: Optional[str]) -> str:
    """生成预编译包平台标识"""
    if system == "windows":
        return f"win32-{normalize_cpu_name(platform.machine())}"
    if system == "linux":
        return f"linux-{normalize_cpu_name(platform.machine())}"
    if system == "darwin":
        return f"macos-{normalize_macos_target_cpu(target_cpu)}"
    raise RuntimeError(f"不支持的平台: {system}")


def get_cmake_platform_condition(system: str) -> str:
    """获取 CMake config package 的平台保护条件"""
    if system == "windows":
        return "WIN32"
    if system == "linux":
        return "UNIX AND NOT APPLE"
    if system == "darwin":
        return "APPLE"
    raise RuntimeError(f"不支持的平台: {system}")


def get_gpu_backends(system: str) -> str:
    """获取包内启用的 GPU 后端描述"""
    if system == "windows":
        return "D3D12"
    if system == "linux":
        return "Vulkan;EGL;GL"
    if system == "darwin":
        return "Metal"
    raise RuntimeError(f"不支持的平台: {system}")


def get_interface_compile_definitions(system: str) -> str:
    """获取 Skia imported target 的 public compile definitions"""
    if system == "windows":
        return "SKIA_DLL;SK_GANESH;SK_DIRECT3D"
    if system == "linux":
        return "SKIA_DLL;SK_GANESH;SK_VULKAN;SK_GL;SK_USE_INTERNAL_VULKAN_HEADERS;SK_R32_SHIFT=16"
    if system == "darwin":
        return "SKIA_DLL;SK_GANESH;SK_METAL"
    raise RuntimeError(f"不支持的平台: {system}")


def get_imported_location_checks(system: str) -> str:
    """获取 SkiaTargets.cmake 用来校验包完整性的文件列表"""
    if system == "windows":
        return '    "${_skia_package_prefix}/bin/skia.dll"\n    "${_skia_package_prefix}/lib/skia.dll.lib"'
    if system == "linux":
        return '    "${_skia_package_prefix}/lib/libskia.so"'
    if system == "darwin":
        return '    "${_skia_package_prefix}/lib/libskia.dylib"'
    raise RuntimeError(f"不支持的平台: {system}")


def get_imported_location_properties(system: str) -> str:
    """获取 Skia imported target 的平台二进制路径属性"""
    if system == "windows":
        return (
            '    IMPORTED_LOCATION "${_skia_package_prefix}/bin/skia.dll"\n'
            '    IMPORTED_IMPLIB "${_skia_package_prefix}/lib/skia.dll.lib"'
        )
    if system == "linux":
        return '    IMPORTED_LOCATION "${_skia_package_prefix}/lib/libskia.so"'
    if system == "darwin":
        return '    IMPORTED_LOCATION "${_skia_package_prefix}/lib/libskia.dylib"'
    raise RuntimeError(f"不支持的平台: {system}")


def normalize_macos_target_cpu(target_cpu: Optional[str]) -> str:
    """规范化 macOS GN target_cpu"""
    raw_cpu = (target_cpu or platform.machine()).lower()
    cpu_map = {
        "x64": "x64",
        "x86_64": "x64",
        "amd64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }

    if raw_cpu not in cpu_map:
        raise RuntimeError(f"不支持的 macOS CPU 架构: {raw_cpu}")

    return cpu_map[raw_cpu]


def get_macos_min_version(target_cpu: str) -> str:
    """获取 macOS 最低部署版本，参考 SkiaSharp 的配置"""
    return "11.0" if target_cpu == "arm64" else "10.13"


def render_args_gn_contents(src: str, system: str, target_cpu: Optional[str]) -> str:
    """生成当前平台最终写入构建目录的 args.gn 内容"""
    with open(src, "r", encoding="utf-8") as f:
        contents = f.read()

    if system == "darwin":
        macos_target_cpu = normalize_macos_target_cpu(target_cpu)
        min_macos_version = get_macos_min_version(macos_target_cpu)

        contents += "\n"
        contents += "# Generated by generate_skia_package.py for macOS.\n"
        contents += f'target_cpu = "{macos_target_cpu}"\n'
        contents += f'min_macos_version = "{min_macos_version}"\n'

    return contents


def prepare_args_gn_file(src: str, dst: str, system: str, target_cpu: Optional[str]) -> None:
    """复制 args.gn，并为需要动态参数的平台追加配置"""
    contents = render_args_gn_contents(src, system, target_cpu)
    dst_dir = os.path.dirname(dst)
    if dst_dir and not os.path.exists(dst_dir):
        os.makedirs(dst_dir)

    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(contents)

    if system == "darwin":
        macos_target_cpu = normalize_macos_target_cpu(target_cpu)
        min_macos_version = get_macos_min_version(macos_target_cpu)
        print(f"macOS target_cpu: {macos_target_cpu}")
        print(f"macOS min_macos_version: {min_macos_version}")


def build_args_gn_is_current(build_dir: str, system: str, target_cpu: Optional[str]) -> bool:
    """判断构建目录里的 args.gn 是否与当前打包模板一致"""
    args_gn_src = get_args_gn_file_path()
    args_gn_dst = os.path.join(build_dir, "args.gn")
    if not os.path.isfile(args_gn_dst):
        return False

    expected = render_args_gn_contents(args_gn_src, system, target_cpu)
    with open(args_gn_dst, "r", encoding="utf-8") as f:
        actual = f.read()

    return actual == expected


def get_expected_binary_description(system: str) -> str:
    """获取平台对应的 Skia 二进制产物描述"""
    if system == "windows":
        return "skia.dll 和 skia.dll.lib"
    elif system == "linux":
        return "libskia.so"
    elif system == "darwin":
        return "libskia.dylib"
    else:
        return "skia 二进制文件"


def build_skia(skia_repo_dir: str, build_dir: str, target_cpu: Optional[str] = None) -> None:
    """
    构建 Skia 库

    Args:
        skia_repo_dir: Skia 仓库目录
        build_dir: 构建目录
    """
    system = platform.system().lower()

    # 1. 如果构建目录不存在，创建它
    if not os.path.exists(build_dir):
        os.makedirs(build_dir)
        print(f"已创建构建目录: {build_dir}")

    # 2. 根据平台，复制对应的 args.gn 文件到构建目录
    args_gn_src = get_args_gn_file_path()
    args_gn_dst = os.path.join(build_dir, "args.gn")
    prepare_args_gn_file(args_gn_src, args_gn_dst, system, target_cpu)
    print(f"已复制 args.gn: {args_gn_src} -> {args_gn_dst}")

    # 3. 在 skia 仓库根目录执行 ./bin/gn gen <构建目录>
    gn_script = os.path.join(skia_repo_dir, "bin", "gn")
    # if system == "windows":
    #     gn_script = gn_script + ".bat"

    print(f"正在执行 GN 生成: {gn_script} gen {build_dir}")
    try:
        result = subprocess.run(
            [gn_script, "gen", build_dir],
            cwd=skia_repo_dir,
            check=True
        )
        print("GN 生成完成")
    except subprocess.CalledProcessError as e:
        print(f"GN 生成失败", file=sys.stderr)
        raise RuntimeError(f"GN 生成失败")

    # 4. 在 skia 仓库根目录执行 ninja -C <构建目录> skia
    # Skia 的默认 Ninja target 会包含测试/工具目标；预编译包只需要 skia 动态库。
    print(f"正在执行 Ninja 编译: ninja -C {build_dir} skia")
    try:
        result = subprocess.run(
            ["ninja", "-C", build_dir, "skia"],
            cwd=skia_repo_dir,
            check=True
        )
        print("Ninja 编译完成")
    except subprocess.CalledProcessError as e:
        print(f"Ninja 编译失败", file=sys.stderr)
        raise RuntimeError(f"Ninja 编译失败")


def find_binary_files(build_dir: str, system: str) -> dict:
    """查找构建目录中的二进制文件"""
    binaries = {}

    if system == "windows":
        # Windows: 查找 .dll 和 .dll.lib 文件
        dll_path = os.path.join(build_dir, "skia.dll")
        lib_path = os.path.join(build_dir, "skia.dll.lib")

        if os.path.isfile(dll_path):
            binaries["dll"] = dll_path
        if os.path.isfile(lib_path):
            binaries["lib"] = lib_path

    elif system == "linux":
        # Linux: 查找 .so 文件
        so_path = os.path.join(build_dir, "libskia.so")

        if os.path.isfile(so_path):
            binaries["so"] = so_path

    elif system == "darwin":
        # macOS: 查找 .dylib 文件
        dylib_path = os.path.join(build_dir, "libskia.dylib")

        if os.path.isfile(dylib_path):
            binaries["dylib"] = dylib_path

    return binaries


def has_required_binary_files(binaries: dict, system: str) -> bool:
    """判断当前平台所需的全部 Skia 二进制产物是否齐全"""
    if system == "windows":
        return "dll" in binaries and "lib" in binaries
    elif system == "linux":
        return "so" in binaries
    elif system == "darwin":
        return "dylib" in binaries
    else:
        return False


def write_cmake_package_files(
    output_dir: str,
    output_folder_name: str,
    system: str,
    target_cpu: Optional[str]
) -> None:
    """生成标准 CMake config package 文件"""
    version_info = parse_package_version(output_folder_name)
    package_platform = detect_package_platform(system, target_cpu)
    cmake_package_dir = os.path.join(output_dir, "lib", "cmake", "Skia")
    os.makedirs(cmake_package_dir, exist_ok=True)

    replacements = {
        "SKIA_PACKAGE_VERSION": version_info["version"],
        "SKIA_PACKAGE_VERSION_MAJOR": version_info["version_major"],
        "SKIA_MILESTONE": version_info["milestone"],
        "SKIA_COMMIT": version_info["commit"],
        "SKIA_PREBUILT_PLATFORM": package_platform,
        "SKIA_GPU_BACKENDS": get_gpu_backends(system),
        "SKIA_CMAKE_PLATFORM_CONDITION": get_cmake_platform_condition(system),
        "SKIA_INTERFACE_COMPILE_DEFINITIONS": get_interface_compile_definitions(system),
        "SKIA_IMPORTED_LOCATION_CHECKS": get_imported_location_checks(system),
        "SKIA_IMPORTED_LOCATION_PROPERTIES": get_imported_location_properties(system),
    }

    for template_name, output_name in (
        ("SkiaConfig.cmake.in", "SkiaConfig.cmake"),
        ("SkiaConfigVersion.cmake.in", "SkiaConfigVersion.cmake"),
        ("SkiaTargets.cmake.in", "SkiaTargets.cmake"),
    ):
        contents = render_template_file(template_name, replacements)
        dst = os.path.join(cmake_package_dir, output_name)
        write_text_file(dst, contents)
        print(f"已生成 CMake package 文件: {dst}")


def create_package(
    skia_repo_dir: str,
    build_dir: str,
    output_folder_name: str,
    target_cpu: Optional[str] = None,
    force_build: bool = False
) -> str:
    """
    创建 Skia CMake Package

    Args:
        skia_repo_dir: Skia 仓库目录
        build_dir: 构建目录
        output_folder_name: 输出文件夹名称

    Returns:
        生成的压缩包路径
    """
    system = platform.system().lower()

    # 提前解析版本，确保输出包名能生成标准 CMake package version file。
    parse_package_version(output_folder_name)

    # 查找二进制文件
    binaries = find_binary_files(build_dir, system)
    binaries_complete = has_required_binary_files(binaries, system)

    args_gn_current = build_args_gn_is_current(build_dir, system, target_cpu)
    should_build = force_build or not binaries_complete or not args_gn_current

    if should_build:
        if force_build:
            print("已指定 --force-build，开始重新构建 Skia...")
        elif not binaries_complete:
            print(f"在构建目录中未找到完整的 skia 二进制文件，开始自动构建...")
        else:
            print("构建目录中的 args.gn 与当前模板不一致，开始重新构建 Skia...")
        print("-" * 50)

        # 执行构建
        build_skia(skia_repo_dir, build_dir, target_cpu)

        print("-" * 50)
        print("构建完成，重新查找二进制文件...")

        # 重新查找二进制文件
        binaries = find_binary_files(build_dir, system)

        if not has_required_binary_files(binaries, system):
            expected_binary = get_expected_binary_description(system)
            raise FileNotFoundError(
                f"构建完成后在构建目录中仍未找到 {expected_binary}: {build_dir}"
            )

    # 创建临时目录
    temp_base = tempfile.gettempdir()
    random_suffix = generate_random_suffix()
    temp_dir = os.path.join(temp_base, f"skia_pkg_{random_suffix}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        # 创建输出目录结构
        output_dir = os.path.join(temp_dir, output_folder_name)
        os.makedirs(output_dir, exist_ok=True)

        # 创建子目录
        lib_dir = os.path.join(output_dir, "lib")
        dst_include = os.path.join(output_dir, "include")
        os.makedirs(lib_dir, exist_ok=True)
        os.makedirs(dst_include, exist_ok=True)

        # Windows 额外创建 bin 目录
        if system == "windows":
            bin_dir = os.path.join(output_dir, "bin")
            os.makedirs(bin_dir, exist_ok=True)

        # 复制 Skia include 文件夹
        src_include = os.path.join(skia_repo_dir, "include")
        if not os.path.isdir(src_include):
            raise FileNotFoundError(f"Skia include 目录不存在: {src_include}")

        copy_directory(src_include, dst_include)
        print(f"已复制 include 文件夹: {src_include} -> {dst_include}")

        # Skia 的公开头文件会引用 include 目录之外的 skcms 头文件。
        # 保持它们在 Skia 仓库中的相对路径，使原有 #include 语句可以正常解析。
        extra_header_paths = [
            ("modules", "skcms", "skcms.h"),
            ("modules", "skcms", "src", "skcms_public.h"),
        ]
        for path_parts in extra_header_paths:
            relative_path = os.path.join(*path_parts)
            src_header = os.path.join(skia_repo_dir, relative_path)
            dst_header = os.path.join(output_dir, relative_path)
            if not os.path.isfile(src_header):
                raise FileNotFoundError(f"Skia 必需头文件不存在: {src_header}")

            copy_file(src_header, dst_header)
            print(f"已复制必需头文件: {src_header} -> {dst_header}")

        # 复制二进制文件
        if system == "windows":
            if "dll" in binaries:
                dst_dll = os.path.join(bin_dir, "skia.dll")
                copy_file(binaries["dll"], dst_dll)
                print(f"已复制 DLL: {binaries['dll']} -> {dst_dll}")

            if "lib" in binaries:
                dst_lib = os.path.join(lib_dir, "skia.dll.lib")
                copy_file(binaries["lib"], dst_lib)
                print(f"已复制 LIB: {binaries['lib']} -> {dst_lib}")

        elif system == "linux":
            if "so" in binaries:
                dst_so = os.path.join(lib_dir, "libskia.so")
                copy_file(binaries["so"], dst_so)
                print(f"已复制 SO: {binaries['so']} -> {dst_so}")

        elif system == "darwin":
            if "dylib" in binaries:
                dst_dylib = os.path.join(lib_dir, "libskia.dylib")
                copy_file(binaries["dylib"], dst_dylib)
                print(f"已复制 DYLIB: {binaries['dylib']} -> {dst_dylib}")

        # 生成标准 CMake config package
        write_cmake_package_files(output_dir, output_folder_name, system, target_cpu)

        # 压缩打包 - 输出到当前工作目录
        original_cwd = os.getcwd()

        try:
            if system == "windows":
                # Windows: 压缩为 .zip
                zip_path = shutil.make_archive(
                    output_folder_name, 'zip', temp_dir, output_folder_name
                )
                result_path = zip_path
            else:
                # Linux/macOS: 压缩为 .tar.gz
                tar_path = shutil.make_archive(
                    output_folder_name, 'gztar', temp_dir, output_folder_name
                )
                result_path = tar_path

            print(f"已生成压缩包: {result_path}")
            return result_path

        finally:
            os.chdir(original_cwd)

    finally:
        # 清理临时目录
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            print(f"已清理临时目录: {temp_dir}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="生成 Skia CMake Package",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  Windows:
    python generate_skia_package.py C:\\skia C:\\skia\\out\\Release skia-m148-46f2e16555-win32-x64

  Linux:
    python generate_skia_package.py /path/to/skia /path/to/skia/out/Release skia-m148-46f2e16555-linux-x64

  macOS:
    python generate_skia_package.py /path/to/skia /path/to/skia/out/Release skia-m148-46f2e16555-macos-arm64 --target-cpu arm64
        """
    )

    parser.add_argument(
        "skia_repo_dir",
        help="Skia 仓库目录（包含 include 文件夹）"
    )
    parser.add_argument(
        "build_dir",
        help="构建目录（包含编译产物如 .dll/.so/.dylib 文件）"
    )
    parser.add_argument(
        "output_folder_name",
        help="输出文件夹名称"
    )
    parser.add_argument(
        "--target-cpu",
        choices=["x64", "x86_64", "amd64", "arm64", "aarch64"],
        help="macOS GN target_cpu（默认使用当前机器架构）"
    )
    parser.add_argument(
        "--force-build",
        action="store_true",
        help="即使构建目录中已有 Skia 二进制文件，也强制重新执行 GN + Ninja 构建"
    )

    args = parser.parse_args()

    # 将相对路径转换为绝对路径
    args.skia_repo_dir = os.path.abspath(args.skia_repo_dir)
    args.build_dir = os.path.abspath(args.build_dir)

    try:
        # 参数校验
        validate_directory(args.skia_repo_dir, "Skia 仓库")
        # validate_directory(args.build_dir, "构建目录")

        # 创建 Package
        system = platform.system()
        print(f"当前平台: {system}")
        print(f"Skia 仓库目录: {args.skia_repo_dir}")
        print(f"构建目录: {args.build_dir}")
        print(f"输出文件夹名称: {args.output_folder_name}")
        if args.target_cpu and platform.system().lower() != "darwin":
            print("--target-cpu 当前仅用于 macOS 构建，当前平台将忽略该参数")
        print("-" * 50)

        result_path = create_package(
            args.skia_repo_dir,
            args.build_dir,
            args.output_folder_name,
            args.target_cpu,
            args.force_build
        )

        print("-" * 50)
        print(f"成功! 压缩包路径: {result_path}")

    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
