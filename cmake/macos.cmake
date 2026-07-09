cmake_minimum_required(VERSION 3.16)
project(SkiaPrebuilt LANGUAGES C CXX)

find_library(COCOA_FRAMEWORK Cocoa)
find_library(COREFOUNDATION_FRAMEWORK CoreFoundation)
find_library(COREGRAPHICS_FRAMEWORK CoreGraphics)
find_library(CORESERVICES_FRAMEWORK CoreServices)
find_library(CORETEXT_FRAMEWORK CoreText)
find_library(METAL_FRAMEWORK Metal)
find_library(METALKIT_FRAMEWORK MetalKit)

foreach(framework
    COCOA_FRAMEWORK
    COREFOUNDATION_FRAMEWORK
    COREGRAPHICS_FRAMEWORK
    CORESERVICES_FRAMEWORK
    CORETEXT_FRAMEWORK
    METAL_FRAMEWORK
    METALKIT_FRAMEWORK
)
    if("${${framework}}" STREQUAL "" OR "${${framework}}" MATCHES "-NOTFOUND$")
        message(FATAL_ERROR "Required macOS framework not found: ${framework}")
    endif()
endforeach()

# 创建全局导入目标
add_library(skia SHARED IMPORTED GLOBAL)

# 设置动态库路径
set_target_properties(skia PROPERTIES
    IMPORTED_LOCATION "${CMAKE_CURRENT_SOURCE_DIR}/lib/libskia.dylib"
)

# 暴露头文件和宏
target_include_directories(skia INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_definitions(skia INTERFACE SKIA_DLL)

# 将 macOS 系统 framework 绑定到接口上，传递给主项目
target_link_libraries(skia INTERFACE
    ${COCOA_FRAMEWORK}
    ${COREFOUNDATION_FRAMEWORK}
    ${COREGRAPHICS_FRAMEWORK}
    ${CORESERVICES_FRAMEWORK}
    ${CORETEXT_FRAMEWORK}
    ${METAL_FRAMEWORK}
    ${METALKIT_FRAMEWORK}
)

# 创建带命名空间的别名
add_library(Skia::skia ALIAS skia)
