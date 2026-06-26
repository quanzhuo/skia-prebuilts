cmake_minimum_required(VERSION 3.10)
project(SkiaPrebuilt LANGUAGES C CXX)

add_library(skia SHARED IMPORTED GLOBAL)

set_target_properties(skia PROPERTIES IMPORTED_LOCATION "${CMAKE_CURRENT_SOURCE_DIR}/lib/libskia.so")

target_include_directories(skia INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_definitions(skia INTERFACE SKIA_DLL)

add_library(Skia::skia ALIAS skia)