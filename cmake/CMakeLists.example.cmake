cmake_minimum_required(VERSION 3.16)
project(SkiaApp LANGUAGES CXX)

include(FetchContent)

set(SKIA_VERSION "m148-46f2e16555")
set(BASE_URL "https://gitee.com/quanzhuo/skia-prebuilts/releases/download/${SKIA_VERSION}")

if(WIN32)
    # Windows 暂时默认 x64
    set(SKIA_PREBUILT_URL "${BASE_URL}/skia-prebuilt-windows-x64.zip")
    set(SKIA_HASH "Windows_x64_SHA256_哈希值")

elseif(UNIX AND NOT APPLE)
    # Linux 平台：开始细分 CPU 架构
    # CMAKE_SYSTEM_PROCESSOR 在 Linux 下常见的值有 x86_64, amd64, aarch64, arm64
    string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" ARCH)
    
    if(ARCH MATCHES "x86_64" OR ARCH MATCHES "amd64")
        message(STATUS "Detected Linux x64 architecture")
        set(SKIA_PREBUILT_URL "${BASE_URL}/skia-prebuilt-linux-x64.zip")
        set(SKIA_HASH "Linux_x64_SHA256_哈希值")
        
    elseif(ARCH MATCHES "aarch64" OR ARCH MATCHES "arm64")
        message(STATUS "Detected Linux ARM64 architecture")
        set(SKIA_PREBUILT_URL "${BASE_URL}/skia-prebuilt-linux-arm64.zip")
        set(SKIA_HASH "Linux_ARM64_SHA256_哈希值")
        
    else()
        message(FATAL_ERROR "Unsupported Linux architecture: ${CMAKE_SYSTEM_PROCESSOR}")
    endif()
endif()

# =====================================================================
# 2. 拉取并激活预编译包
# =====================================================================
FetchContent_Declare(
    skia_prebuilt
    URL "${SKIA_PREBUILT_URL}"
    URL_HASH SHA256=${SKIA_HASH}
    DOWNLOAD_EXTRACT_TIMESTAMP true
)
FetchContent_MakeAvailable(skia_prebuilt)

# =====================================================================
# 3. 业务构建与链接
# =====================================================================
add_executable(my_skia_app main.cpp)
target_link_libraries(my_skia_app PRIVATE Skia::skia)

# 运行时动态库处理（保持不变）
if(WIN32)
    add_custom_command(TARGET my_skia_app POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<TARGET_FILE:skia>"
        "$<TARGET_FILE_DIR:my_skia_app>"
    )
elseif(UNIX AND NOT APPLE)
    set_target_properties(my_skia_app PROPERTIES
        BUILD_RPATH "${skia_prebuilt_SOURCE_DIR}/lib"
    )
endif()