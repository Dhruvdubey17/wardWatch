#include "wardwatch/validator.hpp"

#include <algorithm>
#include <array>
#include <expected>
#include <format>
#include <utility>

#include "wardwatch/loinc.hpp"

namespace wardwatch {
namespace {

struct RequiredSegments {
    std::string_view message_type;
    std::string_view trigger_event;
    std::array<std::string_view, 5> segments;
};

// ORU^R01 needs PV1 here although the standard makes it optional, because
// every observation must be tied to an encounter downstream.
constexpr std::array kSupportedMessages{
    RequiredSegments{"ADT", "A01", {"MSH", "EVN", "PID", "PV1", ""}},
    RequiredSegments{"ADT", "A03", {"MSH", "EVN", "PID", "PV1", ""}},
    RequiredSegments{"ORU", "R01", {"MSH", "PID", "PV1", "OBR", "OBX"}},
};

// HL7 v2.5.1 table 0085, observation result status.
constexpr std::string_view kResultStatuses = "CDFINOPRSUWXA";

// Value types this pipeline accepts in OBX-2 (HL7 v2.5.1 table 0125 subset).
constexpr std::array<std::string_view, 8> kTextualValueTypes{"ST", "TX", "FT", "SN",
                                                             "ID", "IS", "ED", "RP"};

constexpr std::uint64_t kFnvOffset = 14695981039346656037ULL;
constexpr std::uint64_t kFnvPrime = 1099511628211ULL;

std::string field_location(const SegmentView& segment, std::size_t field) {
    return std::format("{}[{}]-{}", segment.id, segment.occurrence, field);
}

class Checker {
  public:
    explicit Checker(const Message& message) : message_(&message) {}

    [[nodiscard]] ParseError error(ErrorCode code, std::string_view field_view,
                                   std::string detail) const {
        const std::size_t offset =
            field_view.data() == nullptr ? 0 : message_->offset_of(field_view);
        return ParseError{code, offset, std::move(detail)};
    }

    [[nodiscard]] std::expected<std::string, ParseError> decoded(std::string_view raw) const {
        const std::size_t offset = raw.data() == nullptr ? 0 : message_->offset_of(raw);
        return decode_escapes(raw, message_->encoding(), offset);
    }

    [[nodiscard]] std::expected<std::string, ParseError> component(const SegmentView& segment,
                                                                   std::size_t field,
                                                                   std::size_t number) const {
        return decoded(subcomponent(segment.field(field), message_->encoding(), 1, number));
    }

    [[nodiscard]] std::expected<std::optional<DateTime>, ParseError> optional_timestamp(
        const SegmentView& segment, std::size_t field) const {
        const std::string_view raw =
            nth_piece(segment.field(field), message_->encoding().component, 1);
        if (raw.empty()) {
            return std::optional<DateTime>{};
        }
        auto parsed = parse_dtm(raw, message_->offset_of(raw));
        if (!parsed) {
            auto failure = std::move(parsed.error());
            failure.detail = std::format("{}: {}", field_location(segment, field), failure.detail);
            return std::unexpected(std::move(failure));
        }
        return std::optional<DateTime>{*parsed};
    }

    [[nodiscard]] const Message& message() const noexcept { return *message_; }

  private:
    const Message* message_;
};

std::expected<void, ParseError> read_header(const Checker& check, MessageSummary& summary) {
    const SegmentView& msh = *check.message().find("MSH");
    struct Slot {
        std::size_t field;
        std::size_t component;
        std::string MessageSummary::* member;
    };
    static constexpr std::array<Slot, 9> kSlots{{
        {3, 1, &MessageSummary::sending_application},
        {4, 1, &MessageSummary::sending_facility},
        {5, 1, &MessageSummary::receiving_application},
        {6, 1, &MessageSummary::receiving_facility},
        {9, 1, &MessageSummary::message_type},
        {9, 2, &MessageSummary::trigger_event},
        {10, 1, &MessageSummary::control_id},
        {11, 1, &MessageSummary::processing_id},
        {12, 1, &MessageSummary::version},
    }};
    for (const auto& slot : kSlots) {
        auto value = check.component(msh, slot.field, slot.component);
        if (!value) {
            return std::unexpected(std::move(value.error()));
        }
        summary.*slot.member = std::move(*value);
    }
    return {};
}

const RequiredSegments* find_supported(const MessageSummary& summary) {
    const auto* found = std::ranges::find_if(kSupportedMessages, [&](const auto& supported) {
        return supported.message_type == summary.message_type &&
               supported.trigger_event == summary.trigger_event;
    });
    return found == kSupportedMessages.end() ? nullptr : found;
}

std::expected<std::string, ParseError> read_mrn(const Checker& check, const SegmentView& pid) {
    const std::string_view identifiers = pid.field(3);
    const auto& encoding = check.message().encoding();
    const std::size_t repetitions = piece_count(identifiers, encoding.repetition);
    std::string_view chosen = nth_piece(identifiers, encoding.repetition, 1);
    // Prefer the repetition whose identifier type (CX-5) is MR, the medical
    // record number, since PID-3 may also carry visit or insurance numbers.
    for (std::size_t index = 1; index <= repetitions; ++index) {
        const std::string_view repetition = nth_piece(identifiers, encoding.repetition, index);
        if (nth_piece(repetition, encoding.component, 5) == "MR") {
            chosen = repetition;
            break;
        }
    }
    if (chosen.empty()) {
        return std::unexpected(check.error(ErrorCode::patient_identifier_missing, identifiers,
                                           "PID-3 has no identifier"));
    }
    auto cx = parse_cx(chosen, encoding, check.message().offset_of(chosen));
    if (!cx) {
        if (cx.error().code == ErrorCode::field_value_invalid) {
            cx.error().code = ErrorCode::patient_identifier_missing;
        }
        return std::unexpected(std::move(cx.error()));
    }
    return std::move(cx->id);
}

std::expected<void, ParseError> check_timestamps(const Checker& check) {
    struct TimestampField {
        std::string_view segment;
        std::size_t field;
    };
    static constexpr std::array<TimestampField, 6> kFields{
        {{"EVN", 2}, {"PID", 7}, {"PV1", 44}, {"PV1", 45}, {"OBR", 7}, {"OBX", 14}}};
    for (const auto& segment : check.message().segments()) {
        for (const auto& field : kFields) {
            if (segment.id != field.segment) {
                continue;
            }
            if (auto parsed = check.optional_timestamp(segment, field.field); !parsed) {
                return std::unexpected(std::move(parsed.error()));
            }
        }
    }
    return {};
}

std::expected<void, ParseError> check_observation(const Checker& check, const SegmentView& obx,
                                                  std::vector<Warning>& warnings) {
    const auto& encoding = check.message().encoding();

    const std::string_view status = obx.field(11);
    if (status.size() != 1 || !kResultStatuses.contains(status[0])) {
        return std::unexpected(check.error(
            ErrorCode::result_status_invalid, status,
            std::format("{} '{}' is not a result status", field_location(obx, 11), status)));
    }

    const std::string_view code_field = nth_piece(obx.field(3), encoding.repetition, 1);
    auto code = parse_cwe(code_field, encoding, check.message().offset_of(obx.field(3)));
    if (!code) {
        auto failure = std::move(code.error());
        failure.detail = std::format("{}: {}", field_location(obx, 3), failure.detail);
        return std::unexpected(std::move(failure));
    }
    const LoincCode* loinc = code->coding_system == "LN" ? find_loinc(code->identifier) : nullptr;
    if (loinc == nullptr) {
        warnings.push_back(Warning{WarningCode::observation_code_unknown,
                                   std::format("'{}' in system '{}' is not in the LOINC table",
                                               code->identifier, code->coding_system),
                                   field_location(obx, 3)});
    }

    const std::string_view value_type = obx.field(2);
    const std::string_view value = obx.field(5);
    const auto mismatch = [&](std::string detail) {
        return std::unexpected(check.error(ErrorCode::value_type_mismatch, value,
                                           std::format("{}: {}", field_location(obx, 5), detail)));
    };
    if (value.empty()) {
        return {};
    }
    if (value_type.empty()) {
        return mismatch("OBX-5 has a value but OBX-2 is empty");
    }
    const std::size_t repetitions = piece_count(value, encoding.repetition);
    for (std::size_t index = 1; index <= repetitions; ++index) {
        const std::string_view repetition = nth_piece(value, encoding.repetition, index);
        if (value_type == "NM") {
            const auto number = parse_nm(repetition);
            if (!number) {
                return mismatch(std::format("'{}' is not numeric but OBX-2 is NM", repetition));
            }
            if (loinc != nullptr &&
                (*number < loinc->plausible_min || *number > loinc->plausible_max)) {
                warnings.push_back(Warning{
                    WarningCode::value_implausible,
                    std::format("{} {} is outside the plausible range {} to {}", loinc->display,
                                *number, loinc->plausible_min, loinc->plausible_max),
                    field_location(obx, 5)});
            }
        } else if (value_type == "CWE" || value_type == "CE") {
            if (!parse_cwe(repetition, encoding).has_value()) {
                return mismatch("coded value has neither identifier nor text");
            }
        } else if (value_type == "DTM" || value_type == "TS") {
            if (!parse_dtm(nth_piece(repetition, encoding.component, 1)).has_value()) {
                return mismatch(std::format("'{}' is not a DTM value", repetition));
            }
        } else if (std::ranges::find(kTextualValueTypes, value_type) == kTextualValueTypes.end()) {
            return mismatch(std::format("value type '{}' is not supported", value_type));
        }
    }
    return {};
}

std::expected<void, ParseError> run_rules(const Checker& check, MessageSummary& summary,
                                          std::vector<Warning>& warnings) {
    const Message& message = check.message();
    const SegmentView& msh = *message.find("MSH");
    if (auto header = read_header(check, summary); !header) {
        return std::unexpected(std::move(header.error()));
    }

    const RequiredSegments* supported = find_supported(summary);
    if (supported == nullptr) {
        return std::unexpected(
            check.error(ErrorCode::message_type_unsupported, msh.field(9),
                        std::format("MSH-9 '{}^{}' is not ADT^A01, ADT^A03 or ORU^R01",
                                    summary.message_type, summary.trigger_event)));
    }
    if (summary.control_id.empty()) {
        return std::unexpected(
            check.error(ErrorCode::control_id_missing, msh.field(10), "MSH-10 is empty"));
    }

    auto message_time = check.optional_timestamp(msh, 7);
    if (!message_time) {
        return std::unexpected(std::move(message_time.error()));
    }
    if (!message_time->has_value()) {
        return std::unexpected(
            check.error(ErrorCode::timestamp_invalid, msh.field(7), "MSH-7 is empty"));
    }
    summary.message_time = **message_time;

    for (const std::string_view required : supported->segments) {
        if (!required.empty() && message.find(required) == nullptr) {
            return std::unexpected(
                ParseError{ErrorCode::required_segment_missing, message.raw().size(),
                           std::format("{}^{} requires a {} segment", summary.message_type,
                                       summary.trigger_event, required)});
        }
    }

    auto mrn = read_mrn(check, *message.find("PID"));
    if (!mrn) {
        return std::unexpected(std::move(mrn.error()));
    }
    summary.mrn = std::move(*mrn);

    if (auto timestamps = check_timestamps(check); !timestamps) {
        return std::unexpected(std::move(timestamps.error()));
    }
    for (const auto& segment : message.segments()) {
        if (segment.id != "OBX") {
            continue;
        }
        if (auto observation = check_observation(check, segment, warnings); !observation) {
            return std::unexpected(std::move(observation.error()));
        }
    }
    return {};
}

}  // namespace

AckCode ack_code_for(ErrorCode code) noexcept {
    switch (code) {
        case ErrorCode::msh_missing:
        case ErrorCode::encoding_characters_invalid:
        case ErrorCode::segment_id_invalid:
        case ErrorCode::frame_oversize:
        case ErrorCode::message_type_unsupported:
            return AckCode::ar;
        default:
            return AckCode::ae;
    }
}

std::string_view to_string(AckCode code) noexcept {
    switch (code) {
        case AckCode::aa:
            return "AA";
        case AckCode::ae:
            return "AE";
        case AckCode::ar:
            return "AR";
    }
    return "AR";
}

MessageSummary sniff_header(std::string_view raw) {
    MessageSummary summary;
    if (!raw.starts_with("MSH") || raw.size() < 4) {
        return summary;
    }
    const char field_separator = raw[3];
    const std::string_view first_segment = raw.substr(0, raw.find_first_of("\r\n"));
    const std::string_view msh2 = nth_piece(first_segment, field_separator, 2);
    const char component = msh2.empty() ? '^' : msh2[0];
    // Field n of MSH is piece n of the segment split on the separator,
    // because piece 1 is "MSH" and MSH-1 is the separator itself.
    const auto field = [&](std::size_t number) {
        return std::string(
            nth_piece(nth_piece(first_segment, field_separator, number), component, 1));
    };
    summary.sending_application = field(3);
    summary.sending_facility = field(4);
    summary.receiving_application = field(5);
    summary.receiving_facility = field(6);
    summary.control_id = field(10);
    summary.processing_id = field(11);
    summary.version = field(12);
    const std::string_view type = nth_piece(first_segment, field_separator, 9);
    summary.message_type = std::string(nth_piece(type, component, 1));
    summary.trigger_event = std::string(nth_piece(type, component, 2));
    return summary;
}

std::uint64_t content_hash(std::string_view raw) noexcept {
    std::uint64_t hash = kFnvOffset;
    for (const char c : raw) {
        hash ^= static_cast<unsigned char>(c);
        hash *= kFnvPrime;
    }
    return hash;
}

ControlIdRegistry::Seen ControlIdRegistry::check(std::string_view control_id,
                                                 std::uint64_t hash) const {
    const auto found = hashes_.find(std::string(control_id));
    if (found == hashes_.end()) {
        return Seen::new_id;
    }
    return found->second == hash ? Seen::same_content : Seen::different_content;
}

void ControlIdRegistry::remember(std::string_view control_id, std::uint64_t hash) {
    if (capacity_ == 0) {
        return;
    }
    auto [entry, inserted] = hashes_.try_emplace(std::string(control_id), hash);
    if (!inserted) {
        return;
    }
    order_.push_back(entry->first);
    if (order_.size() > capacity_) {
        hashes_.erase(order_.front());
        order_.pop_front();
    }
}

ValidationResult Validator::validate(const Message& message) {
    ValidationResult result;
    result.warnings.assign(message.warnings().begin(), message.warnings().end());
    const Checker check(message);
    if (auto rules = run_rules(check, result.summary, result.warnings); !rules) {
        result.disposition = Disposition::rejected;
        result.ack_code = ack_code_for(rules.error().code);
        result.error = std::move(rules.error());
        return result;
    }

    const std::uint64_t hash = content_hash(message.raw());
    switch (registry_.check(result.summary.control_id, hash)) {
        case ControlIdRegistry::Seen::new_id:
            registry_.remember(result.summary.control_id, hash);
            result.disposition = Disposition::accepted;
            result.ack_code = AckCode::aa;
            break;
        case ControlIdRegistry::Seen::same_content:
            result.disposition = Disposition::retransmission;
            result.ack_code = AckCode::aa;
            break;
        case ControlIdRegistry::Seen::different_content:
            result.disposition = Disposition::rejected;
            result.ack_code = AckCode::ae;
            result.error = ParseError{
                ErrorCode::control_id_duplicate, message.offset_of(message.find("MSH")->field(10)),
                std::format("MSH-10 '{}' was already used by a different message",
                            result.summary.control_id)};
            break;
    }
    return result;
}

ProcessedMessage Validator::process(std::vector<char> bytes) {
    auto parsed = Message::parse(bytes);
    ProcessedMessage processed;
    if (!parsed) {
        processed.result.summary = sniff_header(std::string_view(bytes.data(), bytes.size()));
        processed.result.ack_code = ack_code_for(parsed.error().code);
        processed.result.error = std::move(parsed.error());
        processed.unparsed = std::move(bytes);
        return processed;
    }
    processed.result = validate(*parsed);
    processed.message = std::move(*parsed);
    return processed;
}

}  // namespace wardwatch
