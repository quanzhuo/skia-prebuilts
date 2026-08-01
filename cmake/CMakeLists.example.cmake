cmake_minimum_required(VERSION 3.21)
project(SkiaApp LANGUAGES CXX)

include(FetchContent)

set(SKIA_VERSION "m148-46f2e16555")
set(BASE_URL "https://gitee.com/quanzhuo/skia-prebuilts/releases/download/${SKIA_VERSION}")

if(WIN32)
    set(SKIA_PREBUILT_URL "${BASE_URL}/skia-${SKIA_VERSION}-win32-x64.zip")
elseif(APPLE)
    string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" ARCH)

    if(ARCH MATCHES "x86_64" OR ARCH MATCHES "amd64")
        set(SKIA_PREBUILT_URL "${BASE_URL}/skia-${SKIA_VERSION}-macos-x64.tar.gz")
    elseif(ARCH MATCHES "arm64" OR ARCH MATCHES "aarch64")
        set(SKIA_PREBUILT_URL "${BASE_URL}/skia-${SKIA_VERSION}-macos-arm64.tar.gz")
    else()
        message(FATAL_ERROR "Unsupported macOS architecture: ${CMAKE_SYSTEM_PROCESSOR}")
    endif()
elseif(UNIX AND NOT APPLE)
    string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" ARCH)

    if(ARCH MATCHES "x86_64" OR ARCH MATCHES "amd64")
        set(SKIA_PREBUILT_URL "${BASE_URL}/skia-${SKIA_VERSION}-linux-x64.tar.gz")
    elseif(ARCH MATCHES "aarch64" OR ARCH MATCHES "arm64")
        set(SKIA_PREBUILT_URL "${BASE_URL}/skia-${SKIA_VERSION}-linux-arm64.tar.gz")
    else()
        message(FATAL_ERROR "Unsupported Linux architecture: ${CMAKE_SYSTEM_PROCESSOR}")
    endif()
else()
    message(FATAL_ERROR "Unsupported platform: ${CMAKE_SYSTEM_NAME}")
endif()

FetchContent_Declare(
    skia_prebuilt
    URL "${SKIA_PREBUILT_URL}"
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    SOURCE_SUBDIR "__skia_prebuilt_no_add_subdirectory"
)
FetchContent_MakeAvailable(skia_prebuilt)

find_package(Skia CONFIG REQUIRED
    PATHS "${skia_prebuilt_SOURCE_DIR}"
    NO_DEFAULT_PATH
)

add_executable(my_skia_app main.cpp)
target_link_libraries(my_skia_app PRIVATE Skia::Skia)

if(WIN32)
    add_custom_command(TARGET my_skia_app POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_RUNTIME_DLLS:my_skia_app>
            $<TARGET_FILE_DIR:my_skia_app>
        COMMAND_EXPAND_LISTS
    )
endif()
