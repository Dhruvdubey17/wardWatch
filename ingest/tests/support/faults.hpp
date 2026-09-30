#pragma once

#include <functional>
#include <map>
#include <string>
#include <string_view>

namespace wardwatch::testing {

inline std::string replace_first(std::string text, std::string_view from, std::string_view to) {
    const std::size_t at = text.find(from);
    if (at != std::string::npos) {
        text.replace(at, from.size(), to);
    }
    return text;
}

inline std::string erase_segment(std::string text, std::string_view id) {
    const std::size_t start = text.find("\r" + std::string(id) + "|");
    if (start == std::string::npos) {
        return text;
    }
    const std::size_t end = text.find('\r', start + 1);
    text.erase(start, end - start);
    return text;
}

// Applies each fault in contracts/fault_catalog.json to a valid CR-terminated
// ORU^R01. These mirror the simulator's fault injector so the ingest tests do
// not depend on Python. duplicate_control_id and oversized_frame need more than
// one message or a framer, so the tests handle them separately.
inline const std::map<std::string, std::function<std::string(const std::string&)>, std::less<>>&
fault_transforms() {
    static const std::map<std::string, std::function<std::string(const std::string&)>, std::less<>>
        transforms{
            {"truncated_message",
             [](const std::string& message) {
                 const std::size_t msh_end = message.find('\r');
                 const std::size_t next_end = message.find('\r', msh_end + 1);
                 return message.substr(0, msh_end + 1 + ((next_end - msh_end - 1) / 2));
             }},
            {"missing_pid",
             [](const std::string& message) { return erase_segment(message, "PID"); }},
            {"wrong_encoding_characters",
             [](const std::string& message) {
                 return replace_first(message, "MSH|^~\\&|", "MSH|^^\\&|");
             }},
            {"lf_segment_terminators",
             [](const std::string& message) {
                 std::string out = message;
                 for (char& c : out) {
                     if (c == '\r') {
                         c = '\n';
                     }
                 }
                 return out;
             }},
            {"non_numeric_nm",
             [](const std::string& message) {
                 return replace_first(message, "LN||112|", "LN||high|");
             }},
            {"invalid_timestamp",
             [](const std::string& message) {
                 return replace_first(message, "|20240315130005+0000|", "|20241315130005+0000|");
             }},
            {"trailing_separators",
             [](const std::string& message) {
                 std::string out;
                 for (const char c : message) {
                     if (c == '\r') {
                         out += "||";
                     }
                     out.push_back(c);
                 }
                 return out;
             }},
            {"non_ascii_bytes",
             [](const std::string& message) {
                 return replace_first(message, "Lindgren", "Lindgr\xC3\xA9n");
             }},
    };
    return transforms;
}

}  // namespace wardwatch::testing
