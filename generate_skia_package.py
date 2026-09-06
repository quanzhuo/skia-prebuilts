#!/usr/bin/env python3
"""
Skia CMake Package 生成脚本

从同一 Skia checkout 构建 full / cpu-only 两种可独立消费的 CMake Package。

用法:
    python generate_skia_package.py <skia_repo_dir> <version> [--profile both|full|cpu-only]
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
from contextlib import contextmanager
from typing import Optional


def validate_directory(path: str, name: str) -> None:
    """验证目录是否存在"""
    if not os.path.isdir(path):
        raise ValueError(f"{name} 目录不存在或无效: {path}")


PROFILES = ("full", "cpu-only")


def validate_profile(profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError(f"不支持的构建 profile: {profile}")


def copy_directory(src: str, dst: str) -> None:
    """复制到新建的 staging 路径，不覆盖或删除现有目录。"""
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


def get_source_patch_paths(system: str, profile: str = "full") -> list[str]:
    """返回构建指定平台所需的、受版本控制的 Skia source patches。"""
    validate_profile(profile)
    names = []
    if system == "windows" and profile == "full":
        names.append("0001-expose-angle-runtime-target.patch")
    # PathOps is a CPU geometry capability, independent of platform/GPU backend.
    names.append("0002-include-pathops-in-skia.patch")
    return [os.path.join(get_script_dir(), "patches", name) for name in names]


BUILD_INPUTS_STAMP = ".skia-package-build-inputs.sha256"


def source_build_fingerprint(skia_repo_dir: str, system: str, profile: str = "full") -> str:
    """记录提交、profile、配置和补丁，避免复用其他构建输入的二进制。"""
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=skia_repo_dir,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    digest = hashlib.sha256()
    digest.update(f"skia-package-inputs-v2\n{system}\n{profile}\n{revision}\n".encode("utf-8"))
    digest.update(read_text_file(get_args_gn_file_path(system, profile)).encode("utf-8") + b"\0")
    for patch_path in get_source_patch_paths(system, profile):
        digest.update(os.path.basename(patch_path).encode("utf-8") + b"\0")
        # Git patches are text. Do not rebuild solely due to checkout EOL style.
        digest.update(read_text_file(patch_path).encode("utf-8") + b"\0")
    return digest.hexdigest()


def build_source_is_current(build_dir: str, fingerprint: str) -> bool:
    stamp_path = os.path.join(build_dir, BUILD_INPUTS_STAMP)
    try:
        return read_text_file(stamp_path).strip() == fingerprint
    except FileNotFoundError:
        return False


def git_apply_check(repo_dir: str, patch_path: str, reverse: bool = False) -> bool:
    command = ["git", "apply", "--check"]
    if reverse:
        command.append("--reverse")
    command.append(patch_path)
    result = subprocess.run(
        command,
        cwd=repo_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


@contextmanager
def applied_source_patches(skia_repo_dir: str, system: str, profile: str = "full"):
    """临时应用打包 patch，并且只撤销本次调用实际应用的 patch。"""
    applied = []
    try:
        for patch_path in get_source_patch_paths(system, profile):
            if not os.path.isfile(patch_path):
                raise FileNotFoundError(f"Skia source patch 不存在: {patch_path}")

            if git_apply_check(skia_repo_dir, patch_path):
                subprocess.run(
                    ["git", "apply", patch_path],
                    cwd=skia_repo_dir,
                    check=True,
                )
                applied.append(patch_path)
                print(f"已临时应用 Skia source patch: {patch_path}")
            elif git_apply_check(skia_repo_dir, patch_path, reverse=True):
                print(f"Skia source patch 已存在，保持现状: {patch_path}")
            else:
                raise RuntimeError(
                    f"Skia source patch 无法应用到当前源码版本: {patch_path}")
        yield
    finally:
        restore_errors = []
        for patch_path in reversed(applied):
            try:
                subprocess.run(
                    ["git", "apply", "--reverse", patch_path],
                    cwd=skia_repo_dir,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                print(f"已撤销临时 Skia source patch: {patch_path}")
            except (OSError, subprocess.CalledProcessError) as error:
                # One conflicting patch must not prevent independent patches
                # from being restored. Never certify this build as successful.
                restore_errors.append((patch_path, error))
        if restore_errors:
            details = "\n".join(
                f"{path}: {getattr(error, 'stderr', None) or str(error)}"
                for path, error in restore_errors
            )
            raise RuntimeError(
                f"以下临时 Skia source patches 未能撤销，请检查源码工作区后重试：\n{details}"
            ) from restore_errors[0][1]


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


def get_args_gn_file_path(system: str, profile: str = "full") -> str:
    """获取平台对应的 args.gn 文件路径"""
    validate_profile(profile)
    suffixes = {"windows": "windows", "linux": "linux", "darwin": "macos"}
    if system not in suffixes:
        raise RuntimeError(f"不支持的平台: {system}")
    return os.path.join(get_script_dir(), "configs", profile, f"args.gn.{suffixes[system]}")


def parse_package_version(version: str) -> dict:
    """只接受版本号，不接受路径、profile、平台或完整输出文件名。"""
    match = re.fullmatch(r"m(?P<milestone>[1-9][0-9]*)-(?P<commit>[0-9a-fA-F]{7,40})", version)
    if not match:
        raise ValueError(
            "version 必须形如 m152-0873ec164a（提交号为 7 到 40 位十六进制）。"
        )

    milestone_number = match.group("milestone")
    return {
        "version": f"{milestone_number}.0.0",
        "version_major": milestone_number,
        "milestone": f"m{milestone_number}",
        "commit": match.group("commit").lower(),
    }


def validate_source_version(skia_repo_dir: str, version: str) -> None:
    info = parse_package_version(version)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=skia_repo_dir,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    milestone_header = read_text_file(os.path.join(skia_repo_dir, "include", "core", "SkMilestone.h"))
    milestone = re.search(r"^\s*#define\s+SK_MILESTONE\s+(\d+)\s*$", milestone_header, re.MULTILINE)
    if not revision.startswith(info["commit"]) or not milestone or milestone.group(1) != info["version_major"]:
        raise ValueError(f"版本号 {version} 与当前 Skia 源码不一致（HEAD={revision}）。脚本不会自动切换提交。")


def package_name(version: str, system: str, target_cpu: Optional[str], profile: str) -> str:
    validate_profile(profile)
    info = parse_package_version(version)
    suffix = "-cpu-only" if profile == "cpu-only" else ""
    return f"skia-{info['milestone']}-{info['commit']}{suffix}-{detect_package_platform(system, target_cpu)}"


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
    systems = {"windows": "win32", "linux": "linux", "darwin": "macos"}
    if system not in systems:
        raise RuntimeError(f"不支持的平台: {system}")
    cpu = normalize_cpu_name(target_cpu or platform.machine())
    if system != "darwin" and cpu != normalize_cpu_name(platform.machine()):
        raise ValueError("--target-cpu 跨架构构建目前仅支持 macOS；Windows/Linux 使用本机架构。")
    return f"{systems[system]}-{cpu}"


def get_cmake_platform_condition(system: str) -> str:
    """获取 CMake config package 的平台保护条件"""
    if system == "windows":
        return "WIN32"
    if system == "linux":
        return "UNIX AND NOT APPLE"
    if system == "darwin":
        return "APPLE"
    raise RuntimeError(f"不支持的平台: {system}")


def get_gpu_backends(system: str, profile: str = "full") -> str:
    """获取包内启用的 GPU 后端描述"""
    validate_profile(profile)
    if system not in ("windows", "linux", "darwin"):
        raise RuntimeError(f"不支持的平台: {system}")
    if profile == "cpu-only":
        return ""
    if system == "windows":
        return "ANGLE;EGL;OpenGLES"
    if system == "linux":
        return "Vulkan;EGL;GL"
    if system == "darwin":
        return "Metal"
    raise RuntimeError(f"不支持的平台: {system}")


def get_interface_compile_definitions(system: str, profile: str = "full") -> str:
    """获取 Skia imported target 的 public compile definitions"""
    common_definitions = [
        "SKIA_DLL",
        "SK_RELEASE",
        "SK_DISABLE_TRACING",
        "SK_CODEC_DECODES_BMP",
        "SK_CODEC_DECODES_WBMP",
        "SK_CODEC_DECODES_ICO",
        "SK_CODEC_DECODES_JPEG",
        "SK_CODEC_DECODES_PNG",
        "SK_CODEC_DECODES_PNG_WITH_LIBPNG",
        "SK_CODEC_ENCODES_PNG",
        "SK_CODEC_ENCODES_PNG_WITH_LIBPNG",
    ]

    if system == "windows":
        platform_definitions = [
            "SK_GANESH",
            "SK_GL",
            "SK_ASSUME_GL_ES=1",
            "SK_TYPEFACE_FACTORY_DIRECTWRITE",
            "SK_FONTMGR_DIRECTWRITE_AVAILABLE",
        ]
    elif system == "linux":
        platform_definitions = [
            "SK_GANESH",
            "SK_VULKAN",
            "SK_GL",
            "SK_USE_INTERNAL_VULKAN_HEADERS",
            "SK_R32_SHIFT=16",
            "SK_FONTMGR_FREETYPE_DIRECTORY_AVAILABLE",
            "SK_FONTMGR_FREETYPE_EMBEDDED_AVAILABLE",
            "SK_FONTMGR_FREETYPE_EMPTY_AVAILABLE",
            "SK_FONTMGR_FONTCONFIG_AVAILABLE",
            "SK_FONTMGR_FCI_AVAILABLE",
        ]
    elif system == "darwin":
        platform_definitions = [
            "SK_GANESH",
            "SK_METAL",
            "SK_ENABLE_API_AVAILABLE",
            "SK_TYPEFACE_FACTORY_CORETEXT",
            "SK_FONTMGR_CORETEXT_AVAILABLE",
        ]
    else:
        raise RuntimeError(f"不支持的平台: {system}")

    validate_profile(profile)
    if profile == "cpu-only":
        gpu_definitions = {"SK_GANESH", "SK_GL", "SK_ASSUME_GL_ES=1", "SK_VULKAN", "SK_USE_INTERNAL_VULKAN_HEADERS", "SK_METAL"}
        platform_definitions = [value for value in platform_definitions if value not in gpu_definitions]
    return ";".join(common_definitions + platform_definitions)


def get_imported_location_checks(system: str, profile: str = "full") -> str:
    """获取 SkiaTargets.cmake 用来校验包完整性的文件列表"""
    return "\n".join(f'    "${{_skia_package_prefix}}/{destination}"'
                     for _, destination in binary_layout(system, profile).values())


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


def get_auxiliary_imported_targets(system: str, profile: str = "full") -> str:
    """生成 Skia 随包运行时的 imported targets。"""
    validate_profile(profile)
    if system != "windows" or profile == "cpu-only":
        return ""

    return '''
add_library(Skia::AngleEGL SHARED IMPORTED)
set_target_properties(Skia::AngleEGL PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_skia_package_prefix}/angle/include"
    IMPORTED_LOCATION "${_skia_package_prefix}/bin/libEGL.dll"
    IMPORTED_IMPLIB "${_skia_package_prefix}/lib/libEGL.dll.lib"
)

add_library(Skia::AngleGLESv2 SHARED IMPORTED)
set_target_properties(Skia::AngleGLESv2 PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_skia_package_prefix}/angle/include"
    IMPORTED_LOCATION "${_skia_package_prefix}/bin/libGLESv2.dll"
    IMPORTED_IMPLIB "${_skia_package_prefix}/lib/libGLESv2.dll.lib"
)

add_library(Skia::Angle INTERFACE IMPORTED)
set_target_properties(Skia::Angle PROPERTIES
    INTERFACE_LINK_LIBRARIES "Skia::AngleEGL;Skia::AngleGLESv2"
)
'''.strip()


def render_args_gn_contents(src: str, system: str, target_cpu: Optional[str]) -> str:
    """生成当前平台最终写入构建目录的 args.gn 内容"""
    with open(src, "r", encoding="utf-8") as f:
        contents = f.read()

    detect_package_platform(system, target_cpu)
    cpu = normalize_cpu_name(target_cpu or platform.machine())
    return contents + f'\n# Generated by generate_skia_package.py.\ntarget_cpu = "{cpu}"\n'


def prepare_args_gn_file(src: str, dst: str, system: str, target_cpu: Optional[str]) -> None:
    """复制 args.gn，并为需要动态参数的平台追加配置"""
    write_text_file(dst, render_args_gn_contents(src, system, target_cpu))


def build_args_gn_is_current(build_dir: str, system: str, target_cpu: Optional[str], profile: str = "full") -> bool:
    """判断构建目录里的 args.gn 是否与当前打包模板一致"""
    args_gn_src = get_args_gn_file_path(system, profile)
    args_gn_dst = os.path.join(build_dir, "args.gn")
    if not os.path.isfile(args_gn_dst):
        return False

    expected = render_args_gn_contents(args_gn_src, system, target_cpu)
    with open(args_gn_dst, "r", encoding="utf-8") as f:
        actual = f.read()

    return actual == expected


def get_expected_binary_description(system: str, profile: str = "full") -> str:
    """获取平台对应的 Skia 二进制产物描述"""
    if system == "windows":
        return "Skia DLL 与 import library" if profile == "cpu-only" else "Skia、ANGLE EGL 和 ANGLE GLESv2 的 DLL 与 import library"
    elif system == "linux":
        return "libskia.so"
    elif system == "darwin":
        return "libskia.dylib"
    else:
        return "skia 二进制文件"


def resolve_jobs(jobs: Optional[int] = None) -> int:
    """优先采用进程可用的逻辑 CPU 数；允许调用者显式限制并发。"""
    if jobs is not None:
        if isinstance(jobs, bool) or not isinstance(jobs, int) or jobs < 1:
            raise ValueError("jobs 必须是正整数。")
        return jobs

    # Python 3.13+ accounts for process CPU availability where supported.
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if process_cpu_count is not None:
        count = process_cpu_count()
        if count is not None and count > 0:
            return count
    # Preserve affinity limits on older Python versions where this is available.
    get_affinity = getattr(os, "sched_getaffinity", None)
    if get_affinity is not None:
        try:
            count = len(get_affinity(0))
            if count > 0:
                return count
        except (OSError, NotImplementedError):
            pass
    return max(1, os.cpu_count() or 1)


def positive_jobs(value: str) -> int:
    """供 argparse 使用，在开始构建前拒绝 0/负数/非整数。"""
    try:
        return resolve_jobs(int(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError("并发数必须是正整数。") from error


def build_skia(
    skia_repo_dir: str,
    build_dir: str,
    target_cpu: Optional[str] = None,
    jobs: Optional[int] = None,
    profile: str = "full",
) -> None:
    """
    构建 Skia 库

    Args:
        skia_repo_dir: Skia 仓库目录
        build_dir: 构建目录
    """
    jobs = resolve_jobs(jobs)
    system = platform.system().lower()
    fingerprint = source_build_fingerprint(skia_repo_dir, system, profile)
    if not os.path.exists(build_dir):
        os.makedirs(build_dir, exist_ok=True)
        print(f"已创建构建目录: {build_dir}")
    stamp_path = os.path.join(build_dir, BUILD_INPUTS_STAMP)
    # Invalidate before any source mutation/build. A failed --force-build must
    # not leave a successful old stamp that permits packaging stale binaries.
    write_text_file(stamp_path, "")

    with applied_source_patches(skia_repo_dir, system, profile):
        # 1. 根据平台，复制对应的 args.gn 文件到构建目录
        args_gn_src = get_args_gn_file_path(system, profile)
        args_gn_dst = os.path.join(build_dir, "args.gn")
        prepare_args_gn_file(args_gn_src, args_gn_dst, system, target_cpu)
        print(f"已复制 args.gn: {args_gn_src} -> {args_gn_dst}")

        # 2. 在 skia 仓库根目录执行 ./bin/gn gen <构建目录>
        gn_script = os.path.join(skia_repo_dir, "bin", "gn")

        print(f"正在执行 GN 生成: {gn_script} gen {build_dir}")
        try:
            subprocess.run(
                [gn_script, "gen", build_dir, "--fail-on-unused-args"],
                cwd=skia_repo_dir,
                check=True
            )
            print("GN 生成完成")
        except subprocess.CalledProcessError:
            print("GN 生成失败", file=sys.stderr)
            raise RuntimeError("GN 生成失败")

        # 3. 在 skia 仓库根目录执行所需的最小 Ninja targets。
        targets = ["skia"]
        if system == "windows" and profile == "full":
            targets.append("skia_angle_runtime")
        print(f"正在执行 Ninja 编译: ninja -C {build_dir} -j {jobs} {' '.join(targets)}")
        try:
            subprocess.run(
                ["ninja", "-C", build_dir, "-j", str(jobs), *targets],
                cwd=skia_repo_dir,
                check=True
            )
            print("Ninja 编译完成")
        except subprocess.CalledProcessError:
            print("Ninja 编译失败", file=sys.stderr)
            raise RuntimeError("Ninja 编译失败")

    # Only certify inputs after both compilation and patch restoration succeed.
    if source_build_fingerprint(skia_repo_dir, system, profile) != fingerprint:
        raise RuntimeError("构建期间 Skia 提交、配置或打包补丁发生变化，请重新构建。")
    write_text_file(stamp_path, fingerprint + "\n")


def binary_layout(system: str, profile: str = "full") -> dict:
    """唯一的二进制清单：key -> (GN 输出名, 包内相对路径)。"""
    validate_profile(profile)
    if system == "windows":
        outputs = {
            "dll": ("skia.dll", "bin/skia.dll"),
            "lib": ("skia.dll.lib", "lib/skia.dll.lib"),
        }
        if profile == "full":
            outputs.update({
                "egl_dll": ("libEGL.dll", "bin/libEGL.dll"),
                "egl_lib": ("libEGL.dll.lib", "lib/libEGL.dll.lib"),
                "gles_dll": ("libGLESv2.dll", "bin/libGLESv2.dll"),
                "gles_lib": ("libGLESv2.dll.lib", "lib/libGLESv2.dll.lib"),
            })
        return outputs
    if system == "linux":
        return {"so": ("libskia.so", "lib/libskia.so")}
    if system == "darwin":
        return {"dylib": ("libskia.dylib", "lib/libskia.dylib")}
    raise RuntimeError(f"不支持的平台: {system}")


def find_binary_files(build_dir: str, system: str, profile: str = "full") -> dict:
    # Never include unrelated/stale outputs, particularly ANGLE in CPU-only.
    return {
        key: os.path.join(build_dir, source)
        for key, (source, _) in binary_layout(system, profile).items()
        if os.path.isfile(os.path.join(build_dir, source))
    }


def has_required_binary_files(binaries: dict, system: str, profile: str = "full") -> bool:
    return all(key in binaries for key in binary_layout(system, profile))


def write_cmake_package_files(
    output_dir: str,
    version: str,
    system: str,
    target_cpu: Optional[str],
    profile: str = "full",
) -> None:
    """生成标准 CMake config package 文件。"""
    version_info = parse_package_version(version)
    package_platform = detect_package_platform(system, target_cpu)
    cmake_package_dir = os.path.join(output_dir, "lib", "cmake", "Skia")
    replacements = {
        "SKIA_PACKAGE_VERSION": version_info["version"],
        "SKIA_PACKAGE_VERSION_MAJOR": version_info["version_major"],
        "SKIA_MILESTONE": version_info["milestone"],
        "SKIA_COMMIT": version_info["commit"],
        "SKIA_PREBUILT_PLATFORM": package_platform,
        "SKIA_BUILD_PROFILE": profile,
        "SKIA_GPU_BACKENDS": get_gpu_backends(system, profile),
        "SKIA_CMAKE_PLATFORM_CONDITION": get_cmake_platform_condition(system),
        "SKIA_INTERFACE_COMPILE_DEFINITIONS": get_interface_compile_definitions(system, profile),
        "SKIA_IMPORTED_LOCATION_CHECKS": get_imported_location_checks(system, profile),
        "SKIA_IMPORTED_LOCATION_PROPERTIES": get_imported_location_properties(system),
        "SKIA_AUXILIARY_IMPORTED_TARGETS": get_auxiliary_imported_targets(system, profile),
    }
    for template_name in ("SkiaConfig", "SkiaConfigVersion", "SkiaTargets"):
        contents = render_template_file(template_name + ".cmake.in", replacements)
        write_text_file(os.path.join(cmake_package_dir, template_name + ".cmake"), contents)


def create_package(
    skia_repo_dir: str,
    build_dir: str,
    version: str,
    target_cpu: Optional[str] = None,
    force_build: bool = False,
    jobs: Optional[int] = None,
    profile: str = "full",
    output_dir: Optional[str] = None,
) -> str:
    """构建一个 profile 并原子替换其压缩包；调用方负责 checkout 级互斥。"""
    system = platform.system().lower()
    name = package_name(version, system, target_cpu, profile)
    validate_source_version(skia_repo_dir, version)
    fingerprint = source_build_fingerprint(skia_repo_dir, system, profile)
    binaries = find_binary_files(build_dir, system, profile)
    if (force_build or not has_required_binary_files(binaries, system, profile)
            or not build_args_gn_is_current(build_dir, system, target_cpu, profile)
            or not build_source_is_current(build_dir, fingerprint)):
        print(f"[{profile}] 开始构建（配置、源码、补丁或成功记录需要更新）。")
        build_skia(skia_repo_dir, build_dir, target_cpu, jobs=jobs, profile=profile)
        binaries = find_binary_files(build_dir, system, profile)
    else:
        print(f"[{profile}] 复用已验证构建记录对应的二进制。")
    if not has_required_binary_files(binaries, system, profile):
        raise FileNotFoundError(f"构建产物不完整: {get_expected_binary_description(system, profile)}: {build_dir}")

    output_dir = os.path.abspath(output_dir or os.path.join(os.getcwd(), "dist"))
    os.makedirs(output_dir, exist_ok=True)
    # Staging is on the destination volume. A failed copy/compression cannot
    # truncate a previously published archive.
    with tempfile.TemporaryDirectory(prefix=".skia-package-", dir=output_dir) as staging:
        package_dir = os.path.join(staging, name)
        os.makedirs(package_dir)
        copy_directory(os.path.join(skia_repo_dir, "include"), os.path.join(package_dir, "include"))
        if system == "windows" and profile == "full":
            copy_directory(
                os.path.join(skia_repo_dir, "third_party", "externals", "angle2", "include"),
                os.path.join(package_dir, "angle", "include"),
            )
        for relative_path in ("modules/skcms/skcms.h", "modules/skcms/src/skcms_public.h"):
            copy_file(os.path.join(skia_repo_dir, relative_path), os.path.join(package_dir, relative_path))
        for key, (_, destination) in binary_layout(system, profile).items():
            copy_file(binaries[key], os.path.join(package_dir, destination))
        write_cmake_package_files(package_dir, version, system, target_cpu, profile)
        validate_source_version(skia_repo_dir, version)
        if source_build_fingerprint(skia_repo_dir, system, profile) != fingerprint:
            raise RuntimeError("构建或打包期间源码提交、配置或补丁发生变化，请重新运行。")
        archive_format = "zip" if system == "windows" else "gztar"
        archive = shutil.make_archive(os.path.join(staging, name), archive_format, staging, name)
        destination = os.path.join(output_dir, os.path.basename(archive))
        os.replace(archive, destination)
    print(f"[{profile}] 已生成: {destination}")
    return destination


@contextmanager
def checkout_build_lock(skia_repo_dir: str):
    """进程退出自动释放；拒绝同一 checkout 的并发脚本，避免临时补丁互相撤销。"""
    git_dir = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=skia_repo_dir,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    # Keep the stable lock file in Git metadata, not in the source worktree.
    # Do not unlink it: on POSIX replacing an inode can create two independent locks.
    with open(os.path.join(git_dir, "skia-prebuilts.lock"), "a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("已有打包脚本正在使用该 Skia checkout，请等待它完成。") from error
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("已有打包脚本正在使用该 Skia checkout，请等待它完成。") from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def create_packages(
    skia_repo_dir: str,
    version: str,
    build_root: Optional[str] = None,
    output_dir: Optional[str] = None,
    profile: str = "both",
    target_cpu: Optional[str] = None,
    force_build: bool = False,
    jobs: Optional[int] = None,
) -> list[str]:
    """同一源码顺序构建两套独立产物；不同时启用两个 Ninja 构建。"""
    skia_repo_dir = os.path.abspath(skia_repo_dir)
    validate_directory(skia_repo_dir, "Skia 仓库")
    parse_package_version(version)
    selected_profiles = PROFILES if profile == "both" else (profile,)
    for selected in selected_profiles:
        validate_profile(selected)
    resolve_jobs(jobs)
    package_platform = detect_package_platform(platform.system().lower(), target_cpu)
    build_root = os.path.abspath(build_root or os.path.join(skia_repo_dir, "out", "prebuilts"))
    output_dir = os.path.abspath(output_dir or os.path.join(os.getcwd(), "dist"))
    with checkout_build_lock(skia_repo_dir):
        validate_source_version(skia_repo_dir, version)
        return [
            create_package(
                skia_repo_dir, os.path.join(build_root, package_platform, selected), version,
                target_cpu, force_build, jobs=jobs, profile=selected, output_dir=output_dir,
            )
            for selected in selected_profiles
        ]


def main():
    parser = argparse.ArgumentParser(
        description="从同一 Skia checkout 生成 full 和 cpu-only CMake 包",
        epilog="示例: python generate_skia_package.py ../skia m152-0873ec164a",
    )
    parser.add_argument("skia_repo_dir", help="Skia 源码仓库（请先同步依赖）")
    parser.add_argument("version", help="版本号，例如 m152-0873ec164a；必须对应当前源码")
    parser.add_argument("--profile", choices=("both", *PROFILES), default="both",
                        help="默认 both，顺序生成 full 和 cpu-only 两个包")
    parser.add_argument("--build-root", help="构建根目录，默认 <Skia仓库>/out/prebuilts")
    parser.add_argument("--output-dir", help="压缩包输出目录，默认当前目录下的 dist")
    parser.add_argument("--target-cpu", choices=("x64", "x86_64", "amd64", "arm64", "aarch64"),
                        help="默认本机架构；只有 macOS 支持跨架构构建")
    parser.add_argument("--force-build", action="store_true", help="强制重新执行 GN + Ninja")
    parser.add_argument("-j", "--jobs", type=positive_jobs, metavar="N",
                        help="Ninja 并发数，默认自动检测进程可用的逻辑 CPU 数")
    args = parser.parse_args()
    try:
        results = create_packages(
            args.skia_repo_dir, args.version, build_root=args.build_root,
            output_dir=args.output_dir, profile=args.profile, target_cpu=args.target_cpu,
            force_build=args.force_build, jobs=args.jobs,
        )
        print(f"成功! 共生成 {len(results)} 个包。")
        for result in results:
            print(result)
    except Exception as error:
        print(f"错误: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
