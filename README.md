# Skia Prebuilts

Native shared-library packages for Windows, Linux and macOS, on x64 and ARM64.
Each release provides **12 packages**: six OS/architecture combinations, each
with a `cpu` and a `gpu` profile. Build and packaging tools are MIT licensed;
Skia and bundled dependencies retain their original licenses.

## Release identity

[`release.json`](release.json) is the single source of release identity. It pins
the milestone, full upstream SHA and a positive release revision.

| Identity | Example |
| --- | --- |
| Release tag | `m152-0873ec164a-r1` |
| CMake version | `152.1.0` |
| CPU package | `skia-m152-0873ec164a-r1-cpu-windows-x64.zip` |
| GPU package | `skia-m152-0873ec164a-r1-gpu-linux-arm64.tar.gz` |

`revision` increases monotonically **within a milestone**. Increment it whenever
the upstream commit, patches, toolchain, configuration or packaging changes.
Changing the upstream commit does not reset the counter. The CMake version is
`<milestone>.<revision>.0`; metadata also retains the full upstream SHA.
Published releases are never replaced.

Platforms: `windows-x64`, `windows-arm64`, `linux-x64`, `linux-arm64`, `macos-x64`
and `macos-arm64`. Windows archives are ZIP; Linux/macOS archives are tar.gz.
Every archive has one top-level directory matching its filename without the
archive extension. Both profiles are explicitly named; `gpu` includes CPU raster.

## Profiles

| Capability | `cpu` | `gpu` |
| --- | --- | --- |
| CPU raster, PathOps, PNG encode/decode, JPEG decode | Yes | Yes |
| Windows GPU | — | Ganesh + ANGLE; EGL/GLES runtime included |
| Linux GPU | — | Ganesh + Vulkan and EGL/GL; no X11/GLX requirement |
| macOS GPU | — | Ganesh + Metal |

The GPU profile does not enable every optional Skia module. Configurations under
`configs/` define the exact selection. Font backends are DirectWrite on Windows,
Fontconfig with bundled FreeType on Linux, and CoreText on macOS. The profiles
are alternative packages and cannot be imported together.

## Prepare sources and build

Requirements: Python 3.11+, Git, CMake 3.21+, and a native Clang toolchain.
Use a new source directory for a different upstream commit.

```sh
python tools/prepare_source.py build/skia
export PATH="$PWD/build/skia/third_party/ninja:$PATH"
python generate_skia_package.py build/skia --jobs 12
python tools/verify_packages.py dist --jobs 12
```

Source preparation fetches the exact commit from the official Skia repository,
then invokes upstream `tools/git-sync-deps` and `bin/fetch-ninja`. The sync script
fetches GN. Chromium depot_tools is optional for this workflow. Upstream's
`GIT_SYNC_DEPS_PATH` override selects dependencies required by the existing
profiles, preserving their exact upstream URLs and commits. Unrelated Android,
WASM, Dawn and test dependencies are omitted. GN feature flags are not rewritten.
See the official [download](https://skia.org/docs/user/download/) and
[build](https://skia.org/docs/user/build/) documentation.

Linux needs Clang, a C++ standard library/toolchain, pkg-config, Fontconfig
and EGL/GL development packages. macOS needs Xcode and its command-line tools.
Windows needs Visual Studio C++ tools, a Windows SDK and native LLVM under
`C:/Program Files/LLVM`. Windows ARM64 requires ARM64 C++ tools and native ARM64 LLVM.

On Windows, run commands through the developer environment wrapper:

```powershell
$env:PATH = "$PWD/build/skia/third_party/ninja;$env:PATH"
./tools/Invoke-WindowsBuild.ps1 -Executable python -Arguments @('generate_skia_package.py', 'build/skia', '--jobs', '12')
./tools/Invoke-WindowsBuild.ps1 -Executable python -Arguments @('tools/verify_packages.py', 'dist', '--jobs', '12')
```

Use `-TargetCpu arm64` on a Windows ARM64 host. The wrapper locates Visual Studio
with vswhere and verifies the native MSVC compiler/linker environment. Skia is
built with **clang-cl and lld-link**. Both profiles build sequentially. Options:
`--profile cpu|gpu|both`, `--build-root`, `--output-dir`, `--release-file`,
`--target-cpu`, `--force-build`, and `--jobs`. Cross-architecture builds are
supported only on macOS; release CI tests every architecture on a native runner.

## CMake package contract

```cmake
find_package(Skia 152.1.0 EXACT CONFIG REQUIRED
    PATHS "/path/to/extracted/package" NO_DEFAULT_PATH)
target_link_libraries(my_application PRIVATE Skia::Skia)
```

The target supplies C++20 and required public definitions. Windows GPU consumers
also link `Skia::Angle`; use `TARGET_RUNTIME_DLLS` for runtime deployment.
See the [FetchContent example](cmake/CMakeLists.example.cmake).

Packages reject incompatible OS/CPU combinations before importing targets.
A single-version request must match the actual package revision. An explicit
version range expresses the consumer's acceptance policy; a shared milestone
is not an ABI compatibility promise.

Metadata: `Skia_VERSION`, `Skia_RELEASE_TAG`, `Skia_RELEASE_REVISION`, `Skia_COMMIT`
(full SHA), `Skia_PREBUILT_PLATFORM`, `Skia_BUILD_PROFILE`, `Skia_GPU_BACKENDS`,
`Skia_HAS_PATHOPS`, `Skia_SANITIZER` and `Skia_HEADER_ABI`.

### ABI and sanitizer boundaries

These are optimized, uninstrumented libraries. Staged headers preserve their
container layout in ASan-instrumented applications. Compiler ASan checks in
consumer code remain enabled; bespoke Skia container poisoning is disabled.
The binary itself is not sanitizer-instrumented. Manually defining
`SK_SANITIZE_ADDRESS` is rejected.

Windows requires the MSVC ABI and release STL layout (`_ITERATOR_DEBUG_LEVEL=0`),
including in applications with debug symbols. MinGW is unsupported. Linux
requires the modern libstdc++ ABI without `_GLIBCXX_DEBUG`; macOS uses libc++.
Use toolchains compatible with the compiler/SDK recorded in the manifest.
Actual ASan consumers run on supported platforms. Windows ARM64 runs native
consumers and a simulated sanitizer-detection layout test because MSVC ASan
currently supports only x86/x64.

## Integrity and tests

The packager verifies clean sources and dependencies at their upstream DEPS
commits; it never resets user changes. A checkout lock serializes temporary
build patches, which are restored on success and failure. The ABI header patch
is applied only to the staged copy. Archives are completed in staging before
replacing local outputs.

Source, compiler, SDK, dependency and recipe identities isolate object caches.
A build is certified only after patch restoration and input verification.
`build-manifest.json` records source/dependency commits, tool versions, recipe
digest and binary hashes. Local archives have SHA-256 sidecars. Traceable inputs
do not imply byte-identical output across different toolchains or hosts.

Run regression tests through the Windows wrapper, or directly on Unix:

```sh
python -m unittest discover -s tests -v
```

`tools/verify_packages.py` checks archive structure, hashes, native architecture,
metadata and relocation. It compiles/runs consumers for PathOps, path building,
CPU raster, PNG round trips, Ganesh mock context and Windows ANGLE imports in
normal, sanitizer-detection and supported ASan modes. Mock contexts validate
Ganesh linkage without claiming hardware GPU validation on hosted runners.

Release CI publishes only after all six native jobs and twelve packages pass.
Assets include twelve archives, `SHA256SUMS` and a release manifest. See
[release maintenance](docs/releasing.md).

## Licenses

The [MIT license](LICENSE) covers this repository's build and packaging code.
Packages preserve Skia's original license, SkCMS attribution, and license/notice
files from bundled dependencies under their original terms.
