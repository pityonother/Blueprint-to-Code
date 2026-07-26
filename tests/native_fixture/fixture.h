#pragma once

#if defined(_WIN32)
#define BTC_FIXTURE_EXPORT __declspec(dllexport)
#define BTC_FIXTURE_NOINLINE __declspec(noinline)
#else
#define BTC_FIXTURE_EXPORT
#define BTC_FIXTURE_NOINLINE __attribute__((noinline))
#endif

namespace BlueprintToCodeFixture {

struct BTC_FIXTURE_EXPORT QualityInputs {
  int base_rating;
  double multiplier;
  int maximum;
};

class BTC_FIXTURE_EXPORT QualityModel {
 public:
  virtual ~QualityModel();
  virtual int Adjust(int value) const;
  int TouchFields(QualityInputs& inputs, int delta) const;
};

BTC_FIXTURE_EXPORT BTC_FIXTURE_NOINLINE int ComputeQuality(int base_rating,
                                                          int bonus);
BTC_FIXTURE_EXPORT BTC_FIXTURE_NOINLINE double ComputeQuality(
    double base_rating, double multiplier);
BTC_FIXTURE_EXPORT BTC_FIXTURE_NOINLINE int QualityLeaf(int value);
BTC_FIXTURE_EXPORT BTC_FIXTURE_NOINLINE int QualityMiddle(int value);
BTC_FIXTURE_EXPORT BTC_FIXTURE_NOINLINE int QualityEntry(int value);
BTC_FIXTURE_EXPORT BTC_FIXTURE_NOINLINE int InspectBranchAndConstant(int value);

}  // namespace BlueprintToCodeFixture
