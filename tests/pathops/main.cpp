#include "include/core/SkPath.h"
#include "include/core/SkRect.h"
#include "include/pathops/SkPathOps.h"

// Exercise native entry points, not just header availability. Return codes keep
// checks active in Release builds, where assert() would silently disappear.
int main()
{
    const auto outer = SkPath::Rect(SkRect::MakeWH(100, 100));
    const auto inner = SkPath::Rect(SkRect::MakeXYWH(25, 25, 50, 50));
    const auto difference = Op(outer, inner, kDifference_SkPathOp);
    if (!difference || !difference->contains(10, 10) || difference->contains(50, 50)) return 1;
    const auto intersection = Op(outer, inner, kIntersect_SkPathOp);
    if (!intersection || intersection->contains(10, 10) || !intersection->contains(50, 50)) return 2;
    const auto exclusive = Op(outer, outer, kXOR_SkPathOp);
    if (!exclusive || !exclusive->isEmpty()) return 3;
    const auto simplified = Simplify(*difference);
    if (!simplified || !simplified->contains(10, 10) || simplified->contains(50, 50)) return 4;
    const auto winding = AsWinding(*simplified);
    if (!winding || winding->contains(50, 50) || !winding->contains(10, 10)) return 5;
    SkOpBuilder builder;
    builder.add(*difference, kUnion_SkPathOp);
    builder.add(inner, kUnion_SkPathOp);
    const auto combined = builder.resolve();
    if (!combined || !combined->contains(50, 50) || !combined->contains(10, 10)) return 6;
    return 0;
}
