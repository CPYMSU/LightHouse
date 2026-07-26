# CodeFoundry source lineage / CodeFoundry 原始碼沿革

## Purpose / 目的

CodeFoundry is LightHouse's own coding-production-line runtime.  We selectively
translate small, proven algorithms from public source when they improve the
native loop; we do not import another agent's protocol, binary, client, or
control plane.

CodeFoundry 是 LightHouse 自己的程式開發生產線。我們只選擇性翻譯能改善原生
迴圈的小型、成熟演算法；不引入其他 agent 的協議、二進位、客戶端或控制平面。

## Upstream baseline / 上游基線

- Repository: `openai/codex`
- Commit: `61a44880a85d2fd0d8770908dea5733495e571c8`
- Snapshot date: 2026-07-26
- License: Apache-2.0
- Attribution and license copy: [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)
  and `THIRD_PARTY_LICENSES/Apache-2.0.txt`.

Every adapted file has a prominent source header.  The commit above is an
inspection baseline, not a Git submodule and not a runtime dependency.

每個改造檔案都含有明確的來源標頭。上面的 commit 是審閱基線，不是 Git
submodule，也不是執行期依賴。

## Adaptations / 已改造模組

| LightHouse file | Upstream source | Retained idea | LightHouse-specific changes | Tests |
|---|---|---|---|---|
| `src/lighthouse/code_foundry/tool_context.py` | `codex-rs/core/src/context/world_state/tools.rs` | Normalise the first description line, sort deterministically, render a full snapshot once then added/changed/removed deltas, XML-escape text, and cap output by UTF-8 bytes. | Uses `CodeToolSpec` and eight native actions; renders `<code_tools>` only as model context; has no Codex namespace, world-state, or protocol types. | `tests/test_code_foundry_tool_context.py` and `tests/test_code_foundry_loop.py` |
| `src/lighthouse/code_foundry/truncation.py` | `codex-rs/utils/string/src/truncate.rs`; `codex-rs/utils/output-truncation/src/lib.rs` | Preserve prefix and suffix on UTF-8 boundaries; retain a visible truncation marker and approximate token count. | Bounds only observation payloads passed to the model through `CodeHistory.for_model`; the complete local history remains intact for audit/recovery; no Codex content-item types. | `tests/test_code_foundry_truncation.py` |
| `src/lighthouse/code_foundry/patch.py` | `codex-rs/core/src/tools/handlers/apply_patch.rs` | Account for every path touched by a patch before downstream verification. | Parses only LightHouse's existing unified-diff patch capability and returns metadata; Kernel remains the only patch executor. | `tests/test_code_foundry_patch.py` and `tests/test_code_foundry_runtime.py` |

`KernelCodeActionExecutor` also now supplies a LightHouse-owned
`lighthouse.code_review.v1` action. It obtains a new normal `system.git.diff.v1`
Receipt, rejects an empty diff or unresolved merge-conflict markers, and emits a
deterministic review Receipt. This is original LightHouse code, not a Codex
source adaptation; it can later be complemented by a model reviewer.

## Maintenance rule / 維護規則

1. Update upstream code only through a deliberate file-by-file review; never
   copy the Codex repository or add it as a hidden runtime dependency.
2. Record the exact upstream path, commit, local destination, behavioural
   changes, and regression test before merging an adaptation.
3. Preserve the original notice and Apache-2.0 license for every direct
   translation or literal reuse.
4. Keep all provider interfaces, persistence schemas, operation receipts, and
   release behaviour owned by LightHouse.

1. 上游更新必須逐檔審閱；不得整包複製 Codex，也不得將它變成隱藏執行期依賴。
2. 每次改造合併前，都要記錄上游路徑、commit、本地目標、行為差異與回歸測試。
3. 對每個直接翻譯或字面重用，保留原始聲明與 Apache-2.0 授權。
4. Provider 介面、持久化結構、Operation Receipt 與發行行為，一律由 LightHouse
   自己擁有。
