// Responses as the manager sends them, typed by the schema so a drift shows
// here at compile time.
import type { ApiEvent, Flow, Run, Task } from "../api";

export const WORD_PICKER: Flow = {
  name: "word_picker",
  version: "1.2.0",
  content: {
    name: "word_picker",
    version: "1.2.0",
    inputs: { sentence: "string", preferred_letter: "string" },
    output: "tasks.keep_matching_words",
    steps: {
      split_words: {
        handler: "tasks:split_words",
        params: { sentence: "inputs.sentence" },
      },
      keep_matching_words: {
        handler: "tasks:keep_matching_words",
        params: {
          words: "tasks.split_words",
          letter: "inputs.preferred_letter",
        },
      },
    },
  },
};

export const WORD_PICKER_1_0: Flow = {
  ...WORD_PICKER,
  version: "1.0.0",
  content: { ...(WORD_PICKER.content as object), version: "1.0.0" },
};

export const HELLO: Flow = {
  name: "hello",
  version: "0.1.0",
  content: {
    name: "hello",
    version: "0.1.0",
    steps: { greet: { handler: "tasks:greet" } },
  },
};

export const SUCCEEDED: Run = {
  id: "5f3a1c2e-1111-4bbb-8ccc-000000000001",
  flow: "word_picker",
  version: "1.2.0",
  inputs: { sentence: "potato tomate", preferred_letter: "t" },
  status: "succeeded",
  root_id: "5f3a1c2e-1111-4bbb-8ccc-000000000001",
  parent_id: null,
  parent_address: null,
  output: ["potato", "tomate"],
  reason: null,
  created_at: "2026-09-13T10:00:00+00:00",
  finished_at: "2026-09-13T10:00:02.500000+00:00",
};

export const ACTIVE: Run = {
  id: "a11ce000-1111-4bbb-8ccc-000000000002",
  flow: "hello",
  version: "0.1.0",
  inputs: {},
  status: "active",
  root_id: "a11ce000-1111-4bbb-8ccc-000000000002",
  parent_id: null,
  parent_address: null,
  output: null,
  reason: null,
  created_at: "2026-09-13T10:05:00+00:00",
  finished_at: null,
};

export const CANCELLED: Run = {
  ...ACTIVE,
  id: "ca7ce11e-1111-4bbb-8ccc-000000000003",
  root_id: "ca7ce11e-1111-4bbb-8ccc-000000000003",
  status: "cancelled",
  reason: "cancelled by hand",
  finished_at: "2026-09-13T10:06:00+00:00",
};

export const SPLIT: Task = {
  id: "7a7a7a7a-0000-4000-8000-000000000001",
  run_id: SUCCEEDED.id,
  address: { step: "split_words", scope: [] },
  queue: "default",
  handler: "tasks:split_words",
  params: { sentence: "inputs.sentence" },
  fixed_params: {},
  status: "succeeded",
  attempts: 1,
  lease_expires_at: null,
  result: ["potato", "tomate"],
  error: null,
  created_at: "2026-09-13T10:00:00.100000+00:00",
  started_at: "2026-09-13T10:00:00.200000+00:00",
  finished_at: "2026-09-13T10:00:01+00:00",
};

export function event(sequence: number, run: Run): ApiEvent {
  return { sequence, run_id: run.id, kind: "run_finished" };
}
