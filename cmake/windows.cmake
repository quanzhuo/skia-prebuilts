cmake_minimum_required(VERSION 3.16)
project(SkiaPrebuilt)

# 创建全局导入目标
add_library(skia SHARED IMPORTED GLOBAL)

# 设置导入库(链接时用的 .lib) 和动态库 （运行时用的 .dll）的路径
set_target_properties(skia PROPERTIES
    IMPORTED_IMPLIB "${CMAKE_CURRENT_SOURCE_DIR}/lib/skia.dll.lib"
    IMPORTED_LOCATION "${CMAKE_CURRENT_SOURCE_DIR}/bin/skia.dll"
)

# 暴露头文件目录，让链接这个目标的程序能自动找到头文件
target_include_directories(skia INTERFACE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
)

# 定义 SKIA_DLL 宏,这样 Skia 头文件里的 SK_API 才会展开为 __declspec(dllimport)
target_compile_definitions(skia INTERFACE SKIA_DLL)

# 创建带命名空间的别名
add_library(Skia::Skia ALIAS skia)
