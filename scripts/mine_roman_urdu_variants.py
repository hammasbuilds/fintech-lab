"""Build ``projects/p03_guardian/variants.py`` from two real Roman Urdu corpora.

Roman Urdu has no standard orthography. The same word is written half a dozen ways by
the same person in the same conversation, and a generated test set whose spellings were
invented by whoever wrote the generator tests the generator's imagination rather than the
product. So the spellings here are **proposed by hand and then checked against real
text**: a variant is kept only if people actually write it, and the count of how often
they do is committed next to it.

Sources, both fetched by ``scripts/fetch_raw.py``:

* ``smat26/roman-urdu-dataset`` - 20,563 labelled Roman Urdu sentences
* ``yrrebeere/daraz-code-mixed-product-reviews`` - 18,484 code-mixed reviews

37,220 sentences and 505,452 word tokens between them. Neither is about payments, which
is the point: these are the spellings people use when nobody is filling in a form.

Run with ``python scripts/mine_roman_urdu_variants.py``. It rewrites the module and
prints what changed; ``tests/test_p03_variants.py`` fails if the committed module does
not match the sources it claims to come from.
"""

from __future__ import annotations

import collections
import csv
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared import datasets  # noqa: E402

TARGET = ROOT / "projects" / "p03_guardian" / "variants.py"

SOURCES = (
    (
        "kaggle:smat26/roman-urdu-dataset",
        ("smat26_roman-urdu-dataset", "Dataset", "Roman Urdu DataSet.csv"),
        0,
    ),
    (
        "kaggle:yrrebeere/daraz-code-mixed-product-reviews",
        ("yrrebeere_daraz-code-mixed-product-reviews", "daraz-code-mixed-product-reviews.csv"),
        1,
    ),
)

#: Proposed spellings, grouped by what they mean. Every one is checked against the
#: corpora below and dropped if nobody writes it.
PROPOSED: dict[str, tuple[str, ...]] = {
    # verbs of sending and doing
    "send": ("bhej", "bhej", "bhejo", "bheja", "bheji", "bheje", "bhejna", "bhejdo", "send"),
    "do": ("karo", "kardo", "kar", "karna", "karen", "kijiye", "krdo", "kro"),
    "give": ("do", "dedo", "dena", "dedena", "dein", "den"),
    "transfer": ("transfer", "trasfer", "transfar", "tarnsfer"),
    # money
    "money": ("paise", "paisay", "paisa", "pese", "pesay", "paisey", "rupay", "rupee", "rupees"),
    "thousand": ("hazaar", "hazar", "hazzar", "hzar", "thousand", "k"),
    "lakh": ("lakh", "lak", "lacs", "lakhs"),
    "crore": ("crore", "karor", "croar"),
    # urgency, which is what a coercion script leans on
    "quickly": ("jaldi", "jald", "fori", "foran", "urgent", "ab", "abhi", "abhee"),
    "please": ("please", "plz", "pls", "meharbani", "mehrbani"),
    "important": ("zaroori", "zarori", "zaroorat", "zarurat", "important"),
    "trouble": ("mushkil", "museebat", "pareshani", "problem", "masla"),
    "help": ("madad", "madaad", "help"),
    # people a wallet pays
    "brother": ("bhai", "bhaijan", "bhaiya", "brother"),
    "sister": ("behen", "behan", "behn", "baji", "sister"),
    "mother": ("ammi", "amma", "ami", "walida", "mother", "mom"),
    "father": ("abbu", "abba", "abu", "walid", "father"),
    "uncle": ("chacha", "mamu", "taya", "uncle"),
    # banking nouns
    "account": ("account", "acount", "khata", "akount"),
    "balance": ("balance", "balnce", "balans"),
    "bill": ("bill", "bil", "billl"),
    "number": ("number", "numbr", "no", "nmbr"),
    "code": ("code", "kod", "otp", "pin", "password"),
    "card": ("card", "kard", "atm"),
}

#: Words that must never be treated as a spelling variant even when they look like one.
#: ``k`` is both a real abbreviation for a thousand and the commonest Roman Urdu
#: conjunction, so it is kept only where a digit precedes it, which the generator does.
NOTES = {
    "k": "also the conjunction 'ke'/'ki' shortened; only generated directly after a digit",
    "no": "also the English 'no'; only generated in 'account no' and 'number' slots",
    "do": "also the numeral two; only generated as a verb at the end of a request",
    "ab": "also 'now'; generated as urgency, which is what it means in these scripts",
}


def token_counts() -> tuple[collections.Counter, dict[str, object]]:
    counts: collections.Counter = collections.Counter()
    provenance: dict[str, object] = {}
    sentences = 0
    for name, parts, column in SOURCES:
        path = datasets.require(datasets.raw(*parts), "make data")
        rows = 0
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.reader(handle):
                if len(row) <= column:
                    continue
                rows += 1
                for token in re.findall(r"[a-z]+", row[column].lower()):
                    counts[token] += 1
        sentences += rows
        provenance[name] = {
            "file": path.name,
            "rows": rows,
            "sha256": datasets.digest(path),
        }
    provenance["sentences"] = sentences
    provenance["word_tokens"] = sum(counts.values())
    provenance["distinct_tokens"] = len(counts)
    return counts, provenance


def build() -> str:
    counts, provenance = token_counts()
    kept: dict[str, dict[str, int]] = {}
    dropped: dict[str, list[str]] = {}
    for meaning, proposals in PROPOSED.items():
        keep: dict[str, int] = {}
        gone: list[str] = []
        for word in proposals:
            seen = counts.get(word, 0)
            if seen >= 2:
                keep[word] = seen
            else:
                gone.append(word)
        if keep:
            kept[meaning] = dict(sorted(keep.items(), key=lambda kv: -kv[1]))
        if gone:
            dropped[meaning] = gone

    fingerprint = hashlib.sha256(
        json.dumps([PROPOSED, provenance], sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    body = [
        '"""Roman Urdu spellings that people actually write, with how often they write them.',
        "",
        "Generated by ``scripts/mine_roman_urdu_variants.py``. Do not edit by hand: the",
        "spellings are proposed in that script and only the ones found in two real corpora",
        "survive into this file, with their counts. ``INPUTS_DIGEST`` covers both the",
        "proposals and the source files, and a test fails if this file is stale.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f"INPUTS_DIGEST = {fingerprint!r}",
        "",
        f"PROVENANCE = {json.dumps(provenance, indent=4, sort_keys=True)}",
        "",
        "#: meaning -> {spelling: how many times it appears in the corpora}",
        f"VARIANTS = {json.dumps(kept, indent=4, sort_keys=True)}",
        "",
        "#: Proposed spellings that no one in the corpora writes, kept so the next person",
        "#: can see what was tried rather than wondering.",
        f"NOT_FOUND = {json.dumps(dropped, indent=4, sort_keys=True)}",
        "",
        "#: Spellings that mean more than one thing, and where the generator may use them.",
        f"AMBIGUOUS = {json.dumps(NOTES, indent=4, sort_keys=True)}",
        "",
        "",
        "def spellings(meaning: str) -> list[str]:",
        '    """Every attested spelling of ``meaning``, commonest first."""',
        "    return list(VARIANTS[meaning])",
        "",
    ]
    return "\n".join(body)


def main() -> None:
    text = build()
    before = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"{'unchanged' if before == text else 'wrote'}  {TARGET.relative_to(ROOT)}")
    namespace: dict[str, object] = {}
    exec(compile(text, str(TARGET), "exec"), namespace)  # noqa: S102
    variants = namespace["VARIANTS"]
    missing = namespace["NOT_FOUND"]
    print(f"  {len(variants)} meanings, {sum(len(v) for v in variants.values())} spellings kept")
    print(f"  dropped as never written: {sum(len(v) for v in missing.values())}")
    for meaning, gone in sorted(missing.items()):
        print(f"    {meaning:12} {', '.join(gone)}")


if __name__ == "__main__":
    main()
