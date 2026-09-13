Mission: ${slug}. Feature: ${feature_id} — ${title}.
Review the patch for ${feature_id} against these assertions. You have not seen how or why it was
written and you should not go looking.
${assertions}
Design guidelines this feature was bound to (pre-code, from design.md):
${design}
Patch: ${patch_path} (base ${base}, head ${head}) — read this file; it is your only diff, and you do not run git yourself.
Codebase intelligence: ${intelligence} — for every public symbol the patch changes, find its callers
(graphify affected "<symbol>" when graphify is named; grep otherwise) and grade them in your Impact table.
Return a per-assertion verdict (satisfied / not satisfied / cannot tell from the diff),
a per-guideline conformance verdict, the impact table, plus defects with file:line and a
root-cause cluster hint. "cannot tell" is a legitimate and useful answer.
Write nothing to the repository. Your final message is the review, in the format your instructions give.
