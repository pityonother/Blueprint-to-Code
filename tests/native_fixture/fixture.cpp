#include "fixture.h"

#include <algorithm>

namespace BlueprintToCodeFixture {

QualityModel::~QualityModel() = default;

BTC_FIXTURE_NOINLINE int QualityModel::Adjust(int value) const {
  return value < 0 ? 0 : value + 7;
}

BTC_FIXTURE_NOINLINE int QualityModel::TouchFields(QualityInputs& inputs,
                                                   int delta) const {
  inputs.base_rating += delta;
  const double scaled =
      static_cast<double>(inputs.base_rating) * inputs.multiplier;
  return std::min(static_cast<int>(scaled), inputs.maximum);
}

BTC_FIXTURE_NOINLINE int ComputeQuality(int base_rating, int bonus) {
  constexpr int kMaximumQuality = 100;
  const int combined = base_rating + bonus;
  return combined > kMaximumQuality ? kMaximumQuality : combined;
}

BTC_FIXTURE_NOINLINE double ComputeQuality(double base_rating,
                                           double multiplier) {
  constexpr double kQualityScale = 1.25;
  return base_rating * multiplier * kQualityScale;
}

BTC_FIXTURE_NOINLINE int QualityLeaf(int value) {
  return ComputeQuality(value, 11);
}

BTC_FIXTURE_NOINLINE int QualityMiddle(int value) {
  return QualityLeaf(value) + 3;
}

BTC_FIXTURE_NOINLINE int QualityEntry(int value) {
  return QualityMiddle(value) * 2;
}

BTC_FIXTURE_NOINLINE int InspectBranchAndConstant(int value) {
  constexpr int kBranchBoundary = 42;
  if (value >= kBranchBoundary) {
    return value - kBranchBoundary;
  }
  return kBranchBoundary - value;
}

}  // namespace BlueprintToCodeFixture
