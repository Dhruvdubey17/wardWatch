#include "wardwatch/mllp.hpp"

namespace wardwatch {

std::vector<char> mllp_wrap(std::span<const char> payload) {
    std::vector<char> framed;
    framed.reserve(payload.size() + 3);
    framed.push_back(kMllpStart);
    framed.insert(framed.end(), payload.begin(), payload.end());
    framed.push_back(kMllpEnd);
    framed.push_back(kMllpTrailer);
    return framed;
}

}  // namespace wardwatch
