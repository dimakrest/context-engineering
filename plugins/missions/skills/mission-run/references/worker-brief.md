Mission: ${slug}. Feature: ${feature_id} — ${title}.

Mission state (digest — this is your briefing; do not read state.md wholesale):
${digest}

Assertions you must satisfy (verbatim from contract.md, with their proof budget):
${assertions}

Design guidelines that bind you (verbatim from design.md, with exemplars):
${design}
Deviating from a guideline is allowed only if declared in the handoff with the reason.

Procedures that apply: ${procedures}
Files worth starting from: ${files}
Out of scope: ${out_of_scope}

Deliverables: working code, tests at the layer named above, one commit whose message
starts with "${feature_id}:", and .missions/${slug}/handoffs/${feature_id}.md written to the schema in
${plugin_root}/templates/MISSIONS_TEMPLATES.md. Do not push.
Changes outside the files named above are allowed only when the handoff names them under Completed with the reason.
