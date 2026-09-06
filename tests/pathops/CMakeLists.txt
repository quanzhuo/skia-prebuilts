cmake_minimum_required(VERSION 3.21)
project(SkiaPathOpsSmoke LANGUAGES CXX)

find_package(Skia CONFIG REQUIRED)

if(DEFINED SKIA_EXPECT_PROFILE)
    if(NOT Skia_BUILD_PROFILE STREQUAL SKIA_EXPECT_PROFILE OR NOT Skia_HAS_PATHOPS)
        message(FATAL_ERROR "The package profile or PathOps metadata is incorrect")
    endif()
    get_target_property(_skia_definitions Skia::Skia INTERFACE_COMPILE_DEFINITIONS)
    if(SKIA_EXPECT_PROFILE STREQUAL "cpu-only")
        if(Skia_GPU_BACKENDS OR TARGET Skia::Angle OR "SK_GANESH" IN_LIST _skia_definitions)
            message(FATAL_ERROR "CPU-only package exposes GPU capabilities")
        endif()
    elseif(SKIA_EXPECT_PROFILE STREQUAL "full")
        if(NOT Skia_GPU_BACKENDS OR NOT "SK_GANESH" IN_LIST _skia_definitions)
            message(FATAL_ERROR "Full package does not expose its GPU capabilities")
        endif()
        if(WIN32 AND NOT TARGET Skia::Angle)
            message(FATAL_ERROR "Windows full package does not export ANGLE")
        endif()
    else()
        message(FATAL_ERROR "Unknown expected profile: ${SKIA_EXPECT_PROFILE}")
    endif()
endif()
add_executable(skia_pathops_smoke main.cpp)
# The imported target must propagate its required C++ language version.
target_link_libraries(skia_pathops_smoke PRIVATE Skia::Skia)

if(WIN32)
    add_custom_command(TARGET skia_pathops_smoke POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "$<TARGET_FILE:Skia::Skia>" "$<TARGET_FILE_DIR:skia_pathops_smoke>"
        VERBATIM)
endif()

enable_testing()
add_test(NAME skia_pathops_smoke COMMAND skia_pathops_smoke)
