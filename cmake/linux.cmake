cmake_minimum_required(VERSION 3.16)
project(SkiaPrebuilt LANGUAGES C CXX)

# 1. 寻找 Linux 系统中必要的依赖库（Skia 的 Linux font manager 依赖 fontconfig）
find_package(PkgConfig REQUIRED)
pkg_check_modules(FONTCONFIG REQUIRED IMPORTED_TARGET fontconfig)

# 寻找 OpenGL 和 X11 (如果你的 Skia 开启了 GPU 和 窗口支持)
find_package(OpenGL REQUIRED)
find_package(X11 REQUIRED)

# 创建全局导入目标
add_library(skia SHARED IMPORTED GLOBAL)

# 设置动态库路径
set_target_properties(skia PROPERTIES 
    IMPORTED_LOCATION "${CMAKE_CURRENT_SOURCE_DIR}/lib/libskia.so"
)

# 暴露头文件和宏
target_include_directories(skia INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_definitions(skia INTERFACE SKIA_DLL)

# 将 Linux 系统依赖库绑定到接口上，传递给主项目
target_link_libraries(skia INTERFACE
    PkgConfig::FONTCONFIG
    OpenGL::GL
    ${X11_LIBRARIES}
    pthread
    dl
    m
)

# 创建带命名空间的别名
add_library(Skia::skia ALIAS skia)
