#!/usr/bin/env node
// Thin wrapper over supabase-js that inserts a BootstrapEvent row into bootstrap_events.
// seq is monotonically incremented per bootstrap job, including events written
// by the portal before this installed runner starts.

import { createClient } from "@supabase/supabase-js";

let _seq = 0;

/** @returns {import("@supabase/supabase-js").SupabaseClient} */
function getClient() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) throw new Error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required");
  return createClient(url, key, { auth: { persistSession: false } });
}

/**
 * @param {{
 *   bootstrap_job_id: string,
 *   phase: import("../../apps/portal/src/lib/pipelines/bootstrap-types.js").BootstrapEventPhase,
 *   level?: import("../../apps/portal/src/lib/pipelines/bootstrap-types.js").BootstrapEventLevel,
 *   message: string,
 *   data?: unknown,
 *   supabase?: import("@supabase/supabase-js").SupabaseClient,
 * }} opts
 */
export async function emitEvent({ bootstrap_job_id, phase, level = "info", message, data, supabase }) {
  const client = supabase ?? getClient();
  async function refreshSeq() {
    const { data: latest, error: selectError } = await client
      .from("bootstrap_events")
      .select("seq")
      .eq("bootstrap_job_id", bootstrap_job_id)
      .order("seq", { ascending: false })
      .limit(1);
    if (selectError) throw new Error(`bootstrap_events seq lookup failed: ${selectError.message}`);
    _seq = latest?.[0]?.seq ?? 0;
  }

  if (_seq === 0) await refreshSeq();

  for (let attempt = 0; attempt < 2; attempt++) {
    _seq += 1;
    const row = {
      bootstrap_job_id,
      seq: _seq,
      phase,
      level,
      message,
      at: new Date().toISOString(),
      ...(data !== undefined ? { data } : {}),
    };

    const { error } = await client.from("bootstrap_events").insert(row);
    if (!error) return row;
    if (!/duplicate key value/.test(error.message) || attempt > 0) {
      throw new Error(`bootstrap_events insert failed: ${error.message}`);
    }
    await refreshSeq();
  }

  throw new Error("bootstrap_events insert failed after seq refresh");
}

/** Reset seq counter — for testing only. */
export function _resetSeq() {
  _seq = 0;
}
