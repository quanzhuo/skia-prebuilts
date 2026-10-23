#include "include/core/SkPath.h"
#include "include/core/SkRect.h"
#include "include/core/SkCanvas.h"
#include "include/core/SkPathBuilder.h"
#include "include/core/SkSurface.h"
#include "include/codec/SkCodec.h"
#include "include/encode/SkPngEncoder.h"
#include "include/pathops/SkPathOps.h"
#include "include/private/SkTArray.h"

#ifdef SK_GANESH
#include "include/gpu/ganesh/GrDirectContext.h"
#include "include/gpu/ganesh/mock/GrMockTypes.h"
#endif

#ifdef SKIA_TEST_ANGLE
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#if defined(_M_ARM64)
#include "include/gpu/ganesh/gl/GrGLInterface.h"
#include "include/gpu/ganesh/gl/win/GrGLMakeWinInterface.h"
#endif
#endif

static_assert(sizeof(skia_private::TArray<int>) == 16, "Prebuilt container ABI changed");
#ifdef SK_SANITIZE_ADDRESS
#error "Prebuilt headers must preserve their uninstrumented container layout"
#endif

// Exercise native entry points, not just header availability. Return codes keep
// checks active in Release builds, where assert() would silently disappear.
int main()
{
    const auto outer = SkPath::Rect(SkRect::MakeWH(100, 100));
    const auto inner = SkPath::Rect(SkRect::MakeXYWH(25, 25, 50, 50));
    const auto difference = Op(outer, inner, kDifference_SkPathOp);
    if (!difference || !difference->contains(10, 10) || difference->contains(50, 50))
        return 1;
    const auto intersection = Op(outer, inner, kIntersect_SkPathOp);
    if (!intersection || intersection->contains(10, 10) || !intersection->contains(50, 50))
        return 2;
    const auto exclusive = Op(outer, outer, kXOR_SkPathOp);
    if (!exclusive || !exclusive->isEmpty())
        return 3;
    const auto simplified = Simplify(*difference);
    if (!simplified || !simplified->contains(10, 10) || simplified->contains(50, 50))
        return 4;
    const auto winding = AsWinding(*simplified);
    if (!winding || winding->contains(50, 50) || !winding->contains(10, 10))
        return 5;
    SkOpBuilder builder;
    builder.add(*difference, kUnion_SkPathOp);
    builder.add(inner, kUnion_SkPathOp);
    const auto combined = builder.resolve();
    if (!combined || !combined->contains(50, 50) || !combined->contains(10, 10))
        return 6;

    SkPathBuilder pathBuilder;
    pathBuilder.addRect(SkRect::MakeWH(8, 8));
    const auto path = pathBuilder.detach();
    if (!path.contains(4, 4))
        return 7;
    auto surface = SkSurfaces::Raster(SkImageInfo::MakeN32Premul(8, 8));
    if (!surface)
        return 8;
    surface->getCanvas()->clear(SK_ColorRED);
    SkPixmap pixels;
    if (!surface->peekPixels(&pixels))
        return 9;
    auto png = SkPngEncoder::Encode(pixels, {});
    if (!png)
        return 10;
    auto codec = SkCodec::MakeFromData(png);
    if (!codec || codec->dimensions().width() != 8 || codec->dimensions().height() != 8)
        return 11;
    auto bitmapInfo = SkImageInfo::MakeN32Premul(8, 8);
    uint32_t decoded[64]{};
    if (codec->getPixels(bitmapInfo, decoded, bitmapInfo.minRowBytes()) != SkCodec::kSuccess)
        return 12;
    if (SkPixmap(bitmapInfo, decoded, bitmapInfo.minRowBytes()).getColor(0, 0) != SK_ColorRED)
        return 13;

#ifdef SK_GANESH
    GrMockOptions mockOptions;
    auto context = GrDirectContext::MakeMock(&mockOptions);
    if (!context)
        return 14;
    context->flushAndSubmit();
#endif
#ifdef SKIA_TEST_ANGLE
    if (eglGetError() != EGL_SUCCESS || glGetError() != GL_NO_ERROR)
        return 15;
#if defined(_M_ARM64)
    if (GrGLInterfaces::MakeWin())
        return 16;
#endif
#endif
    return 0;
}
