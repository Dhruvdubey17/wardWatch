#include "wardwatch/validator.hpp"

#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <gtest/gtest.h>

#include "wardwatch/mllp.hpp"

#include "contracts.hpp"
#include "faults.hpp"

namespace wardwatch {
namespace {

using testing::erase_segment;
using testing::read_contract;
using testing::replace_first;

std::vector<char> bytes(std::string_view text) { return {text.begin(), text.end()}; }

std::string oru() { return read_contract("hl7/oru_r01.hl7"); }

ProcessedMessage run(std::string_view text) {
    Validator validator;
    return validator.process(bytes(text));
}

bool has_warning(const ValidationResult& result, WarningCode code) {
    for (const auto& warning : result.warnings) {
        if (warning.code == code) {
            return true;
        }
    }
    return false;
}

TEST(Validator, AcceptsContractSamples) {
    for (const std::string_view name : {"hl7/oru_r01.hl7", "hl7/adt_a01.hl7", "hl7/adt_a03.hl7"}) {
        const auto processed = run(read_contract(name));
        EXPECT_EQ(processed.result.disposition, Disposition::accepted) << name;
        EXPECT_EQ(processed.result.ack_code, AckCode::aa) << name;
        EXPECT_FALSE(processed.result.error.has_value())
            << name << ": " << processed.result.error->detail;
        EXPECT_TRUE(processed.result.warnings.empty()) << name;
        EXPECT_EQ(processed.result.summary.mrn, "MRN0001234");
        ASSERT_TRUE(processed.message.has_value());
    }
}

TEST(Validator, SummaryCarriesHeaderFields) {
    const auto processed = run(oru());
    const auto& summary = processed.result.summary;
    EXPECT_EQ(summary.sending_application, "WWSIM");
    EXPECT_EQ(summary.sending_facility, "WARDWATCH_ICU");
    EXPECT_EQ(summary.receiving_application, "WARDWATCH");
    EXPECT_EQ(summary.receiving_facility, "WARDWATCH");
    EXPECT_EQ(summary.control_id, "SIM000007");
    EXPECT_EQ(summary.message_type, "ORU");
    EXPECT_EQ(summary.trigger_event, "R01");
    EXPECT_EQ(summary.processing_id, "P");
    EXPECT_EQ(summary.version, "2.5.1");
    ASSERT_TRUE(summary.message_time.has_value());
    EXPECT_EQ(summary.message_time->to_iso8601(), "2024-03-15T13:00:05+00:00");
}

struct RuleCase {
    std::string_view name;
    std::string message;
    ErrorCode code;
    AckCode ack;
    // Where the error must point, when the rule can fire in several fields.
    std::string_view detail_prefix = "";
};

class ErrorRule : public ::testing::TestWithParam<RuleCase> {};

TEST_P(ErrorRule, RejectsWithCodeAndAck) {
    const auto& param = GetParam();
    ASSERT_NE(param.message, oru()) << "the case did not change the sample";
    const auto processed = run(param.message);
    EXPECT_EQ(processed.result.disposition, Disposition::rejected);
    ASSERT_TRUE(processed.result.error.has_value());
    EXPECT_EQ(processed.result.error->code, param.code) << processed.result.error->detail;
    EXPECT_EQ(processed.result.ack_code, param.ack);
    EXPECT_FALSE(processed.result.error->detail.empty());
    EXPECT_TRUE(processed.result.error->detail.starts_with(param.detail_prefix))
        << processed.result.error->detail;
    EXPECT_EQ(processed.raw(), param.message);
}

std::vector<RuleCase> error_rules() {
    const std::string message = oru();
    const std::string adt = read_contract("hl7/adt_a01.hl7");
    return {
        {"unsupported_type", replace_first(message, "ORU^R01^ORU_R01", "ORM^O01^ORM_O01"),
         ErrorCode::message_type_unsupported, AckCode::ar},
        {"unsupported_trigger", replace_first(adt, "ADT^A01^ADT_A01", "ADT^A08^ADT_A01"),
         ErrorCode::message_type_unsupported, AckCode::ar},
        {"empty_message_type", replace_first(message, "ORU^R01^ORU_R01", ""),
         ErrorCode::message_type_unsupported, AckCode::ar},
        {"control_id_missing", replace_first(message, "|SIM000007|", "||"),
         ErrorCode::control_id_missing, AckCode::ae},
        {"msh7_empty", replace_first(message, "|20240315130005+0000|", "||"),
         ErrorCode::timestamp_invalid, AckCode::ae},
        {"msh7_invalid", replace_first(message, "|20240315130005+0000|", "|20240230130005|"),
         ErrorCode::timestamp_invalid, AckCode::ae, "MSH[1]-7"},
        {"obx14_invalid",
         replace_first(message, "F|||20240315130000+0000\rOBX|2", "F|||2024031525\rOBX|2"),
         ErrorCode::timestamp_invalid, AckCode::ae, "OBX[1]-14"},
        {"obr7_invalid", replace_first(message, "L|||20240315130000+0000", "L|||yesterday"),
         ErrorCode::timestamp_invalid, AckCode::ae, "OBR[1]-7"},
        {"pid7_invalid", replace_first(message, "||19580214|", "||19581314|"),
         ErrorCode::timestamp_invalid, AckCode::ae, "PID[1]-7"},
        {"evn2_invalid", replace_first(adt, "EVN|A01|20240315083000+0000", "EVN|A01|2024031508300"),
         ErrorCode::timestamp_invalid, AckCode::ae, "EVN[1]-2"},
        {"pv1_44_invalid", replace_first(adt, "||20240315083000+0000\r", "||20240315083099+0000\r"),
         ErrorCode::timestamp_invalid, AckCode::ae, "PV1[1]-44"},
        {"missing_pv1", erase_segment(message, "PV1"), ErrorCode::required_segment_missing,
         AckCode::ae},
        {"missing_obr", erase_segment(message, "OBR"), ErrorCode::required_segment_missing,
         AckCode::ae},
        {"missing_evn", erase_segment(adt, "EVN"), ErrorCode::required_segment_missing,
         AckCode::ae},
        {"no_obx", message.substr(0, message.find("\rOBX|") + 1),
         ErrorCode::required_segment_missing, AckCode::ae},
        {"pid3_empty", replace_first(message, "PID|1||MRN0001234^^^WARDWATCH^MR|", "PID|1|||"),
         ErrorCode::patient_identifier_missing, AckCode::ae},
        {"pid3_no_id",
         replace_first(message, "PID|1||MRN0001234^^^WARDWATCH^MR|", "PID|1||^^^WW^MR|"),
         ErrorCode::patient_identifier_missing, AckCode::ae},
        {"nm_not_numeric", replace_first(message, "LN||112|", "LN||1l2|"),
         ErrorCode::value_type_mismatch, AckCode::ae, "OBX[1]-5"},
        {"nm_second_repetition_bad", replace_first(message, "LN||112|", "LN||112~x|"),
         ErrorCode::value_type_mismatch, AckCode::ae},
        {"value_without_type", replace_first(message, "OBX|1|NM|", "OBX|1||"),
         ErrorCode::value_type_mismatch, AckCode::ae},
        {"unsupported_value_type", replace_first(message, "OBX|1|NM|", "OBX|1|XYZ|"),
         ErrorCode::value_type_mismatch, AckCode::ae},
        {"coded_value_empty",
         replace_first(message, "OBX|1|NM|8867-4^Heart rate^LN||112|",
                       "OBX|1|CWE|8867-4^Heart rate^LN||^^LN|"),
         ErrorCode::value_type_mismatch, AckCode::ae},
        {"dtm_value_bad",
         replace_first(message, "OBX|1|NM|8867-4^Heart rate^LN||112|",
                       "OBX|1|DTM|8867-4^Heart rate^LN||2024-01-01|"),
         ErrorCode::value_type_mismatch, AckCode::ae},
        {"result_status_empty", replace_first(message, "|||||F|||", "||||||||"),
         ErrorCode::result_status_invalid, AckCode::ae},
        {"result_status_unknown", replace_first(message, "|||||F|||", "|||||Q|||"),
         ErrorCode::result_status_invalid, AckCode::ae, "OBX[1]-11"},
        {"result_status_two_letters", replace_first(message, "|||||F|||", "|||||FF|||"),
         ErrorCode::result_status_invalid, AckCode::ae},
        {"obx3_empty", replace_first(message, "OBX|1|NM|8867-4^Heart rate^LN|", "OBX|1|NM||"),
         ErrorCode::field_value_invalid, AckCode::ae, "OBX[1]-3"},
        {"bad_escape_in_msh", replace_first(message, "WWSIM", "WW\\Q\\SIM"),
         ErrorCode::escape_sequence_invalid, AckCode::ae},
        {"bad_escape_in_mrn", replace_first(message, "MRN0001234^", "MRN\\X4\\1234^"),
         ErrorCode::escape_sequence_invalid, AckCode::ae},
        {"not_msh", "PID|1\r", ErrorCode::msh_missing, AckCode::ar},
        {"bad_segment_id", replace_first(message, "\rPV1|", "\rpv1|"),
         ErrorCode::segment_id_invalid, AckCode::ar},
    };
}

INSTANTIATE_TEST_SUITE_P(Table, ErrorRule, ::testing::ValuesIn(error_rules()),
                         [](const auto& info) { return std::string(info.param.name); });

struct WarningCase {
    std::string_view name;
    std::string message;
    WarningCode code;
    std::string_view location;
};

class WarningRule : public ::testing::TestWithParam<WarningCase> {};

TEST_P(WarningRule, AcceptsWithWarning) {
    const auto& param = GetParam();
    const auto processed = run(param.message);
    ASSERT_FALSE(processed.result.error.has_value()) << processed.result.error->detail;
    EXPECT_EQ(processed.result.disposition, Disposition::accepted);
    EXPECT_EQ(processed.result.ack_code, AckCode::aa);
    ASSERT_TRUE(has_warning(processed.result, param.code));
    for (const auto& warning : processed.result.warnings) {
        if (warning.code == param.code) {
            EXPECT_EQ(warning.location, param.location);
        }
    }
}

std::vector<WarningCase> warning_rules() {
    const std::string message = oru();
    return {
        {"unknown_loinc", replace_first(message, "8867-4^Heart rate^LN", "9999-9^Something^LN"),
         WarningCode::observation_code_unknown, "OBX[1]-3"},
        {"local_code_system", replace_first(message, "8867-4^Heart rate^LN", "HR^Heart rate^L"),
         WarningCode::observation_code_unknown, "OBX[1]-3"},
        {"implausible_high", replace_first(message, "LN||112|", "LN||450|"),
         WarningCode::value_implausible, "OBX[1]-5"},
        {"implausible_negative", replace_first(message, "LN||24|", "LN||-3|"),
         WarningCode::value_implausible, "OBX[2]-5"},
        {"lf_terminators", replace_first(message, "\rPID|", "\nPID|"),
         WarningCode::segment_terminator_not_cr, ""},
        {"trailing_separator", replace_first(message, "\rPID|", "|\rPID|"),
         WarningCode::trailing_separator, "MSH[1]"},
        {"non_ascii", replace_first(message, "Lindgren", "Lindgr\xC3\xA9n"),
         WarningCode::non_ascii_bytes, ""},
    };
}

INSTANTIATE_TEST_SUITE_P(Table, WarningRule, ::testing::ValuesIn(warning_rules()),
                         [](const auto& info) { return std::string(info.param.name); });

TEST(Validator, PlausibleBoundariesDoNotWarn) {
    // Heart rate range is 0 to 300 in contracts/loinc_codes.json.
    for (const std::string_view value : {"0", "300"}) {
        const auto processed =
            run(replace_first(oru(), "LN||112|", "LN||" + std::string(value) + "|"));
        EXPECT_FALSE(has_warning(processed.result, WarningCode::value_implausible)) << value;
    }
    const auto processed = run(replace_first(oru(), "LN||112|", "LN||300.1|"));
    EXPECT_TRUE(has_warning(processed.result, WarningCode::value_implausible));
}

TEST(Validator, TextualAndCodedValuesAreAccepted) {
    std::string message = replace_first(oru(), "OBX|1|NM|8867-4^Heart rate^LN||112|",
                                        "OBX|1|ST|8867-4^Heart rate^LN||irregular|");
    message = replace_first(message, "OBX|2|NM|9279-1^Respiratory rate^LN||24|",
                            "OBX|2|CWE|9279-1^Respiratory rate^LN||N^Normal^HL70078|");
    message = replace_first(message, "OBX|3|NM|", "OBX|3|DTM|");
    message = replace_first(message, "LN||93|", "LN||20240315|");
    const auto processed = run(message);
    EXPECT_EQ(processed.result.disposition, Disposition::accepted)
        << processed.result.error->detail;
}

TEST(Validator, EmptyObservationValueIsAccepted) {
    const auto processed = run(replace_first(oru(), "LN||112|", "LN|||"));
    EXPECT_EQ(processed.result.disposition, Disposition::accepted);
}

TEST(Validator, PrefersMrIdentifierInPid3) {
    const auto message = replace_first(oru(), "PID|1||MRN0001234^^^WARDWATCH^MR|",
                                       "PID|1||V123^^^WARDWATCH^VN~MRN0001234^^^WARDWATCH^MR|");
    EXPECT_EQ(run(message).result.summary.mrn, "MRN0001234");
    const auto without_type =
        replace_first(oru(), "PID|1||MRN0001234^^^WARDWATCH^MR|", "PID|1||X9^^^A|");
    EXPECT_EQ(run(without_type).result.summary.mrn, "X9");
}

TEST(Validator, RetransmissionIsAcknowledgedButNotRepublished) {
    Validator validator;
    EXPECT_EQ(validator.process(bytes(oru())).result.disposition, Disposition::accepted);
    const auto again = validator.process(bytes(oru()));
    EXPECT_EQ(again.result.disposition, Disposition::retransmission);
    EXPECT_EQ(again.result.ack_code, AckCode::aa);
    EXPECT_FALSE(again.result.error.has_value());
}

TEST(Validator, RejectedMessageDoesNotReserveItsControlId) {
    Validator validator;
    const auto bad = replace_first(oru(), "LN||112|", "LN||x|");
    EXPECT_EQ(validator.process(bytes(bad)).result.disposition, Disposition::rejected);
    EXPECT_EQ(validator.process(bytes(oru())).result.disposition, Disposition::accepted);
}

TEST(ControlIdRegistry, EvictsOldestBeyondCapacity) {
    ControlIdRegistry registry(2);
    registry.remember("a", 1);
    registry.remember("b", 2);
    registry.remember("a", 9);
    EXPECT_EQ(registry.check("a", 1), ControlIdRegistry::Seen::same_content);
    registry.remember("c", 3);
    EXPECT_EQ(registry.size(), 2U);
    EXPECT_EQ(registry.check("a", 1), ControlIdRegistry::Seen::new_id);
    EXPECT_EQ(registry.check("b", 2), ControlIdRegistry::Seen::same_content);
    EXPECT_EQ(registry.check("b", 3), ControlIdRegistry::Seen::different_content);
    ControlIdRegistry disabled(0);
    disabled.remember("a", 1);
    EXPECT_EQ(disabled.check("a", 1), ControlIdRegistry::Seen::new_id);
}

TEST(SniffHeader, ReadsFieldsFromUnparseableMessage) {
    const auto summary =
        sniff_header("MSH|^^\\&|APP|FAC|RECV|RFAC|20240101||ORU^R01|CTRL42|P|2.5.1\rPID|1\r");
    EXPECT_EQ(summary.control_id, "CTRL42");
    EXPECT_EQ(summary.sending_application, "APP");
    EXPECT_EQ(summary.receiving_facility, "RFAC");
    EXPECT_EQ(summary.message_type, "ORU");
    EXPECT_EQ(summary.trigger_event, "R01");
    EXPECT_EQ(summary.version, "2.5.1");
    EXPECT_TRUE(sniff_header("garbage").control_id.empty());
    EXPECT_TRUE(sniff_header("").control_id.empty());
    EXPECT_TRUE(sniff_header("MSH").control_id.empty());
}

TEST(ContentHash, IsStableAndSensitive) {
    EXPECT_EQ(content_hash(""), 14695981039346656037ULL);
    EXPECT_EQ(content_hash("a"), 0xaf63dc4c8601ec8cULL);
    EXPECT_NE(content_hash("ab"), content_hash("ba"));
}

TEST(AckCodes, Spelling) {
    EXPECT_EQ(to_string(AckCode::aa), "AA");
    EXPECT_EQ(to_string(AckCode::ae), "AE");
    EXPECT_EQ(to_string(AckCode::ar), "AR");
}

// Every catalogued fault must produce the outcome the catalog promises.
TEST(FaultCatalog, EveryFaultProducesItsCataloguedOutcome) {
    const auto catalog = testing::read_contract_json("fault_catalog.json");
    const auto& transforms = testing::fault_transforms();
    const std::string message = oru();
    for (const auto& fault : catalog.at("faults")) {
        const auto name = fault.at("name").get<std::string>();
        const auto code = fault.at("code").get<std::string>();
        const auto ack = fault.at("ack_code").get<std::string>();
        const bool is_error = fault.at("outcome") == "error";
        SCOPED_TRACE(name);

        if (name == "oversized_frame") {
            MllpFramer framer(64);
            std::optional<Frame> frame;
            const auto framed = mllp_wrap(std::span<const char>(message.data(), message.size()));
            framer.feed(framed, [&](Frame&& completed) { frame = std::move(completed); });
            ASSERT_TRUE(frame.has_value());
            EXPECT_TRUE(frame->oversize);
            EXPECT_EQ(to_string(ErrorCode::frame_oversize), code);
            EXPECT_EQ(to_string(ack_code_for(ErrorCode::frame_oversize)), ack);
            continue;
        }

        Validator validator;
        std::string faulty;
        if (name == "duplicate_control_id") {
            ASSERT_EQ(validator.process(bytes(message)).result.disposition, Disposition::accepted);
            faulty = replace_first(message, "LN||112|", "LN||113|");
        } else {
            const auto transform = transforms.find(name);
            ASSERT_NE(transform, transforms.end()) << "no test transform for fault " << name;
            faulty = transform->second(message);
        }
        const auto processed = validator.process(bytes(faulty));
        EXPECT_EQ(to_string(processed.result.ack_code), ack);
        if (is_error) {
            ASSERT_TRUE(processed.result.error.has_value());
            EXPECT_EQ(to_string(processed.result.error->code), code)
                << processed.result.error->detail;
            EXPECT_EQ(processed.result.disposition, Disposition::rejected);
        } else {
            ASSERT_FALSE(processed.result.error.has_value()) << processed.result.error->detail;
            bool found = false;
            for (const auto& warning : processed.result.warnings) {
                found = found || to_string(warning.code) == code;
            }
            EXPECT_TRUE(found);
        }
    }
}

}  // namespace
}  // namespace wardwatch
