# Community labeling prompt (v3)

Used by `src/label_communities.py generate`. One call per Leiden community.
Placeholders `{region}`, `{period}`, `{n_words}`, `{top_words}` are filled in
by the script before the call. The model must respond by calling the
`assign_label` tool - no free-text output is accepted.

Changed from v2 (2026-09-02): the tool call now requires a `reasoning`
field, filled in before `label`/`lane`, forcing the model to work through
what Core/Mid/Peripheral actually show before deciding, instead of
pattern-matching the first few words and reaching straight for a
grammatical or "Structural / Uncertain" label. `tool_choice` still forces
the `assign_label` tool on every call (so a response always parses), but a
required field ahead of the decision itself gets the same effect a
free-text "think step by step" pass would, without giving up that
guarantee. Motivated directly by user feedback (2026-09-02): too many
communities were coming back as easy, generic descriptions of the grammar
("Base-Form Verbs", "Second-Person Verb Forms") without clear evidence the
Mid-rank/Peripheral tiers were actually weighed, rather than a real,
considered judgment that no topical reading holds. A genuinely grammatical
or foreign-language community is still a correct, expected answer here -
the v2 system prompt's reasoning for that stands, unchanged below - but it
should be a conclusion reached after looking, not a default reached before
looking.

## Fixed lane list

Decided 2026-08-03 (see the vault's `wiki/labeling-pipeline.md`), derived
from 707 real labels already produced across the combined, British, and
American runs. Do not invent a lane outside this list.

1. Government, Law & Administration
2. Religion, Theology & the Church
3. Morality: Virtue & Vice
4. Medicine, Body & Health
5. Science, Mathematics & Natural Philosophy
6. Nature, Landscape & Weather
7. Military & Warfare
8. Trade, Finance & Commerce
9. History, Genealogy, Nobility & Kinship
10. Rhetoric & Persuasion
11. Literature, Drama & Poetic Diction
12. Domestic Life, Dress & Household
13. Geography & Territory
14. Books, Learning & Scholarship
15. Structural / Uncertain

## System prompt

You are labeling word clusters from a computational study of an early
modern through nineteenth-century English-language corpus: the Text
Creation Partnership (EEBO, ECCO, Evans; 1500-1800, clean manually-keyed
transcription) plus the British Library's digitised 19th-century books
collection (1800-1900, OCR text) as the supplement covering the period TCP
does not reach. Each cluster is a "community" found by running the Leiden
algorithm on a per-period word-similarity network - words that are used in
similar contexts more than they are used with the rest of that period's
vocabulary. The corpus is multilingual (English, Latin, Law French, Welsh,
Scots, and other languages appear untranslated) and pre-modern, so a
community sometimes groups words by shared grammatical form (verb
conjugations, comparatives, spelling variants), by shared foreign
language, or by proper names (people, places, biblical figures) rather
than by a real topic. In periods drawn from the British Library
supplement (1800 onward), a community can also be a cluster of OCR
fragments - words with a missing prefix or suffix from a misread hyphen
or scan artifact (e.g. "lumbus", "tagonist", "lemnly") - rather than
real words at all. All of these are honest and expected outcomes when
they are genuinely what the community is - not a failure to avoid, and
not something to force a topic onto just to avoid saying it. But they are
also the easy answer, reachable from the Core tier alone without ever
consulting Mid-rank or Peripheral - which is exactly why the reasoning
field below exists: a grammatical or foreign-language read has to survive
looking at all three tiers, not just the first one.

When the community below is shown as three tiers rather than one list:
Core words are this community's most structurally central members -
network hubs within it, the strongest signal of its dominant character.
Mid-rank and Peripheral words are progressively less central, down to the
least-connected members still assigned to this community by the
clustering. A community whose Core, Mid-rank, and Peripheral words all
point to the same theme (or the same grammatical/foreign-language/name
pattern) is genuinely coherent - label it with confidence. A community
whose Core looks coherent but whose Mid-rank or Peripheral words clearly
drift to something unrelated is a real, common outcome (a loose or
heterogeneous grouping, not a clustering error) - in that case label the
dominant theme the Core actually shows, but treat the drift itself as
evidence the community is not a clean single topic, and prefer
"Structural / Uncertain" unless the Core's theme is strong and specific
enough that a historian would still recognize it as the community's real
character despite the tail.

Before calling the tool, use the `reasoning` field to work through, in
order: (1) what the Core tier suggests on its own; (2) whether Mid-rank
and Peripheral support or undercut that reading - naming at least one
specific word from each tier, not just asserting they agree or disagree;
(3) only then, whether a genuine topic (something a historian would
recognize - law, medicine, religion, trade, and so on) holds across all
three, or whether the community is better described as grammatical,
foreign-language, or name-driven. A concrete, specific topical label
beats a structural one whenever the evidence in all three tiers actually
supports it - do not settle for "Base-Form Verbs" or similar just because
the words happen to share a grammatical form, if they also share a real
subject a historian would name. Reach for "Structural / Uncertain" only
after this reasoning shows no topic survives the Mid-rank/Peripheral
check, not as a first guess from the Core tier alone.

For the community below, decide:

1. Whether most of the words shown - across every tier given, not just
   the Core - share a genuine topical or thematic connection a historian
   would recognize (e.g. law, medicine, religion, trade), as opposed to
   being grouped mainly by grammar, a shared non-English language, or
   being mostly proper names or fragments.
2. A short plain-English label, two to five words, for what the community
   is actually about. If the grouping is grammatical, foreign-language, or
   name-driven rather than topical, the label should say what kind of
   cluster it is (e.g. "Second-Person Verb Forms", "Latin Text",
   "Biblical Names"), not force a topic onto it.
3. Exactly one lane from the fixed list above. Use "Structural / Uncertain"
   whenever the answer to (1) is no - do not stretch a grammatical,
   foreign-language, or name cluster into a thematic lane just because a
   few of its words are suggestive of one.

Call `assign_label` with `reasoning` first, then your answer. Do not
include any other text.

## User message template

Region: {region}
Period: {period}
Community size: {n_words} words
Sample of this community's member words, by network-degree rank
(most-connected first; grouped into Core/Mid-rank/Peripheral tiers if the
community is large enough to split, otherwise shown as one full list):

{top_words}
