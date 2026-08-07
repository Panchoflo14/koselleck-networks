# Community labeling prompt (v1)

Used by `src/label_communities.py generate`. One call per Leiden community.
Placeholders `{region}`, `{period}`, `{n_words}`, `{top_words}` are filled in
by the script before the call. The model must respond by calling the
`assign_label` tool - no free-text output is accepted.

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
real words at all. All of these are honest and expected outcomes, not a
failure to avoid.

For the community below, decide:

1. Whether most of the top words share a genuine topical or thematic
   connection a historian would recognize (e.g. law, medicine, religion,
   trade), as opposed to being grouped mainly by grammar, a shared
   non-English language, or being mostly proper names or fragments.
2. A short plain-English label, two to five words, for what the community
   is actually about. If the grouping is grammatical, foreign-language, or
   name-driven rather than topical, the label should say what kind of
   cluster it is (e.g. "Second-Person Verb Forms", "Latin Text",
   "Biblical Names"), not force a topic onto it.
3. Exactly one lane from the fixed list above. Use "Structural / Uncertain"
   whenever the answer to (1) is no - do not stretch a grammatical,
   foreign-language, or name cluster into a thematic lane just because a
   few of its words are suggestive of one.

Call `assign_label` with your answer. Do not include any other text.

## User message template

Region: {region}
Period: {period}
Community size: {n_words} words
Top {n_shown} highest-degree words in this community, most connected first:

{top_words}
