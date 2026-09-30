add_library(wardwatch_warnings INTERFACE)
target_compile_options(wardwatch_warnings INTERFACE
  -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion -Werror)

if(WARDWATCH_SANITIZERS)
  list(JOIN WARDWATCH_SANITIZERS "," sanitizer_list)
  add_compile_options(-fsanitize=${sanitizer_list} -fno-omit-frame-pointer -fno-sanitize-recover=all)
  add_link_options(-fsanitize=${sanitizer_list})
endif()

if(WARDWATCH_COVERAGE)
  if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    message(FATAL_ERROR "The coverage preset uses clang source-based coverage")
  endif()
  add_compile_options(-fprofile-instr-generate -fcoverage-mapping)
  add_link_options(-fprofile-instr-generate)
endif()
