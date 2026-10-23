cmake_minimum_required(VERSION 3.24)
project(SkiaApp LANGUAGES CXX)

include(FetchContent)

set(SKIA_VERSION "m152-0873ec164a-r2" CACHE STRING "Skia release tag")
set(SKIA_PROFILE "gpu" CACHE STRING "Skia prebuilt profile: gpu or cpu")
set_property(CACHE SKIA_PROFILE PROPERTY STRINGS gpu cpu)
if(SKIA_PROFILE STREQUAL "gpu")
    set(SKIA_ARTIFACT_VERSION "${SKIA_VERSION}-gpu")
elseif(SKIA_PROFILE STREQUAL "cpu")
    set(SKIA_ARTIFACT_VERSION "${SKIA_VERSION}-cpu")
else()
    message(FATAL_ERROR "Unsupported Skia profile: ${SKIA_PROFILE}")
endif()
# Replace this URL/version with assets you have actually published.
set(BASE_URL "https://github.com/quanzhuo/skia-prebuilts/releases/download/${SKIA_VERSION}")

set(_skia_processor "${CMAKE_SYSTEM_PROCESSOR}")
if(APPLE AND CMAKE_OSX_ARCHITECTURES)
    list(LENGTH CMAKE_OSX_ARCHITECTURES _skia_arch_count)
    if(NOT _skia_arch_count EQUAL 1)
        message(FATAL_ERROR "Select one architecture; these packages are not Universal 2")
    endif()
    set(_skia_processor "${CMAKE_OSX_ARCHITECTURES}")
endif()
string(TOLOWER "${_skia_processor}" _skia_processor)
if(_skia_processor MATCHES "^(x86_64|amd64|x64)$")
    set(SKIA_ARCH x64)
elseif(_skia_processor MATCHES "^(arm64|aarch64)$")
    set(SKIA_ARCH arm64)
else()
    message(FATAL_ERROR "Unsupported architecture: ${_skia_processor}")
endif()

set(SKIA_ARCHIVE_SUFFIX tar.gz)
if(WIN32)
    set(SKIA_PLATFORM windows)
    set(SKIA_ARCHIVE_SUFFIX zip)
elseif(APPLE)
    set(SKIA_PLATFORM macos)
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    set(SKIA_PLATFORM linux)
else()
    message(FATAL_ERROR "Unsupported platform: ${CMAKE_SYSTEM_NAME}")
endif()
set(SKIA_PREBUILT_URL
    "${BASE_URL}/skia-${SKIA_ARTIFACT_VERSION}-${SKIA_PLATFORM}-${SKIA_ARCH}.${SKIA_ARCHIVE_SUFFIX}")

FetchContent_Declare(
    skia_prebuilt
    URL "${SKIA_PREBUILT_URL}"
    DOWNLOAD_EXTRACT_TIMESTAMP FALSE
    SOURCE_SUBDIR "__skia_prebuilt_no_add_subdirectory"
)
FetchContent_MakeAvailable(skia_prebuilt)

find_package(Skia CONFIG REQUIRED
    PATHS "${skia_prebuilt_SOURCE_DIR}"
    NO_DEFAULT_PATH
)

if(NOT Skia_BUILD_PROFILE STREQUAL SKIA_PROFILE)
    message(FATAL_ERROR "The imported Skia package does not match SKIA_PROFILE")
endif()

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
