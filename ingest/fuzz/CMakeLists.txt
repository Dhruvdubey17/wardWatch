include(CheckCXXSourceCompiles)

# Linux clang ships the libFuzzer runtime. Apple clang does not, so there the
# runtime is compiled from the matching compiler-rt release sources and the
# targets use -fsanitize=fuzzer-no-link for instrumentation only.
set(CMAKE_REQUIRED_LINK_OPTIONS -fsanitize=fuzzer)
check_cxx_source_compiles(
  "#include <cstddef>
   #include <cstdint>
   extern \"C\" int LLVMFuzzerTestOneInput(const std::uint8_t*, std::size_t) { return 0; }"
  WARDWATCH_HAVE_LIBFUZZER_RUNTIME)
unset(CMAKE_REQUIRED_LINK_OPTIONS)

if(WARDWATCH_HAVE_LIBFUZZER_RUNTIME)
  set(fuzz_compile_flag -fsanitize=fuzzer)
  set(fuzz_link_libraries "")
  add_link_options(-fsanitize=fuzzer)
else()
  FetchContent_Declare(
    compiler_rt
    URL https://github.com/llvm/llvm-project/releases/download/llvmorg-19.1.7/compiler-rt-19.1.7.src.tar.xz
    URL_HASH SHA256=c12b6e764202c615c1a3af9a13d477846878757ae0e29e5f8979215a6958fffc
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    # Only the sources are needed; pointing at a directory with no
    # CMakeLists.txt stops FetchContent from configuring all of compiler-rt.
    SOURCE_SUBDIR lib/fuzzer/none)
  FetchContent_MakeAvailable(compiler_rt)
  file(GLOB libfuzzer_sources "${compiler_rt_SOURCE_DIR}/lib/fuzzer/Fuzzer*.cpp")
  add_library(wardwatch_libfuzzer STATIC ${libfuzzer_sources})
  # The runtime itself must not be instrumented, or it would fuzz itself.
  target_compile_options(wardwatch_libfuzzer PRIVATE -fno-sanitize=all -w -O2)
  set_target_properties(wardwatch_libfuzzer PROPERTIES CXX_STANDARD 17)
  set(fuzz_compile_flag -fsanitize=fuzzer-no-link)
  set(fuzz_link_libraries wardwatch_libfuzzer)
endif()

function(wardwatch_add_fuzzer name source)
  add_executable(${name} ${source})
  target_compile_options(${name} PRIVATE ${fuzz_compile_flag})
  target_link_libraries(${name} PRIVATE wardwatch_core wardwatch_warnings ${fuzz_link_libraries})
  target_include_directories(${name} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
endfunction()

file(GLOB fuzz_sources CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/*_fuzz.cpp)
foreach(source ${fuzz_sources})
  get_filename_component(name ${source} NAME_WE)
  wardwatch_add_fuzzer(${name} ${source})
endforeach()
