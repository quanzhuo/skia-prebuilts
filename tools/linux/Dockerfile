# check=skip=InvalidDefaultArgInFrom
# The driver supplies both digest-pinned images from target.json.
ARG SYSROOT_IMAGE
ARG BUILD_IMAGE
FROM ${SYSROOT_IMAGE} AS target
RUN dnf -y --setopt=install_weak_deps=False install gcc-toolset-11-gcc-c++ fontconfig-devel mesa-libEGL-devel mesa-libGL-devel \
    && rpm -qa --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort > /skia-target-rpms.txt \
    && dnf clean all

FROM ${BUILD_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg git python3 cmake ninja-build pkg-config build-essential libfontconfig1 libegl1 libgl1 \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | gpg --dearmor -o /usr/share/keyrings/llvm.gpg \
    && echo 'deb [signed-by=/usr/share/keyrings/llvm.gpg] https://apt.llvm.org/noble/ llvm-toolchain-noble-21 main' > /etc/apt/sources.list.d/llvm.list \
    && apt-get update \
    && clang_version=$(apt-cache madison clang-21 | awk '$3 ~ /^1:21[.]1[.]8/ {print $3; exit}') \
    && test -n "$clang_version" \
    && apt-get install -y --no-install-recommends "clang-21=$clang_version" "lld-21=$clang_version" \
    && rm -rf /var/lib/apt/lists/*
# Native-host consumer tests also load the GLES dispatch library, independently
# of the copies in the old sysroot used by baseline-runtime tests.
RUN apt-get update && apt-get install -y --no-install-recommends libgles2 && rm -rf /var/lib/apt/lists/*
COPY --from=target / /opt/skia-sysroot/
COPY tools/linux/clang-wrapper /opt/skia-toolchain/bin/clang
COPY tools/linux/write_target_manifest.py tools/linux/target.json /opt/skia-target/
RUN chmod +x /opt/skia-toolchain/bin/clang \
    && ln -s clang /opt/skia-toolchain/bin/clang++ \
    && python3 /opt/skia-target/write_target_manifest.py
ENV PATH=/opt/skia-toolchain/bin:/usr/lib/llvm-21/bin:$PATH
ENV PKG_CONFIG_SYSROOT_DIR=/opt/skia-sysroot
ENV PKG_CONFIG_LIBDIR=/opt/skia-sysroot/usr/lib64/pkgconfig:/opt/skia-sysroot/usr/share/pkgconfig
ENV SKIA_LINUX_TARGET=/opt/skia-target/manifest.json
WORKDIR /work
