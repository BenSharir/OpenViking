# Tau2 Train Pipeline

Tau2 training uses the generic OpenViking session/train batch pipeline.  The
Tau2-specific code in this directory only starts the Tau2 dataset service and
provides thin defaults for the generic runner.

## 1. Start the Tau2 service

```bash
bash benchmark/tau2/train/run_service.sh --host 127.0.0.1 --port 1944
```

Useful options:

```bash
bash benchmark/tau2/train/run_service.sh \
  --host 127.0.0.1 \
  --port 1944 \
  --data-root <tau2-bench>/data/tau2 \
  --config ~/.openviking/ov.conf
```

## 2. Pre-run test score only

Use `--epochs 0` to run final test evaluation without training:

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --epochs 0 \
  --eval-limit 25 \
  --trials 8
```

## 3. Train with a pre-training test score

Use `--baseline-eval` to evaluate the test split before training, then train,
then evaluate the final test score:

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --baseline-eval \
  --epochs 4 \
  --train-limit 25 \
  --eval-limit 25 \
  --trials 8
```

## 4. Defaults

`benchmark/tau2/train/run_batch_train_eval.sh` is a Tau2 convenience wrapper for:

```bash
bash openviking/session/train/run_batch_train_eval.sh \
  --dataset tau2 \
  --domain airline \
  --benchmark-service-url http://127.0.0.1:1944
```

Default concurrency and output behavior:

- rollout concurrency: `150`
- session.commit concurrency: `100`
- eval trials: `8`
- `--clean-result` is enabled by default and clears previous `result/tau2/train/` artifacts before each run. Use `--no-clean-result` to keep previous runs.
- Streaming JSONL events are written to `result/tau2/train/<domain>_<timestamp>/events.jsonl`; train commit events include `trace_id` for live `tail -f` debugging. Use `--events-output` to override the path.

Override examples:

```bash
bash benchmark/tau2/train/run_batch_train_eval.sh \
  --domain airline \
  --epochs 4 \
  --concurrency 150 \
  --commit-concurrency 100 \
  --trials 8
```

## 5. Result and rollout artifacts

By default each run writes artifacts under the repository-level result directory:

```text
result/tau2/train/<domain>_<timestamp>/
  report.json
  rollouts_index.json
  rollouts/
```

`result/tau2/train/latest_rollouts` points to the most recent rollouts directory.
Each rollout artifact group is one original task; each rollout has its own subdirectory
with `memory_context.md`, `messages.json`, `tool_calls.json`, `evaluation.json`,
and, for train rollouts when available, `commit_result.json` and `memory_diff.json`.

