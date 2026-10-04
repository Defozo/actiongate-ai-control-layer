package actiongate

import rego.v1

snapshot := data.actiongate_snapshots[sprintf("%d", [input.generation])]
config := snapshot.config
profile := config.profiles[config.active_profile]

invariant_fields := {"identity_ok", "tenant_ok", "grant_ok", "labels_ok", "budget_ok", "registry_ok"}

blocks contains sprintf("invariant.%s", [field]) if {
    some field in invariant_fields
    object.get(input, field, false) != true
}
blocks contains "platform.kill_switch" if { object.get(input, "kill_switch", false) }
blocks contains "policy.generation_unavailable" if { not snapshot }
blocks contains "policy.tool_not_allowed" if { not input.tool in config.tools.allowed }
blocks contains "profile.observe_tenant" if {
    config.active_profile == "observe"
    input.tenant != profile.restrict_to
}
blocks contains rule if {
    input.deterministic.decision == "block"
    some rule in input.deterministic.rule_ids
}
blocks contains "deterministic.blocked" if { input.deterministic.decision == "block" }
blocks contains "semantic.unknown" if {
    config.controls.semantic.enabled
    object.get(input.semantic, "verdict", "unknown") == "unknown"
}
blocks contains "semantic.incomplete" if {
    config.controls.semantic.enabled
    object.get(input.semantic, "complete", false) != true
}
blocks contains "semantic.risk" if {
    config.controls.semantic.enabled
    config.active_profile != "observe"
    input.semantic.risk_level >= profile.semantic_block_level
}
reviews contains "approval.tool_required" if {
    input.tool in config.tools.require_approval
    object.get(input, "stage", "input") != "output"
    not object.get(input, "approval_valid", false)
}
reviews contains "semantic.review" if {
    config.controls.semantic.enabled
    config.active_profile != "observe"
    profile.semantic_review_level != null
    input.semantic.risk_level >= profile.semantic_review_level
    not object.get(input, "approval_valid", false)
}

result := "block" if { count(blocks) > 0 }
else := "require_approval" if { count(reviews) > 0 }
else := "redact" if { input.deterministic.decision == "redact" }
else := "allow"

decision := {
    "decision": result,
    "rule_ids": sort(array.concat(array.concat([rule | some rule in blocks], [rule | some rule in reviews]), object.get(input.deterministic, "rule_ids", []))),
    "generation": input.generation,
    "policy_revision": object.get(object.get(snapshot, "config", {}), "revision", 0),
    "reason": sprintf("Policy evaluated: %s", [result]),
}
