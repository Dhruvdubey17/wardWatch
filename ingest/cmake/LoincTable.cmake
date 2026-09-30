# Generates the LOINC table header from contracts/loinc_codes.json at configure
# time, so the simulator, the FHIR service and the ingest engine share one list.
set(loinc_json_path "${WARDWATCH_CONTRACTS_DIR}/loinc_codes.json")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${loinc_json_path}")
file(READ "${loinc_json_path}" loinc_json)
string(JSON loinc_count LENGTH "${loinc_json}" codes)
math(EXPR loinc_last "${loinc_count} - 1")

set(loinc_rows "")
foreach(index RANGE ${loinc_last})
  string(JSON code GET "${loinc_json}" codes ${index} code)
  string(JSON display GET "${loinc_json}" codes ${index} display)
  string(JSON ucum GET "${loinc_json}" codes ${index} ucum_unit)
  string(JSON low GET "${loinc_json}" codes ${index} plausible_min)
  string(JSON high GET "${loinc_json}" codes ${index} plausible_max)
  string(APPEND loinc_rows
    "    LoincCode{\"${code}\", \"${display}\", \"${ucum}\", ${low}, ${high}},\n")
endforeach()

set(WARDWATCH_GENERATED_DIR "${CMAKE_BINARY_DIR}/generated")
configure_file("${CMAKE_CURRENT_SOURCE_DIR}/cmake/loinc_table.hpp.in"
  "${WARDWATCH_GENERATED_DIR}/wardwatch/loinc_table.hpp" @ONLY)
