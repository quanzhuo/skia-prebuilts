#!/usr/bin/env python3
"""
Skia CMake Package 生成脚本

用于在已构建完成的 Skia 仓库中生成一个 CMake Package，
以便其他项目能够方便地集成和使用 Skia 库。

用法:
    python generate_skia_package.py <skia_repo_dir> <build_dir> <output_folder_name>

参数:
    skia_repo_dir: Skia 仓库目录（包含 include 文件夹）
    build_dir: 构建目录（包含编译产物如 .dll/.so 文件）
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


def get_cmake_file_path() -> str:
    """获取平台对应的 CMakeLists.txt 文件路径"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    system = platform.system().lower()
    
    if system == "windows":
        return os.path.join(script_dir, "cmake", "windows.cmake")
    elif system == "linux":
        return os.path.join(script_dir, "cmake", "linux.cmake")
    else:
        raise RuntimeError(f"不支持的平台: {system}")


def get_args_gn_file_path() -> str:
    """获取平台对应的 args.gn 文件路径"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    system = platform.system().lower()
    
    if system == "windows":
        return os.path.join(script_dir, "args.gn.windows")
    elif system == "linux":
        return os.path.join(script_dir, "args.gn.linux")
    else:
        raise RuntimeError(f"不支持的平台: {system}")


def build_skia(skia_repo_dir: str, build_dir: str) -> None:
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
    copy_file(args_gn_src, args_gn_dst)
    print(f"已复制 args.gn: {args_gn_src} -> {args_gn_dst}")
    
    # 3. 在 skia 仓库根目录执行 ./bin/gn gen <构建目录>
    gn_script = os.path.join(skia_repo_dir, "bin", "gn")
    if system == "windows":
        gn_script = gn_script + ".bat"
    
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
    
    return binaries


def create_package(skia_repo_dir: str, build_dir: str, output_folder_name: str) -> str:
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
    
    # 验证 CMakeLists.txt 文件存在
    cmake_file = get_cmake_file_path()
    if not os.path.isfile(cmake_file):
        raise FileNotFoundError(f"CMakeLists.txt 文件不存在: {cmake_file}")
    
    # 查找二进制文件
    binaries = find_binary_files(build_dir, system)
    
    if not binaries:
        print(f"在构建目录中未找到 skia 二进制文件，开始自动构建...")
        print("-" * 50)
        
        # 执行构建
        build_skia(skia_repo_dir, build_dir)
        
        print("-" * 50)
        print("构建完成，重新查找二进制文件...")
        
        # 重新查找二进制文件
        binaries = find_binary_files(build_dir, system)
        
        if not binaries:
            if system == "windows":
                raise FileNotFoundError(
                    f"构建完成后在构建目录中仍未找到 skia.dll 或 skia.dll.lib: {build_dir}"
                )
            else:
                raise FileNotFoundError(
                    f"构建完成后在构建目录中仍未找到 libskia.so: {build_dir}"
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
        
        # 复制 CMakeLists.txt
        dst_cmake = os.path.join(output_dir, "CMakeLists.txt")
        copy_file(cmake_file, dst_cmake)
        print(f"已复制 CMakeLists.txt: {cmake_file} -> {dst_cmake}")
        
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
                # Linux: 压缩为 .tar.gz
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
    python generate_skia_package.py C:\\skia C:\\skia\\out\\Release skia-win-x64
  
  Linux:
    python generate_skia_package.py /path/to/skia /path/to/skia/out/Release skia-linux-x64
        """
    )
    
    parser.add_argument(
        "skia_repo_dir",
        help="Skia 仓库目录（包含 include 文件夹）"
    )
    parser.add_argument(
        "build_dir",
        help="构建目录（包含编译产物如 .dll/.so 文件）"
    )
    parser.add_argument(
        "output_folder_name",
        help="输出文件夹名称"
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
        print("-" * 50)
        
        result_path = create_package(
            args.skia_repo_dir,
            args.build_dir,
            args.output_folder_name
        )
        
        print("-" * 50)
        print(f"成功! 压缩包路径: {result_path}")
        
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
