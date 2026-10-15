"""Measure the workbench and write ``results.json``.

Four things are counted, in increasing order of how much they tell you:

1. dictionary coverage and scenario count - size, not quality;
2. the round-trip property over every generated message - the codec agrees with itself;
3. the eighteen invariants over every valid flow - the checker does not cry wolf;
4. the mutation kill rate - the checker catches faults it has not been shown.

Only the fourth is evidence. The first three are the conditions under which the fourth
means anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import fields as F
from . import lifecycle, mutations, scenarios
from .codec import pack, round_trip, unpack

RESULTS = Path(__file__).with_name("results.json")


def measure() -> dict[str, Any]:
    flows = scenarios.build_all()
    messages = [message for flow in flows for message in flow.messages]

    round_trips = sum(1 for message in messages if round_trip(message))
    reparsed_equal = sum(
        1 for message in messages if unpack(pack(message)).fields == message.fields
    )

    flow_findings = {flow.key: [f.as_dict() for f in lifecycle.check(flow)] for flow in flows}
    noisy = {key: found for key, found in flow_findings.items() if found}

    mutation = mutations.run()

    elements_used = sorted({de for message in messages for de in message.present})

    return {
        "dictionary": {
            "elements_defined": len(F.DICTIONARY),
            "elements_exercised": len(elements_used),
            "exercised": elements_used,
        },
        "scenarios": {
            "count": len(flows),
            "messages": len(messages),
            "steps_per_flow": {flow.key: len(flow.steps) for flow in flows},
        },
        "codec": {
            "messages_checked": len(messages),
            "round_trip_stable": round_trips,
            "reparse_equal": reparsed_equal,
        },
        "invariants": {
            "rules": len(lifecycle.RULES),
            "valid_flows_checked": len(flows),
            "valid_flows_with_findings": len(noisy),
            "false_positives": noisy,
        },
        "fuzz": {
            **mutations.fuzz(),
            "note": (
                "The framing group does not reach 1.000 and cannot. Seven of the eight "
                "accepted corruptions turned a valid MTI into another valid MTI, which a "
                "parser must accept. The eighth flipped one bitmap bit and swapped DE 14 "
                "for DE 16 - both n4, so the layout is byte-identical and the message "
                "re-parses as a different, entirely well-formed message. An ISO 8583 "
                "bitmap carries no redundancy, so no parser can catch that. It is the "
                "reason the invariant layer exists rather than stopping at the codec."
            ),
        },
        "mutation": {
            "mutants": mutation["total"],
            "killed": mutation["killed"],
            "killed_by_intended_rule": mutation["killed_by_intended_rule"],
            "survived": mutation["survived"],
            "rules_never_exercised": mutation["rules_never_exercised"],
            "rows": mutation["rows"],
        },
    }


def _pct(part: int, whole: int) -> str:
    return "n/a" if whole == 0 else f"{100 * part / whole:.1f}%"


def report(results: dict[str, Any]) -> str:
    d, s, c = results["dictionary"], results["scenarios"], results["codec"]
    inv, mut, fz = results["invariants"], results["mutation"], results["fuzz"]
    groups = fz["groups"]
    violating = groups["class_violating"]
    lines = [
        "Switchboard - payment-message workbench",
        "",
        f"  data elements defined        {d['elements_defined']}",
        f"  data elements exercised      {d['elements_exercised']}",
        f"  scenarios                    {s['count']}",
        f"  messages generated           {s['messages']}",
        "",
        f"  round trip stable            {c['round_trip_stable']}/{c['messages_checked']}"
        f"  ({_pct(c['round_trip_stable'], c['messages_checked'])})",
        f"  reparse equals original      {c['reparse_equal']}/{c['messages_checked']}",
        "",
        f"  invariants declared          {inv['rules']}",
        f"  valid flows checked          {inv['valid_flows_checked']}",
        f"  valid flows with findings    {inv['valid_flows_with_findings']}"
        "   (a finding on valid traffic is a false positive)",
        "",
        f"  mutants injected             {mut['mutants']}",
        f"  killed                       {mut['killed']}/{mut['mutants']}"
        f"  ({_pct(mut['killed'], mut['mutants'])})",
        f"  killed by the intended rule  {mut['killed_by_intended_rule']}/{mut['mutants']}"
        f"  ({_pct(mut['killed_by_intended_rule'], mut['mutants'])})",
        f"  mutants that survived        {', '.join(mut['survived']) or 'none'}",
        f"  rules never exercised        {', '.join(mut['rules_never_exercised']) or 'none'}",
        "",
        f"  random corruptions           {fz['trials']}",
        "    illegal for the field      "
        f"{groups['class_violating']['rejected']}/{groups['class_violating']['trials']} "
        f"rejected  ({_pct(violating['rejected'], violating['trials'])})",
        "    legal for the field        "
        f"{groups['class_legal']['trials'] - groups['class_legal']['rejected']}"
        f"/{groups['class_legal']['trials']} accepted, as they should be",
        "    in the framing             "
        f"{groups['framing']['rejected']}/{groups['framing']['trials']} rejected  "
        f"({_pct(groups['framing']['rejected'], groups['framing']['trials'])})",
        f"    illegal characters let through  {len(fz['illegal_characters_accepted'])}",
    ]
    return "\n".join(lines)


def main() -> None:
    results = measure()
    RESULTS.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(report(results))
    inside = RESULTS.is_relative_to(Path.cwd())
    print(f"\nwritten to {RESULTS.relative_to(Path.cwd()) if inside else RESULTS}")


if __name__ == "__main__":
    main()
