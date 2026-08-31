# Label-fit check prompt (v1)

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
words shown are this community's own current top words, not the
predecessor's.

Judge only one thing: given the words actually listed below, does the
label still describe them well enough that a reader would not be misled?
Say no if the words have visibly drifted to a different topic, a
different grammatical form, a different language, or a different kind of
name-driven or fragment-driven cluster than the label describes - even if
the drift is partial, not total. Say yes if the words are still a
reasonable match, even if you would have worded the label slightly
differently yourself; this is a fit check, not a rewrite request.

Call `label_still_fits` with your answer and one sentence explaining it.
Do not include any other text.

## User message template

Region: {region}
Period: {period}
Inherited label: {label}
Community size: {n_words} words
Top {n_shown} highest-degree words in this community, most connected first:

{top_words}
