# Release maintenance

1. Pin the full upstream commit and confirm its `SK_MILESTONE` in `release.json`.
2. Increment `revision` for every new release within the milestone, including
   changes to the upstream commit, toolchain or packaging. Start at 1 only for
   a new milestone. Never reuse a published tag or numeric CMake version.
3. Review patches/dependencies against upstream DEPS. Do not change GN features
   to work around missing source dependencies or accept unused GN arguments.
4. Run regression tests and native package consumer tests locally.
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

The first release uses `0873ec164a06966b90ae0d43ef783cfb180084ae`, milestone 152,
revision 1. It preserves the six existing GN configurations byte for byte;
profile directories changed from `full`/`cpu-only` to `gpu`/`cpu`.
