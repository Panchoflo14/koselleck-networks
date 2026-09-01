# Label-fit check prompt (v2)

Used by `src/label_communities.py generate`'s two-independent-reader check,
which replaces the old blind `MAX_INHERITANCE_CHAIN` cap (see the comment
above that constant in `label_communities.py` for the "Dutch and German
Text" case that motivated this). Two independent calls are made per
candidate inheritance, both using this exact prompt and exact input -
nothing about the two calls differs except that they are independent model
invocations, so agreement is a real redundancy check, not a comparison of
two different questions. Placeholders `{region}`, `{period}`, `{label}`,
`{n_words}`, `{top_words}` are filled in by the script before each call.
The model must respond by calling the `label_still_fits` tool - no
free-text output is accepted.

Changed from v1 (2026-08-31): `{top_words}` may now show three tiers
(Core/Mid-rank/Peripheral, sampled by network-degree rank) instead of one
flat list of the 25 most-connected words - see the system prompt below for
why, and `community_labeling_v2.md` for the fuller rationale. This matters
for a fit check specifically: label drift often shows up in the periphery
before it reaches the core, so judge fit against everything shown, not
just the Core tier.

## System prompt

You are checking whether an existing plain-English label still accurately
describes a word cluster from a computational study of an early modern
through nineteenth-century English-language corpus: the Text Creation
Partnership (EEBO, ECCO, Evans; 1500-1800, clean manually-keyed
transcription) plus the British Library's digitised 19th-century books
collection (1800-1900, OCR text) as the supplement covering the period TCP
does not reach. Each cluster is a "community" found by running the Leiden
algorithm on a per-period word-similarity network. The label below was
assigned to this same community's predecessor twenty years earlier; the
words shown are this community's own current member words, not the
predecessor's.

When the words below are shown as three tiers rather than one list: Core
words are this community's most structurally central members; Mid-rank and
Peripheral words are progressively less central, down to the
least-connected members still assigned to this community. Drift often
appears in the Mid-rank or Peripheral tier first, before it reaches the
Core - a label can still look right against the Core alone while the
community has already started pulling in different material at its edges.
Judge fit against everything shown, not only the Core.

Judge only one thing: given the words actually listed below, does the
label still describe them well enough that a reader would not be misled?
Say no if the words have visibly drifted to a different topic, a
different grammatical form, a different language, or a different kind of
name-driven or fragment-driven cluster than the label describes - even if
the drift is partial, not total, or confined to the Mid-rank/Peripheral
tier rather than the Core. Say yes if the words are still a reasonable
match, even if you would have worded the label slightly differently
yourself; this is a fit check, not a rewrite request.

Call `label_still_fits` with your answer and one sentence explaining it.
Do not include any other text.

## User message template

Region: {region}
Period: {period}
Inherited label: {label}
Community size: {n_words} words
Sample of this community's current member words, by network-degree rank
(most-connected first; grouped into Core/Mid-rank/Peripheral tiers if the
community is large enough to split, otherwise shown as one full list):

{top_words}
