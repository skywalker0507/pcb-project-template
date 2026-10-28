# Gateway design as template validation

The modular SIM SMS gateway is a real design used primarily to exercise and
improve this template. Review the workflow after three meaningful checkpoints,
two related rejections, or an otherwise required workflow audit. Do not run a
full stage or release pipeline after every local edit.

## Completed checkpoint batch, 2026-10-26

1. Merged template commit `75e1a3c` into both isolated gateway worktrees while
   preserving their uncommitted hardware drafts. Archived the six inherited
   registries and replaced them with project-scoped, unreviewed inputs.
2. Diagnosed the installed atopile 0.15.8 library and PCB footprint schemas:
   neither exposes `zones`. The raw dual-slot candidate therefore loses all
   twelve socket rule areas. The source-preserving adapter retains that raw
   board, creates a separate normalized board, rejects other/partial resource
   differences, and verifies exact electrical/geometry parity and independent
   resource integrity. It does not modify the installed compiler.
3. Built the 24-component child using the default `pcbctl build` path. The
   independent project audit passes part identities, checked pin contracts and
   both six-area socket resources. A subsequent `build --plan` reuses the exact
   candidate instead of recompiling. The mainboard passes source-only checks;
   its complete power and USB design remains unfinished.

## Framework changes supported by this batch

- Source normalization handles a demonstrated compiler export gap using reviewed
  local resources. Missing/incorrect pads, identity, partial areas and unknown
  affected compiler versions remain failures. Raw candidates and normalization
  provenance remain available; freezing retains the normalization receipt.
- Cross-file consistency no longer requires a USB channel in every design.
  Each declared channel binds its own manufacturer evidence, stack, target,
  geometry and optional layer sequence. Existing example USB constants moved
  unchanged into `config/requirements.json`; no reviewed numerical threshold
  was substituted. Different project identities are rejected.
- New-project requirements include all structural lists accepted by the draft
  validator, including placement regions, and explicitly declare no impedance
  channels until the project adds them. Draft validation does not imply review.

## Evidence and limits

The child checkpoint is in the sibling worktree
`modular-sim-sms-gateway-sim-module-rev-a/docs/design/atomic-checkpoint.md`.
Machine evidence lives in each project's `build/` lanes. The root's schema probe
is `build/00-shared/evidence/template-gateway-iteration/exporter-schema.json`.

Hardware roaming/profile/SMS tests, two-stage SIM behavior, mechanical review,
fabrication rules, complete electrical contract review, freeze, placement,
routing and manufacturing approval are still open. The passing child candidate
must not be treated as a releasable PCB or used to approve the incomplete
mainboard. The next review batch should follow actual mainboard power/USB work.

## Next measured optimization candidate

The child workflow profile still identifies electrical builds as the main time
cost. A changed validator/receipt contract currently invalidates the whole build
fingerprint even when the electrical source is unchanged, so this batch needed a
new compiler run after strengthening normalization receipt validation. Consider
separating compiler-input identity from candidate-validation identity in a later
batch: reuse an unchanged raw compiler candidate, but always run the changed
normalization and validation gates again. This is a proposal, not an existing
capability or permission to reuse an old passing gate.

## Mainboard power/USB checkpoint, 2026-10-26

The mainboard now generates a candidate containing 24 modeled components,
including TPS22975DSGR modem power switching and TS3USB221ARSER USB data
disconnect. Independent pin/net checks cover the modeled blocks; converters,
bulk capacitors, monitor and physical host/modem interfaces remain incomplete.
Project requirements stay draft and reviewed-input gates still reject them.
No freeze, physical adoption or manufacturing export was performed.

The first actual build found an old namespace in the copied 15k resistor
footprint. Its failure receipt remains available. A new source identity
preflight rejects stale library/item identities during `build --plan`, before
WSL compilation. Correcting the source header preserved native pad geometry;
the second build and independent block audit pass, and the next plan reuses
that exact mainboard candidate. Source diagnostics also expose bounded leaf
errors from nested ExceptionGroups instead of only their count.

Template verification passes 271 unit tests and all four template checks.
Detailed project evidence is in the mainboard worktree's
`docs/design/power-usb-checkpoint.md`. Workflow profiling reports two compiler
runs totaling about 72 seconds and no cleanup recommendations. This is an
observed instance where earlier local resource checks avoid wasted compiler
work. No child recompile was needed for this task; its previous passing receipt
is retained, and a later build plan must reassess producer identity before reuse.

Another observation is that candidate resource integrity proves the components
that were emitted, while abstract pin contracts need separate completeness
review. Existing draft requirements prevent this partial design from freezing.
A future compact receipt could expose that coverage more directly; this remains
a recorded improvement candidate rather than a new acceptance rule.

## Converter/protection checkpoint, 2026-10-26

Three more functional drafts exercise the production path: TPS259470 input
protection, TPS62133 host5V and TPS54620 modem supply with split bulk storage.
The new candidate contains58 modeled components. One new full compiler run,
all-component source integrity, independent converter/SIM/USB pin/net audits
and the next exact-candidate reuse plan pass. Custom polygon terminals and
repeated thermal pad numbers survive this actual build without gate changes.

The project records DC calculations separately from144 first-order CCM
sensitivity samples, source/script hashes, a rejected earlier compensation
choice and a bounded comparison. These are draft screening results; no SPICE,
measured stability, thermal or reverse-input/surge approval was fabricated.
Explicit requirements still block freeze for transient clamps, charged-output
reversal and remaining physical interfaces/monitoring. The 12V input adapter
preselection is now4A so the programmed current-limit upper envelope fits it;
no adapter purchase or hardware test occurred.

Current default commands and integrity/reuse gates handled this checkpoint.
No extra default entrypoint or repeated compiler run was needed. Remaining
framework candidates are clearer component-coverage metadata and explicit
proof levels for calculated versus manufacturer/measured evidence. These are
observations for a later bounded change, not a relaxation of existing gates.
Details and exact receipts live in the mainboard worktree's
`docs/design/converter-checkpoint.md`.

## Input blocking/clamp checkpoint, 2026-10-27

The next mainboard candidate contains61 modeled components after adding a
series reverse-blocking diode, a unidirectional TVS after it, and an output
negative clamp. A single new compiler run, all-component source integrity,
29 power pin/net checks and15 inherited SIM/USB checks pass. The next plan
reuses that exact candidate. Native TO-277A anodes1/2 and cathode K, including
unnumbered paste windows, pass the existing resource gate without engine changes.

Project evidence binds the current source tree, exact candidate, calculations,
manufacturer packet and independent audits. Conditional arithmetic remains
separate from waveform/thermal proof: one of12 unclamped cable-inductance
sensitivity cases exceeds the eFuse absolute input rating, and no hardware
surge/reversal approval is asserted. The source-bound blocking/clamp network
therefore does not clear the existing freeze blockers.

A PDF subscript-layout parsing rejection is retained; its correction stays in
the project check instead of expanding the engine or default command surface.
Workflow review reports no repetition cleanup or stale runs. This is the first
functional checkpoint since the previous template review; the next two
interface/monitor checkpoints will revisit coverage/proof-level metadata.
Exact receipts and limits are documented in the mainboard worktree's
`docs/design/input-protection-checkpoint.md`.

## Interface/monitor checkpoints and bounded review context, 2026-10-27

The physical-interface draft replaces abstract Pi40 and modem52 pin contracts
with actual connector resources, reaching66 modeled components. A later monitor
checkpoint adds four-rail divider/isolation/buffer/ADC observation and a hardware
validity signal, reaching102. Each checkpoint required one new compiler run.
The latest exact candidate passes all-component source integrity,38 independent
monitor net groups,41 manufacturer copper-land probes and the inherited power,
SIM,USB and interface checks. Subsequent planning reuses the exact candidate.
No freeze, physical adoption or manufacturing export occurred.

After these three functional checkpoints (input protection, interfaces, monitor),
the template now exposes a bounded informational `review_context` and
`validation_scope: modeled_components_only` in build plans, duplicate-build
responses and successful build receipts. Requirements/selection/assembly states,
declared reference count and up to12 declared unknown IDs make partial scope
visible. The actual mainboard reports draft,102 references and9 unknowns. The
reuse next action now asks to review the candidate instead of suggesting freeze.

These fields do not measure semantic completeness, authorize freeze or change
reuse identity. A plan reads current metadata; a build receipt stores a snapshot.
Tests cover draft and reviewed plans, malformed/missing metadata, bounded output,
unique reference counting and unchanged fingerprint on review metadata edits.
All274 unit tests and four template checks pass. This deliberately small change
keeps supplier-specific calculations and net audits in the project worktree.

Project analysis explicitly separates datasheet bounds, conditional arithmetic
and unmeasured behavior. In particular, the switch's full-temperature powered-off
leakage bound is specified at a grounded drain; extrapolating it across a loaded
drain does not establish zero backfeed. Calibrated accuracy and arbitrary-ramp
brownout response remain unproven and block freeze. Full electrical review,
current connector/latch drawings, power waveforms/thermal, fabrication rules and
mainland-China modem/card/SMS proof remain open. Detailed receipts and limits
live in the mainboard worktree's `docs/design/monitor-checkpoint.md`.

Workflow profiling passes with no stale runs or cleanup recommendations. Native
resource generation caught a KiCad rounding API mismatch before compilation;
it was corrected without changing existing resources. Generic source pin/pad
preflight and compiler/validator identity separation remain measured follow-ups,
not implemented capabilities.

## Service-power review and navigation counts, 2026-10-27

A scoped review of the unchanged102-part mainboard found that modem-off leaves
the daughterboard connector's logic3.3V live. Earlier text incorrectly grouped
SIM card changes and whole daughterboard insertion under the same modem-off
procedure. Both project contracts now require full-device shutdown and verified
deenergization for daughterboard insertion/removal; SIM card service has its own
modem/SIM-off conditions. Fifteen exact-candidate wiring groups pass. This is a
requirements correction, not hardware hot-swap validation or a source change.

An archived Molex SD-48099-006 RevG drawing, mirrored by DigiKey, confirms the
existing latch copper lands, two unequal locating holes and connector-relative
intent. Actual placement, current drawing revision, body/actuation envelope and
complete assembly approval remain open. Power sequencing/QOD calculations also
remain conditional; neither an off ADC reading nor a fixed delay authorizes
service. No circuit, footprint, candidate or physical baseline was regenerated.

The same work exposed a navigation defect: basic-valid draft requirements showed
zero blockers, and9 open items silently became4 after layered compaction. Stage
navigation now uses the existing reviewed-requirements predicates, preserves
counts across bounded previews and marks workset truncation. The actual mainboard
reports2 review-blocker classes and9 open items. Draft validation and independent
freeze gates retain their original behavior. Four new tests cover review state,
malformed basic input, nonblocking notes and counts across both compaction steps;
all278 unit tests and four template checks pass.

This metadata-only template fix does not change electrical producer identity.
The mainboard plan still reuses the exact102-part candidate; no new compiler or
full-board check was needed. Detailed scoped evidence and remaining constraints
are recorded in the mainboard `docs/design/service-power-checkpoint.md`.

## USB land/source preflight and Pi contact transform, 2026-10-27

Archived exact-part manufacturer images expose nine copper-shape differences in
the native USB connector footprint:two external shell annuli, two inner shell
lands and five signal lands. The source draft now follows the selected reference
shapes while retaining all pad numbers,positions,sizes,drills,layers and wiring.
The final102-component candidate passes all-component source fidelity and scoped
block audits. All426 numbered pin/net-name mappings,102 component identities and
101 other source footprints match the previous checkpoint.

The first iteration corrected only four shell lands before compilation. Its
compiler/resource gate passes, but a later manufacturer-geometry contract rejects
the remaining five rounded signal lands. This is a real ordering inefficiency:
full source geometry should have been checked before the expensive compiler.
The failed receipt and intermediate source remain preserved. A project-only
source-only preflight now reproduces that rejection, verifies all13 lands after
correction and precedes the final compiler run. It also binds the selected
manufacturer image and checks a virtual40-contact Pi mating transform against
a wrong-side negative control. This has not been generalized into the default
build plan; manufacturer numbers remain outside engine code.

One retrieved file has HTTP success and a valid PDF header but renders a website
unavailable notice. Project content review rejects it and accepts only the
visually identified exact-part manufacturer image for limited reference geometry.
Retrieval success is therefore recorded separately from source identity/review.
Current document versions,body/solder faces,actual board edge,Pi header/tail/stack,
antenna space and full manufacturing approval remain unresolved.

The final exact candidate is reusable. Existing analytic receipts remain bound
to unchanged gateway source and script hashes, so their144/64-case calculations
were not repeated. No template engine code changed and no new template regression
run was needed;the prior278-test/four-check result is retained without claiming
project experiments as new unit tests. Generic explicit resource contracts before
compilation remain a supported follow-up candidate for the next template review.
Full details live in `docs/design/interface-mechanics-checkpoint.md` in the
mainboard worktree. No freeze or physical adoption occurred.

## Declared source preflight in default builds, 2026-10-27

The next checkpoint closes the prior manual-entry gap. Optional backend
`source_preflight` declares a project Python checker and exact input files.
Plan/check/build execute it fresh before toolchain lookup, reuse or compilation;
failure/timeout/malformed output/input mutation rejects. Declared inputs and
checker/runner hashes participate in producer identity. Logs stay in tmp;
bounded evidence preserves scope without changing stage adoption. Freeze checks
freshness and snapshots declared inputs. New-project initialization archives and
clears inherited declarations; ordinary reconfiguration preserves them. No
manufacturer numbers or new default commands were added to engine code.

Eight new preflight tests and one initializer regression bring the suite to287;
all four template checks pass. A real-resource isolated rejection reproduces the
five bad USB signal corners through the common build-plan function before the
compiler starts,while preserving the real project's inputs. The mainboard's
actual102-component rebuild passes resource and block checks,preserves all102
source footprints/identities and426 numbered pin/net names,and subsequent plan
reuses its exact candidate. Child source/historical candidate remain unchanged;
its optional check is undeclared and current producer reuse is not reassessed.

A new project preservation checker initially compared SWIG object addresses via
str(LIB_ID). Explicit library/item getters corrected that measurement error;
the original checker/log are retained. This is a project diagnostic correction,
not a relaxed acceptance baseline. No further template regression was needed.
The generic extension only checks explicitly declared rules/dependencies;it is
not automatic supplier-document validation. Compiler and validator identities
are still coupled,so this integration required one rebuild without source edits.
That separation remains deferred. Family mating documents do not resolve the
actual Pi header/stack;requirements remain draft with9 open items,no freeze,
physical adoption or hardware proof. Detailed evidence is in the mainboard
`docs/design/source-preflight-checkpoint.md`.

## External host axial mating screen, 2026-10-27

Published SSW insertion-depth and TSW post tolerances support an explicit
external male-header candidate without changing the existing carrier socket or
electrical source. A shorter same-pitch40-pin option fails;nominal-only axial
gap sizing also fails at the shortest allowed post. Body reference heights
remain separate from toleranced stack approval. The project records a conditional
axial model,three boundary scenarios,two alternatives and a rendered SVG.
No board placement,copper,compiler or template execution code changed;one cheap
default plan reuses the existing102-part candidate and refreshes source preflight.
The prior287-test/four-check result is retained without a new regression claim.

A related portability gap is now confirmed with git check-ignore:the mandatory
source-preflight reference image lives only under ignored tmp,as do new vendor
PDFs. Missing inputs reject correctly,but reproducible reference recovery or
promotion is not yet modeled as durable project input. This is queued for the
next focused template review;old reference paths/receipts are preserved. Full
assembly,USB signed body/solder faces and hardware proof remain open;no release
gates or baseline were relaxed. Details are in the mainboard
docs/design/host-assembly-checkpoint.md.

## Durable references and compiler/validation identities, 2026-10-27

The real project's47 active reference paths are now46 byte-identical snapshots
under references/vendor with a hash/origin/scope manifest. Rejected downloads
and historical receipts were retained. Generic default preflight rejects tmp,
generated,ignored,missing or changed required inputs. New-project initialization
archives/removes the reference manifest;freeze snapshots references and validators.

Schema2 separates complete validation identity from compiler identity. Complete
schema1 proof can supply pinned raw output;current normalization/parity/resource
gates still rerun and source origin is flattened/content-bound. A failed fresh
gate does not restart compilation. Common driver invocation semantics have an
explicit version. No manufacturer rules were added to the engine.

With the entire vendor download cache temporarily hidden,the real default plan
and build revalidated all102 components without a compiler call. Exact candidate
bytes,all102 source footprints/identities and426 named pin/net maps stayed equal.
The cache was restored in finally;subsequent plan requires neither compilation
nor revalidation. Updated resource-manifest binding required small conditional
analyses to rerun;the unchanged144-case power calculation was retained. All301
tests/four template checks pass,including failure-without-compiler,identity,
origin,reference durability/tamper and initialization cases. Initial fixture/Git
invocation errors were corrected without changing acceptance baselines.
No freeze,placement,copper or hardware approval occurred. Project details are
in docs/design/reference-persistence-checkpoint.md in the mainboard worktree.


## 2026-10-28：USB 路径与第三轮门禁复盘

主板的 USB 台架参考、五组连接/两个孤立引脚及13焊盘参考板边检查通过；六个反例被拒绝，源与候选未改变。实际安装面/Z/机壳/线缆实测仍待验。

真实模板缺口是状态页和电气合同/冻结入口采用不同的审核标准。电气合同与冻结现在复用完整需求结构、reviewed 状态和未决项检查；冻结在复制结束时再次核对输入绑定并归档校验器。六个新用例先复现后修复，全套307测试/4检查通过，未改基线或放松门禁。脚本/测试已按旧哈希同步两个工程。

另已识别证据阶段循环：需要样板才能完成的波形/温升/校准与布局后的装配验收被当前草案置于冻结前，且 blocks 目前未按阶段解释。下一步应先设计一致的阶段门禁，再审核九类未决项的证据归属；本轮未更改其阻塞状态。项目细节见主板 docs/design/usb-host-and-gate-checkpoint.md 与 evidence-dependency-review.json。

## 2026-10-28：阶段门禁与显式样板范围

引擎0.4.0统一需求结构/阶段谓词，冻结、物理与发布入口分别执行累积范围。
旧字符串保持阻塞，缺失/错误范围拒绝。默认生产目标要求所有生产待验项；
显式样板目标必须有用途、限制和精确覆盖待验项的计划，不能跳过物理/制造门禁。
策略、readiness、导出范围文件和快照保留未完成工作与production_ready标记。
目标/待验项/计划变化使旧策略和readiness失效；发布路径与默认命令未增加。

主板9类义务保留为17条开放记录（8电气、1物理启动、2布局发布、6样板实测），
保存原文/旧范围及完整映射，没有标记任何项目解决。源与102器件候选不变。
18个新增回归先复现再修复；两处历史夹具补齐必需结构后，全套325测试/4检查通过。
18个通用文件按旧哈希同步两工程；没有覆盖工程选型、制造规则或需求审核状态。
下一步为独立桌面电气/资源审核，外部兼容台架和实测继续待验。
# Operational consolidation — 2026-10-28

The SIM mainboard now declares thirteen existing calculations and native checks
in `config/project-checks.json`, executed by `pcbctl verify project`. The first
batch executed all thirteen successfully. After changing declared framework
dependencies, seven checks reran and six reused their exact receipts; the next
unchanged batch executed zero checks and reused all thirteen. No electrical
source, part, net, footprint or candidate bytes changed. The compiler was not
invoked during the matching electrical revalidation.

Project selection is explicit or machine-local; root session boot now navigates
to the SIM project while `--local` preserves access to template maintenance.
Status reports the selected root and the last check batch, with freshness
explicitly unchecked. Requirement-review obligations still block freeze; no
screening pass is converted into approval. Existing stage handoffs and failed
historical evidence remain intact.

The complete framework regression passes 349 tests and four aggregate checks.
Evidence is in `build/00-shared/evidence/framework-optimization-20261028/complete/`;
real project aggregates are in its `build/03-electrical/project-checks/` folder.
The declarations and complete dependency sets replace per-checkpoint Python
orchestration and repeated hand-maintained status snapshots. See
[the command and proof contract](PROJECT_CHECKS.md).
