add_library(wardwatch_core
  ack.cpp
  encoding.cpp
  error.cpp
  loinc.cpp
  message.cpp
  mllp.cpp
  types.cpp
  validator.cpp)
target_include_directories(wardwatch_core
  PUBLIC ${PROJECT_SOURCE_DIR}/include ${WARDWATCH_GENERATED_DIR})
target_link_libraries(wardwatch_core
  PUBLIC nlohmann_json::nlohmann_json
  PRIVATE wardwatch_warnings)
