# Star Fox (`0400E88025B1A000`)

A Switch 2 game: update v131072, SDK 21.4.4 built for the Switch 2 (`Ounce`), NVN2 with DLSS. A custom engine (its own prefixes: `vfs`, `vrender`, `vjob`, `vscript`, `vspirv`, `veffects`, `vphysics`; project name `eclipse`), with Wwise 2024.1 for audio, Bullet physics and NPLN for online play. Modules `main`, `sdk`, `subsdk0` (the SDK's movie module), `subsdk3` (`nn::camera`); `subsdk1` (the C library) and `subsdk2` (`NintendoSDK_DLSS`) are shipped but `main` imports nothing from them. **Plays on the Mac** (M4 Pro, 48 GB): the title, the prologue cinematic and the tutorial flight, with sound. The tutorial runs at 56 to 60 fps with a 4K screenshot every 4 s and at 60 without them; the cinematic at 30 fps, which looks like the game's own choice (not checked). Scripted input here reached the tutorial's first lesson and worked its options menus; flying under input has not been watched on this Mac (pull request #1259's author reports 7 minutes of it with a controller on an M5 Max). No IPA has been built.

## What it needs

| Setting | Where | Why |
|---|---|---|
| `mem_alias 0x400000000`, `mem_reserve 0x550000000`, `mem_stack 0x10000000`, `app_pool 0x240000000` | [`game.tsv`](game.tsv) | the Switch 2's 9 GB. The alias region mapped up to +8.26 GB of its 16 GB, 6.78 GB at most at once, through the tutorial's start; 8.5 GB resident, 4 GB of it allocated on the GPU |
| `sys_resource 0x1000000` | `game.tsv` | the NPDM declares 16 MB, so virtual address memory is on; `main` never sets a heap |
| `modules main subsdk0 subsdk3 sdk` | `game.tsv` | `main` imports 17 movie functions from `subsdk0` and 19 `nn::camera` functions from `subsdk3`, nothing from `subsdk1` or `subsdk2` |
| `romfs_marker data.zip` | `game.tsv` | the RomFS has two files, and this is the game |
| `zip_store .zip:.metallib` | `game.tsv` | the archive's members are LZ4-compressed already |
| `shader_groups /data.zip=shader` | `game.tsv` | every shader is a member of the archive |

There is no game-specific patch, and only v131072's build row: the base game, v0, has not been run.

## Files

- A NUP container of 30 GB: a base package (v0) and an update package (v131072), each with its own ExeFS and RomFS. Neither package carries a NACP or an icon, so the name comes from the profile, the display version is the placeholder `v131072`, and the app has no icon.
- The RomFS is two files: `data.zip` (14.5 GB) and `veclipse_build.cvar` (a JSON line: `system.is_shipping_build: true`). `main` opens `rom:/data.zip` through its own virtual file system (`vfs.zip.c`): [LZ4-chained zip members behind a VZIP preamble](../../../docs/reference/shaders.md#lz4-chained-zip-members-behind-a-vzip-preamble-and-shader-binaries-of-one-stage). Its 114,154 members are named by a 16-digit hash with no folders or extensions; 92,069 are chained, 22,085 (the Wwise `RIFF` sound files and `BKHD` banks) stored plain.
- `main` is 192 MB of code with 1,036 imports, of which the host and the other modules supply all but two (gRPC thread-local initialisers, `_ZTH…`). No shader is in any code module.

## Shaders

2,856 in [`shader_map.tsv`](shader_map.tsv): 1,483 vertex, 1,305 pixel, 68 compute; 2,828 one per archive member, no task or mesh shaders, and the rest from the two NDSR upscaler networks embedded in `VIPR` containers. All of them translate. A build takes about 4 minutes in all on an M4 Pro. Each member is the engine's shader binary (`_vrender_shader_binary_t`), read as a single-stage program and paired at draw time (`separable_pairs`: 1,480 cover pairs).

## Build timing

Measured on 2026-11-15, M3 Pro (11 CPU cores, 18 GB), the same external APFS SSD and build folder, update v131072, complete `--platform mac-app` builds with output to a regular file and no profiler:

- Before the [Mac app reuse fixes](../../../docs/reference/packing.md#a-repeated-mac-app-build-recompiles-and-signs-unchanged-data), two warm builds took 14.80 and 12.38 s. Their app-pack stages took 10.38 and 9.87 s, rebuilding the launcher and signing unchanged shader/pipeline data.
- With the permanent fixes, after one pack to update the packer's source key, two warm builds took 5.53 and 2.63 s. Their app-pack stages took 0.60 and 0.39 s and reused the complete app. The shader image and pipeline metadata kept their inode, size, mtime and ctime across both runs. The bundle passed `codesign --verify --deep --strict`; its generated shader tables matched and its links stayed inside the bundle.
- These are warm-build measurements only. No gameplay or iOS test was performed for these packaging fixes.

For the [large raw NUP copy optimization](../../../docs/reference/packing.md#large-uncompressed-nup-files-copy-slowly), one full build per variant was measured on the same M3 Pro and SSD after removing the app and the entire project build folder each time. Shared macOS/Xcode caches were not purged. The unchanged v131072 NUP was the source for both runs; no profiler ran.

- Before: 632.15 s total (10 min 32 s), including 109.03 s for extraction (22.21 s of CPU time), 140.82 s for Mac shaders and 124.98 s for the pipeline archive.
- After: 569.68 s total (9 min 30 s), including 57.99 s for extraction, 138.71 s for Mac shaders and 123.35 s for the pipeline archive. The total fell by 9.9%, extraction by 46.8%; this does not establish a speedup for other games or drives.
- Both builds compiled all 2,856 shaders with zero project-cache hits. File size/SHA-256 checks remained enabled, and both apps passed the deep/strict signature check. No new gameplay or iOS test was performed.

Further isolated phase comparisons on the same Mac and SSD, with fresh project caches for each shader run:

- [Larger shader translation batches](../../../docs/reference/shaders.md#shader-batches-repeatedly-start-the-translator): 141.64 to 133.75 s at `J=11` (5.6% less time), one complete run per variant. All 11,827 MSL, metallib and reflection files compared byte-identical; build bookkeeping was excluded.
- [Overlapping offline pipeline groups](../../../docs/reference/shaders.md#offline-pipeline-groups-leave-a-separate-compute-tail): 139.20 to 115.59 s (17.0% less time) for the overlap prototype, one complete empty-cache phase run per variant, 1,483 bases and 68 compute pipelines. Stamps and coverage metadata were identical; binary archive bytes differed, so byte identity is not claimed for pipeline archives.
- [One read for both signing hashes](../../../docs/reference/packing.md#a-fresh-mac-app-signature-reads-each-large-resource-twice): the 15.6 GB data bundle took 133.85 and 137.55 s with codesign's default, 72.88 and 73.50 s with `--digest-algorithm=sha1,sha256`. The first dual-hash run overlapped a roughly ten-second shader probe; the second was isolated. Clone setup and final verification are excluded. Both hashes cover the resources; SHA-256 remains enabled.

Final complete cold build with the permanent shader batching, shared pipeline worker pool and single-read signing changes: 529.63 s (8 min 50 s), versus the preceding raw-NUP-optimized 569.68 s (9 min 30 s), 7.0% less total time. App, app key and the entire project build folder were removed before the run; shared macOS/Xcode caches remained. Source stayed fixed during the timed CLI. One full run per implementation was measured, with no profiler; table embedding and independent final verification were outside both CLI timings.

| Stage | Before these three changes (s) | Final (s) |
|---|---:|---:|
| Extract | 57.99 | 58.40 |
| Check extracted files | 0.003 | 0.001 |
| Rewrite | 69.49 | 71.75 |
| Shader split | 26.05 | 28.31 |
| Mac shaders | 138.71 | 134.81 |
| Mac host and fonts | 19.49 | 43.13 |
| Pipeline archive | 123.35 | 108.52 |
| App pack, including signing | 131.35 | 81.48 |
| Complete CLI | 569.68 | 529.63 |

The unchanged host/font stage varied substantially, so the total is an observed single-run result, not a stable speedup estimate. An earlier complete overlap prototype took 501.11 s (8 min 21 s), with a 21.42 s host/font stage; the final scheduler additionally releases the graphics worker to queued compute groups, covered by its concurrency test.

All 2,856 shaders rebuilt with zero project-cache hits. The 2,856 MSL sources and 6,115 metallibs remained byte-identical across the prototype and final full builds; the prototype had also matched the preceding build's MSL/metallibs. The final archive passed direct `MTL4Archive` lookups for all 1,483 graphics bases and 68 compute pipelines, with zero misses and a missing-pipeline negative control rejected; these lookups use no compiler fallback. The final app passed `codesign --verify --deep --strict`; generated tables were embedded as files, bundle links resolved internally, and the NUP identity stayed unchanged. Tool tests and Python lint passed with no new findings. No gameplay, iOS or five-game regression run was performed; only Star Fox had prepared inputs, and no reference source copies/worktrees were created.

For the [host/font follow-up](../../../docs/reference/packing.md#cold-mac-host-and-font-builds-serialize-independent-work), the old 43.13 s stage comprised about 27.35 s for the host and 15.78 s for fonts. Its component CPU/load data was not captured, so the historical spike's exact cause remains open. Two new fresh-tree references took 20.18 and 18.53 s: prerequisite make 5.45/3.59 s, native host make 8.00/8.21 s, fonts 6.64/6.63 s. They do not reproduce the spike.

The scheduling prototype's complete fresh host/font runs took 12.53 and 11.99 s. A separate CPU-budget comparison took 11.44 s with one concurrent font process, 11.52 s with five (59.22 versus 62.47 s combined child CPU): cold builds therefore reserve fonts a single process; standalone/font-only generation can use five. The standalone font comparison took 6.23/6.42 s serially, 2.331/2.329 s with five workers. All font table bytes matched after masking only `head` timestamps and their checksum adjustment. Every file-backed host code/data section, including its address and size, matched the existing host; whole-file hashes differ and are not used to claim executable byte identity.

The final complete cold build with these host/font changes took 510.26 s (8 min 30 s), versus 529.63 s (8 min 50 s): 19.37 s, or 3.7%, less total time. App, app key and all project build contents were removed first; shared macOS/Xcode caches remained. The CLI ran without a profiler, concurrent tests or index work. Independent final verification and generated-table embedding were outside both CLI timings.

| Stage | Previous complete run (s) | Host/font optimized (s) |
|---|---:|---:|
| Extract | 58.40 | 58.44 |
| Check extracted files | 0.001 | 0.002 |
| Rewrite | 71.75 | 72.33 |
| Shader split | 28.31 | 28.05 |
| Mac shaders | 134.81 | 132.85 |
| Mac host and fonts | 43.13 | 11.98 |
| Pipeline archive | 108.52 | 120.48 |
| App pack, including signing | 81.48 | 82.67 |
| Complete CLI | 529.63 | 510.26 |

The new component log measured host 11.982 s and fonts 10.783 s concurrently, 11.983 s combined, 60.81 s combined child CPU. Its load averages were 22.87/13.33/8.27 at entry and 24.01/13.92/8.54 at exit; these averages include preceding work and do not identify the historical spike's cause. The unchanged pipeline stage took 11.96 s longer in this run. These are observed single-run complete-build numbers; the isolated fresh host/font references above are the stronger comparison for that component.

All 2,856 shaders rebuilt with zero project-cache hits, with byte-identical MSL/metallibs; all font tables matched after masking only timestamps and their checksum adjustment. Host implementation code and data sections, addresses and sizes matched. Two linker-generated stub sections differed: [equivalent GOT placement](../../../docs/reference/packing.md#equivalent-host-links-produce-different-stub-bytes) was confirmed by relinking the same objects. All 513 regular and 1,091 Objective-C stub targets/selectors were validated; 1,092 GOT references moved while their symbolic targets stayed identical. Raw whole-host byte identity is not claimed.

The final archive passed all 1,483 render and 68 compute lookups with zero misses and rejected an absent-pipeline control. The self-contained app passed deep/strict codesign verification; the original NUP identity remained unchanged. The 133 targeted host/font, process-group, incremental and progress tests passed, as did the documentation tests; Python lint had zero new findings. No gameplay, iOS or five-game regression test was performed; other games did not have prepared build inputs and no reference source copies/worktrees were created.

The [rewrite/split follow-up](../../../docs/reference/rewriter.md#linear-instruction-scans-repeat-python-decoding-and-reachability-checks) first identified main as the rewrite's critical path: its log spanned 71.93 s, SDK 7.94 s, subsdk0 0.83 s and subsdk3 0.36 s. Profiling was diagnostic only and excluded from performance numbers. With linear C word decoding and one spin precheck per candidate, a complete four-module run at five workers per module took 62.98 s, versus 72.33 s for the preceding complete build's rewrite stage. All 28 binary/table outputs matched; the separate source-provenance digest is expected to change. This is one component run, not a general speedup estimate.

A further four-module run with 11 workers per module took 57.40 s, again comparing all 28 binary/table outputs equal. The final automatic allocation follows NSO/NRO text size instead of module count: on this 11-core Mac it selects main 11, SDK 1 and each small subsdk 1. Explicit worker overrides retain their prior meaning. The final complete-build measurement below checks this automatic allocation; the isolated 57.40 s run used the explicit 11-worker setting on every module.

For [ZIP index reuse](../../../docs/reference/packing.md#shader-split-reparses-the-same-zip-directory-for-every-range), complete splits with 11 workers took 28.17 s and 166.44 CPU s without reuse, 27.32 s and 143.47 CPU s with it: 3.0% less elapsed time, 13.8% less CPU. Both compared all three package files and four tables' data rows equal. An earlier direct pair measured 28.29 to 27.39 s. Decompression/CRC checks remain active and no asset-name filter was introduced.

The final complete cold build with these rewrite/split changes took 462.62 s (7 min 43 s), versus 510.26 s (8 min 30 s): 47.64 s, or 9.3%, less elapsed time in this run. The app, app key and entire project build folder were removed first; shared macOS/Xcode caches remained. The CLI ran without a profiler, concurrent tests or index work. Generated-table embedding and independent final verification were outside both CLI timings.

| Stage | Previous complete run (s) | Rewrite/split optimized (s) |
|---|---:|---:|
| Extract | 58.44 | 58.12 |
| Check extracted files | 0.002 | 0.003 |
| Rewrite | 72.33 | 58.55 |
| Shader split | 28.05 | 24.94 |
| Mac shaders | 132.85 | 131.47 |
| Mac host and fonts | 11.98 | 11.31 |
| Pipeline archive | 120.48 | 106.39 |
| App pack, including signing | 82.67 | 68.48 |
| Complete CLI | 510.26 | 462.62 |

The unchanged pipeline and app stages also took about 14 s less each, so the entire 47.64 s reduction cannot be attributed to the rewrite/split changes. These are single-run complete-build observations; the isolated component comparisons above give the narrower evidence for those optimizations.

All 28 rewritten binary/table files matched byte for byte, excluding only the intentionally changed source-provenance digest. Split package files and supplemental pipeline-pair rows matched, and the CLI verified the registered shader-table data rows. All 2,856 shaders rebuilt with zero project-cache hits; all 2,856 MSL sources and 6,115 metallibs matched byte for byte. Direct archive lookups passed all 1,483 render and 68 compute pipelines with zero misses and rejected an absent-pipeline control. The self-contained app passed deep/strict codesign verification; the NUP identity stayed unchanged. The 216 targeted rewrite/split/incremental tests and six documentation tests passed; Python lint had zero new findings. No gameplay, iOS or five-game regression test was performed; other games lacked prepared build inputs and no reference source copies/worktrees were created.

## The picture

Docked, every 3D target was 768x432 from the first frame of the title to the tutorial: MetalFX stands in for DLSS at 768x432 to 768x432, the game's NDSR compute networks take that to 1536x864, and the result reaches the 3840x2160 swap chain, whose top-left 1920x1080 holds the picture. The game's own defaults give the reason for the figure, not the cause: `render.dynres_min_scale_x` is 0.5 and `max` 1.0, so 768x432 is the minimum of a 1536x864 base, at 6 to 11 ms of GPU time a frame on the M4 Pro. Segment timestamps (the profile key `gpu_timestamps segment` then, the host's default since) changed nothing, with the GPU's idle gaps still counted. Resolved, the cause of the docked size: the host answered graphics scale tier 1 docked, and the game renders 0.8 times smaller below tier 0 (byte +0xa of its mode event, `main+0xba69970`): 768x432 for its 1920x1080 window where tier 0 gives 960x540, its handheld size. The host now answers tier 0 in both modes ([The graphics scale does not follow the operation mode](../../../docs/reference/services.md#the-graphics-scale-does-not-follow-the-operation-mode)). Peppy's close-up 65 s into the intro has a mean luminance gradient (x1000 at 1080p) of 5.8 in console footage, 5.3 with tier 0 and 4.6 with tier 1. The sizes and gradients were measured on a build of the PR author's fork, not on `main`. Whether a console renders above 960x540 is not known.

## Driving it on the Mac

`python3 scripts/build_ipa_from_nsp.py <folder> --platform mac` builds the Mac shaders and the host and prints the run command. Headless, through `DM_CTL`: the title is up about 40 s after boot and `A` starts the game. The prologue cinematic follows, about 4 minutes on every run (a save holds no progress yet, and skipping it was not tried), then the tutorial's first question, about 300 s after boot: `LDown`, `A` answers no. Shots of the swap chain hold the picture in their top-left quarter. A `shot` takes about 165 ms: a hitch every few seconds in a run that takes them is the shot's.

## Found on this game

- [Linear instruction scans repeat Python decoding and reachability checks](../../../docs/reference/rewriter.md#linear-instruction-scans-repeat-python-decoding-and-reachability-checks) and [Shader split reparses the same ZIP directory for every range](../../../docs/reference/packing.md#shader-split-reparses-the-same-zip-directory-for-every-range): the rewrite/split measurements above, with unchanged binary/table outputs.

- [Equivalent host links produce different stub bytes](../../../docs/reference/packing.md#equivalent-host-links-produce-different-stub-bytes): the final host comparison above; symbolic imports/selectors remain identical despite different GOT locations.
- [Cold Mac host and font builds serialize independent work](../../../docs/reference/packing.md#cold-mac-host-and-font-builds-serialize-independent-work): the host/font follow-up above; dependency scheduling, font generation, overlap and separate component timings.

- [Offline pipeline groups leave a separate compute tail](../../../docs/reference/shaders.md#offline-pipeline-groups-leave-a-separate-compute-tail): the phase comparison above; compute work shares a bounded compiler budget with the sequential graphics stream.
- [Shader batches repeatedly start the translator](../../../docs/reference/shaders.md#shader-batches-repeatedly-start-the-translator) and [A fresh Mac app signature reads each large resource twice](../../../docs/reference/packing.md#a-fresh-mac-app-signature-reads-each-large-resource-twice): the phase comparisons above; translation batching and a single resource read for both hashes.

- [Large uncompressed NUP files copy slowly](../../../docs/reference/packing.md#large-uncompressed-nup-files-copy-slowly): the cold-build comparison above; block read-ahead overlaps input I/O with hashing and output writes.
- [A repeated Mac app build recompiles and signs unchanged data](../../../docs/reference/packing.md#a-repeated-mac-app-build-recompiles-and-signs-unchanged-data): the warm-build comparison above; unused APFS clone probes and identical pipeline metadata rewrites invalidated reuse.
- [SDK 21 socket initialization and overloads](../../../docs/reference/services.md#sdk-21-socket-initialization-and-overloads) and [Socket `Poll` and `Select` with nothing to wait on](../../../docs/reference/services.md#socket-poll-and-select-with-nothing-to-wait-on): the game's HTTP thread aborted on a failed `poll`.
- [SDK 21's OutputStream on a virtual output device](../../../docs/reference/audio.md#sdk-21s-outputstream-on-a-virtual-output-device) and [SDK 21's hardware Opus decoder manager](../../../docs/reference/audio.md#sdk-21s-hardware-opus-decoder-manager): its voice and music.
- [GameChat queries from the title screen (SDK 21)](../../../docs/reference/services.md#gamechat-queries-from-the-title-screen-sdk-21): a null read in `nn::chat::GetChattingUid`.
- [The camera commands of SDK 21's own camera module](../../../docs/reference/services.md#the-camera-commands-of-sdk-21s-own-camera-module): the game's own camera module, a console with no camera plugged in.
- [sRGB textures loaded and stored raw through an image](../../../docs/reference/rendering.md#srgb-textures-loaded-and-stored-raw-through-an-image): the pale picture with grey blacks (the upscaler network's encoded store was encoded again); measured on a build of our fork, not on `main`.
- [A GPU copy into a texture uploaded from its storage uploaded all of it again](../../../docs/reference/nvn-objects.md#a-gpu-copy-into-a-texture-uploaded-from-its-storage-uploaded-all-of-it-again): 7.6 s and 10 s frames at the prologue's scene changes; measured on a build of our fork, not on `main`.
- [The graphics scale does not follow the operation mode](../../../docs/reference/services.md#the-graphics-scale-does-not-follow-the-operation-mode): the 768x432 scene docked; measured on a build of the PR author's fork, not on `main`.
- [The DLSS network resized](../../../docs/reference/rendering.md#the-dlss-network-resized): `nvnDlssNetworkResize`.
- [TXD through a uniform register kept no handle](../../../docs/reference/shaders.md#txd-through-a-uniform-register-kept-no-handle): 281 pixel shaders, the fur shells among them.
- [A handle pair ORed sampler word first](../../../docs/reference/shaders.md#a-handle-pair-ored-sampler-word-first): 62 pixel shaders.
- [NVN2 compute's separate handles in c\[0\]](../../../docs/reference/shaders.md#nvn2-computes-separate-handles-in-c0): `1002069a331ceded`, dispatched in the prologue.
- [Ampere BMSK and the read of a covered pixel](../../../docs/reference/shaders.md#ampere-bmsk-and-the-read-of-a-covered-pixel): the last 3 shaders, `58046f286de779e1`, `46040940ce0fd49b` and `826168691af2e211`, none asked for in play so far.
- [Compute pipelines compiled at first use](../../../docs/reference/shaders.md#compute-pipelines-compiled-at-first-use): 233 compute pipelines in the app's archive, none built at the title.
- [Compute pipelines outside the bundled archive built at every start](../../../docs/reference/shaders.md#compute-pipelines-outside-the-bundled-archive-built-at-every-start): the second headless start took its 8 from the device's archive.

## Open

- [The picture](#the-picture): the 3D stayed at 768x432 docked because the host answered graphics scale tier 1 there; the host now answers tier 0, which gives 960x540 (measured on a build of the PR author's fork, not on `main`). Whether a console renders above that is not known.
- `[RED] DLSS: the game's bias mask (a reactive mask for MetalFX) is not passed on`.
- The game's wake-up eventfd fails (the host refuses `nn::socket::EventFd`), so its HTTP thread polls without a wake-up; harmless offline.
- A `pso_manifest.tsv` has not been recorded; the later levels and combat, and iOS, not tested.
- Mario Kart World 1.8.0 has not been run with the camera answers changed for this game.
