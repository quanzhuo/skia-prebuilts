# Release maintenance

1. Pin the full upstream commit and confirm its `SK_MILESTONE` in `release.json`.
2. Increment `revision` for every new release within the milestone, including
   changes to the upstream commit, toolchain or packaging. Start at 1 only for
   a new milestone. Never reuse a published tag or numeric CMake version.
3. Review patches/dependencies against upstream DEPS. Do not change GN features
   to work around missing source dependencies or accept unused GN arguments.
4. Run regression tests and native package consumer tests locally.
   On Linux use `python tools/build_linux.py build/skia --jobs 12`; do not build
   release libraries directly against the runner's system headers/libraries.
5. Push validated code and `release.json` to `main`. The release workflow builds
   six native platform/architecture combinations. Manual dispatch can build
   without publishing.
6. All twelve packages must match the source/recipe identity and have passing
   native verification reports. Publishing creates a draft, uploads the complete
   set, then publishes it. The workflow never replaces published release assets.

A failed job prevents publishing. Correct an already published release with a
new revision. Keep temporary Actions artifacts briefly; use Releases as the
stable download surface. Caches never replace verification of source inputs.

If all six native jobs succeeded but publication failed, dispatch the release
workflow with `publish=true` and `artifacts_run_id` set to that completed run.
This skips recompilation. The publisher validates repository, branch, workflow,
all six successful jobs, all twelve archives/reports and the current recipe hash.
The release tag points to the verified producer commit. Changed build material
requires a new full native build; artifacts cannot be reused across recipe changes.
The recipe also covers the native consumer and verification implementation;
new verification requirements cannot reuse reports from an older policy.

The first release uses `0873ec164a06966b90ae0d43ef783cfb180084ae`, milestone 152,
revision 1. It preserves the six existing GN configurations byte for byte;
profile directories changed from `full`/`cpu-only` to `gpu`/`cpu`.

Revision 2 introduces the common glibc 2.28 baseline for Linux x64 and ARM64,
with unchanged GN configurations and Clang 21.1.8. The shared Docker definition
pins build/sysroot base image indexes; exact installed RPMs, sysroot digest and
image identity are retained in each package's manifest. Review updates to
`tools/linux/target.json`, the Dockerfile and compiler wrapper as release inputs.
GCC Toolset runtime compatibility code retains its GPLv3 + Runtime Library
Exception texts in the package.

Linux publication additionally requires ELF symbol ceilings, the revisioned
SONAME and its relative symlink chain, old-runtime execution and relocated
runtime deployment. Modern-host consumers and ASan remain required. Never
substitute a host-only `ldd` check for the old-runtime tests. ARM64 uses its
native GitHub runner and the same source, container recipe and gates as x64.
Changing the milestone or revision changes the SONAME; all consumers must be
rebuilt against that exact package. Do not replace the previously published r1.
