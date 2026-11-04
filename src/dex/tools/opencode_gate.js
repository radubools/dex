// OpenCode's gate for a dex task (design/28).
//
// OpenCode loads this plugin for a dex session only (dex points
// OPENCODE_CONFIG_DIR at its own directory). Before every tool call it hands
// the call to the dex task that started the session, over the loopback address
// in DEX_OC_GATE, and does what dex decides: run it as asked, run it as dex
// rewrote it (a confined command), or fail it with dex's reason, which the
// model reads as the tool's error. Anything that goes wrong denies, so a run
// never gets past dex because dex could not be asked.

const GATE = process.env.DEX_OC_GATE

export default {
  id: "dex.gate",
  setup: async (api) => {
    const hook = await api.tool.hook("execute.before", async (call) => {
      let decision
      try {
        if (!GATE) throw new Error("this session was not started by dex")
        const response = await fetch(GATE, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ tool: call.tool, id: call.id, sessionID: call.sessionID, input: call.input }),
          // As long as the operator may take to answer a question about it.
          signal: AbortSignal.timeout(24 * 3600 * 1000),
          timeout: false,
        })
        decision = await response.json()
      } catch (error) {
        decision = { decision: "deny", reason: `dex could not decide on this call: ${error}` }
      }
      if (!decision || decision.decision !== "allow") {
        throw new Error((decision && decision.reason) || "dex does not allow this")
      }
      if (decision.input && typeof decision.input === "object") call.input = decision.input
    })
    return () => hook.dispose()
  },
}
