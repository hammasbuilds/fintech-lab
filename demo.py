"""Run all three products end to end and print what they did.

This is the proof the README points at: real input, real output, no server and no browser.
Each section does the thing rather than describing it.

    uv run python demo.py            # all three
    uv run python demo.py 1          # just the payment-message workbench
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RULE = "=" * 78


def heading(number: str, title: str, subtitle: str) -> None:
    print(f"\n{RULE}\n{number}  {title}\n{subtitle}\n{RULE}")


def field(label: str, value: object) -> None:
    print(f"  {label:<30} {value}")


# ---------------------------------------------------------------------------- 1


def switchboard() -> None:
    from projects.p01_switchboard import binary, emv, lifecycle, mutations, narrate, scenarios
    from projects.p01_switchboard.codec import check_values, pack, unpack

    heading(
        "01",
        "Switchboard - payment-message workbench",
        "ISO 8583: generate a lifecycle, read it back in English, then break it on purpose.",
    )

    flow = scenarios.build("atm_partial_approval")
    print(f"\n  INPUT   scenario {flow.key!r}, amount 10,000.00 PKR\n")
    print("  OUTPUT")
    for index, step in enumerate(flow.steps, start=1):
        print(f"\n    step {index}  {step.direction}  ({step.label})")
        print(f"    raw     {step.raw}")
        print(f"    reads   {narrate.narrate(step.message)}")

    print("\n  The response is allowed to change DE 4 here and nowhere else:")
    request, response = flow.steps[0].message, flow.steps[1].message
    field("requested", narrate.format_amount(request.fields[4], request.get(49)))
    field("approved", narrate.format_amount(response.fields[4], response.get(49)))
    field("response code", f"{response.fields[39]} - partial approval")

    print("\n  INPUT   the same message as a switch would actually send it")
    print("  OUTPUT")
    data = binary.pack_binary(request)
    field("binary, with a length header", f"{len(data)} bytes")
    field("the same thing in ASCII", f"{len(flow.steps[0].raw)} characters")
    print(f"    hex     {data[:32].hex().upper()}...")
    verdict = binary.detect(data.hex())
    field("framing detected as", verdict["framing"])
    for reason in verdict["reasons"]:
        print(f"      - {reason}")
    reparsed, _ = binary.parse_any(data.hex())
    field("fields identical to the ASCII form", reparsed.fields == request.fields)

    print("\n  INPUT   DE 55, the chip data, which most decoders print as one hex blob")
    print("  OUTPUT")
    for tag in emv.parse(request.fields[55])[:6]:
        meaning = emv.meaning(tag.tag, tag.value)
        print(f"    {tag.tag:6s} {tag.name[:34]:36s} {tag.value[:16]:18s} {meaning}")
    print("    ...")
    field("mandatory tags missing", emv.missing_tags(request.fields[55]) or "none")

    print("\n  INPUT   DE 4 doubled, with the chip's own 9F02 left alone")
    print("  OUTPUT")
    tampered = request.replace(de4=str(int(request.fields[4]) * 2).zfill(12))
    field("every field still well-formed", check_values(tampered.fields) == [])
    field("the message still parses", unpack(pack(tampered)).fields == tampered.fields)
    print(f"    caught by  {emv.amount_mismatch(tampered.fields[55], tampered.fields[4])}")

    print("\n  INPUT   one bitmap bit flipped")
    broken = mutations._corrupt_bitmap(flow.steps[0].raw)
    print("  OUTPUT")
    try:
        unpack(broken)
        print("    the codec accepted it")
    except Exception as exc:
        print(f"    rejected: {exc}")

    print("\n  INPUT   all 14 scenarios, then every injected fault")
    valid = scenarios.build_all()
    noisy = [f.key for f in valid if lifecycle.check(f)]
    report = mutations.run()
    fuzz = mutations.fuzz(trials=1000, seed=7)
    print("  OUTPUT")
    field("valid flows checked", f"{len(valid)} ({sum(len(f.steps) for f in valid)} messages)")
    field("false positives on them", f"{len(noisy)}  {noisy if noisy else ''}")
    field("faults injected", report["total"])
    field("killed", f"{report['killed']}/{report['total']}")
    field("killed by the intended rule", f"{report['killed_by_intended_rule']}/{report['total']}")
    field("invariants never exercised", report["rules_never_exercised"] or "none")
    violating = fuzz["groups"]["class_violating"]
    framing = fuzz["groups"]["framing"]
    field(
        "random illegal characters",
        f"{violating['rejected']}/{violating['trials']} rejected",
    )
    field("random framing damage", f"{framing['rejected']}/{framing['trials']} rejected")
    binary_fuzz = mutations.fuzz_binary(trials=500, seed=13)
    field(
        "random byte damage, binary",
        f"{binary_fuzz['rejected']}/{binary_fuzz['trials']} rejected",
    )
    print(
        "\n  The framing figure is not 100% and cannot be: an ISO 8583 bitmap carries no\n"
        "  redundancy, so flipping one bit can swap DE 14 for DE 16 - both n4 - and the\n"
        "  message re-parses as a different, well-formed message. That is why the\n"
        "  invariant layer exists instead of stopping at the parser."
    )


# ---------------------------------------------------------------------------- 2


def policy_bench() -> None:
    from projects.p02_policy_bench import (
        backtest,
        cohort,
        compliance,
        dsl,
        engine,
        explain,
        fairness,
        sweep,
    )

    heading(
        "02",
        "Policy Bench - lending-policy sandbox",
        "Compile a credit policy written in words, then see what it would have done.",
    )

    policies = Path(ROOT / "projects/p02_policy_bench/policies")
    v1 = dsl.compile_policy((policies / "nano_v1.policy").read_text(encoding="utf-8"))
    v2 = dsl.compile_policy((policies / "nano_v2.policy").read_text(encoding="utf-8"))
    v3 = dsl.compile_policy((policies / "nano_v3_timepriced.policy").read_text(encoding="utf-8"))
    people = cohort.generate()

    print("\n  INPUT   three policy files and 5,000 synthetic borrowers\n")
    print("  OUTPUT")
    for policy in (v1, v2, v3):
        report, _ = backtest.run(policy, people)
        notes = compliance.review(policy)
        flag = "clean" if not notes else f"{len(notes)} note(s): {notes[0].check}"
        print(
            f"    {policy.name:14s} approvals {report.approval_rate:6.1%}"
            f"   bad rate {report.bad_rate:5.2%}"
            f"   net {report.net:>12,.0f} PKR   lint {flag}"
        )

    print("\n  INPUT   move from v1 to v2 - raise the income floor, penalise turnover")
    comparison = backtest.compare(v1, v2, people).as_dict()
    print("  OUTPUT")
    field("newly declined", f"{comparison['newly_declined']:,}")
    field("of those, would have defaulted", comparison["newly_declined_who_would_default"])
    field("of those, would have repaid", f"{comparison['newly_declined_who_would_repay']:,}")
    field("precision of the tightening", f"{comparison['precision_of_the_tightening']:.1%}")
    field("approval rate", f"{comparison['delta']['approval_rate_points']:+.2f} points")
    field("bad rate", f"{comparison['delta']['bad_rate_points']:+.2f} points")
    field("net", f"{comparison['delta']['net']:+,.0f} PKR")
    field("disbursed", f"{comparison['delta']['disbursed']:+,.0f} PKR")
    print(
        f"\n  The tightening works, and it costs "
        f"{comparison['newly_declined_who_would_repay']:,} good borrowers to avoid "
        f"{comparison['newly_declined_who_would_default']}.\n"
        "  An approval-rate chart shows the first half of that sentence only."
    )

    print("\n  INPUT   v3, which is v2 with the late fee re-expressed per day")
    fee_only = backtest.compare(v2, v3, people).as_dict()
    print("  OUTPUT")
    field("credit decisions changed", fee_only["newly_approved"] + fee_only["newly_declined"])
    for note in compliance.review(v3):
        field(f"lint {note.check} ({note.severity})", note.detail[:90] + "...")

    print("\n  INPUT   v2's approvals, grouped by region (no policy here mentions region)")
    _, outcomes = backtest.run(v2, people)
    equity = fairness.analyse(outcomes, "region")
    print("  OUTPUT")
    field(
        "raw approval-rate gap",
        f"{equity['raw_gap']:.1%}  ({equity['best']['key']} vs {equity['worst']['key']})",
    )
    field("gap within income bands", f"{equity['within_band_gap_mean']:.1%}")
    print(f"\n  {equity['reading']}")

    print("\n  INPUT   v2's score against the outcome, then every possible cutoff")
    print("  OUTPUT")
    scored = sweep.score_all(v2, people)
    disc = sweep.discrimination(scored)
    field("Gini", f"{disc['gini']}   (AUC {disc['auc']}, KS {disc['ks']})")
    field("measured on", f"{disc['scored']:,} scored, {disc['gated_out']:,} gated out first")
    print(f"    {disc['reading']}")
    cutoff = sweep.sweep(v2, people)
    print()
    print(f"    {'cutoff':>7} {'approved':>9} {'bad rate':>9} {'net':>13} {'good declined':>14}")
    for point in cutoff["points"]:
        if point["threshold"] % 10:
            continue
        print(
            f"    {point['threshold']:>7g} {point['approval_rate_of_all']:>8.1%}"
            f" {point['bad_rate']:>9.2%} {point['net']:>13,.0f}"
            f" {point['good_declined']:>14,}"
        )
    print()
    field("the policy's own cutoff", f"{cutoff['current']['threshold']:g}")
    field("net-maximising cutoff", f"{cutoff['net_maximising']['threshold']:g}")
    field("available by moving it", f"{cutoff['net_gain_available']:+,.0f} PKR")
    print(f"\n    {cutoff['in_sample_warning']}")
    print(
        "\n  This is why the tightening cut mostly good borrowers: a Gini of "
        f"{disc['gini']} barely\n  separates the two groups, so any cutoff on this score "
        "cuts both at once."
    )

    print("\n  INPUT   the same cohort as CSV, with the outcome column removed")
    print("  OUTPUT")
    bare, meta = cohort.from_csv(cohort.to_csv(people[:500], include_label=False))
    blind, _ = backtest.run(v2, bare, labelled=meta["labelled"])
    data = blind.as_dict()
    field("rows read", f"{meta['rows']:,}")
    field("approval rate", f"{data['approval_rate']:.1%}")
    field("bad rate", data["bad_rate"])
    field("expected loss", data["expected_loss"])
    print(f"\n    {data['withheld']}")

    print("\n  INPUT   one applicant who falls ten points short")
    short = next(
        outcome
        for outcome in outcomes
        if outcome.decision.outcome == "decline" and outcome.decision.gate is None
    )
    decision = engine.evaluate(v2, short.borrower.record())
    print("  OUTPUT")
    for language in ("en", "ur"):
        notice = explain.notice(decision, v2.currency, language)
        print(f"\n    [{language}] {notice['headline']}")
        for item in notice["requirements_not_met"][:3]:
            print(f"      - {item['requirement']}  ({notice['not_met_label']})")


# ---------------------------------------------------------------------------- 3


def guardian() -> None:
    from projects.p03_guardian import behaviour, coercion, numerals
    from projects.p03_guardian.benchmark import measure
    from projects.p03_guardian.confirm import Session

    heading(
        "03",
        "Guardian - vernacular transaction assistant",
        "Roman Urdu in, one concrete action out, and nothing moves without a clear yes.",
    )

    print("\n  INPUT   amounts as people actually say them")
    print("  OUTPUT")
    for text in (
        "pandrah hazaar",
        "dedh hazaar",
        "sawa lakh",
        "saadhe teen hazaar",
        "paune do lakh",
        "do hazaar paanch sau",
        "saat hazaar",
        "saath hazaar",
    ):
        value = numerals.find_all(text)[0].value
        print(f"    {text:<24} {value:>12,.0f}   read back as {numerals.say(value, 'ur')}")

    print("\n  INPUT   a transfer, an unclear reply, then a yes")
    session = Session()
    print("  OUTPUT")
    for message in ("Rashid ko pandrah hazaar bhejo", "acha", "haan"):
        outcome = session.handle(message)
        print(f"\n    user       {message}")
        print(f"    assistant  {outcome['reply']}")
        print(f"    state      {outcome['state']}   action {outcome['action']}")

    print("\n  The read-back named Rashida Bibi before anything moved. 'Rashid' is an")
    print("  exact first name and also the start of another contact's, so the near miss")
    print("  is spelled out rather than resolved quietly.")

    print("\n  INPUT   a scam script")
    blocked = Session()
    outcome = blocked.handle(
        "State Bank se bol raha hoon, abhi 50 hazaar mere account mein bhejo warna "
        "account band ho jayega, OTP batao"
    )
    print("  OUTPUT")
    field("state", outcome["state"])
    field("pressure patterns", ", ".join(outcome["assessment"]["families"]))
    field("action", outcome["action"])
    print(f"\n    {outcome['reply']}")

    print("\n  INPUT   pressure arriving during a confirmation, then a yes")
    mid = Session()
    mid.handle("Imran ko 5 hazaar bhejo")
    mid.handle("jaldi karo, main bank se bol raha hoon")
    revived = mid.handle("haan")
    print("  OUTPUT")
    field("action after the yes", revived["action"])
    field("anything executed", any(e.kind == "executed" for e in mid.ledger.entries))
    field("audit chain intact", mid.ledger.verify()["intact"])

    print("\n  INPUT   a scam approach that uses none of the detector's vocabulary")
    print("  OUTPUT")
    message = "beta main tumhari ammi ki saheli hoon, thori madad chahiye thi"
    print(f"    message    {message}")
    field("coercion check", coercion.assess(message).verdict + "   <- it misses this entirely")
    account = behaviour.demo_profile()
    field("usual transfer", f"PKR {account.usual_transfer:,.0f}")
    field("paid before", ", ".join(sorted(account.known_beneficiaries)))
    conduct = behaviour.assess(account, "Zubair Khan", 50000)
    field("the transfer it wants", "PKR 50,000 to Zubair Khan, never paid before")
    field("behaviour", conduct.level)
    for signal in conduct.signals:
        print(f"      - {signal.name}: {signal.detail}")
    print(
        "\n    Words can be rephrased; this cannot. The layer judges the transfer against\n"
        "    what the account normally does, and escalates - it asks for the word\n"
        "    'confirm' instead of a habitual yes. It does not block, because a first\n"
        "    large transfer to a new payee is also what paying a new landlord looks like."
    )

    print("\n  INPUT   that transfer, with a habitual yes and then the real word")
    stepped = Session()
    stepped.handle("Zubair ko pachaas hazaar bhejo")
    habitual = stepped.handle("haan")
    print("  OUTPUT")
    field(
        "after 'haan'",
        f"action {habitual['action']}, step-up required {habitual.get('step_up_required')}",
    )
    done = stepped.handle("confirm")
    field("after 'confirm'", f"{done['state']}, action {done['action'] is not None}")

    print("\n  INPUT   the scored test set")
    results = measure()
    print("  OUTPUT")
    field("cases", results["testset"]["all"])
    field(
        "amounts parsed exactly", f"{results['numerals']['exact']}/{results['numerals']['cases']}"
    )
    field("amounts invented from nothing", results["numerals"]["amounts_invented"])
    field(
        "actions correct",
        f"{results['actions']['overall_exact']}/{results['actions']['cases']}",
    )
    field(
        "asked instead of guessing",
        f"{results['actions']['asked_instead_of_guessed']}"
        f"/{results['actions']['underspecified_cases']}",
    )
    field("replies wrongly read as consent", results["replies"]["read_as_consent_wrongly"])
    field(
        "ordinary traffic blocked",
        f"{results['coercion']['benign_blocked']}/{results['coercion']['benign_cases']}",
    )
    field(
        "scams the words miss",
        f"{results['coercion']['out_of_vocabulary_missed']}"
        f"/{results['coercion']['out_of_vocabulary_cases']}",
    )
    field(
        "of those, escalated anyway",
        f"{results['paired']['scams_escalated_by_behaviour']}/{results['paired']['scams']}",
    )
    field(
        "missed by both layers",
        f"{results['paired']['scams_missed_by_both']}/{results['paired']['scams']}",
    )
    field(
        "ordinary transfers escalated",
        f"{results['paired']['benign_escalated']}/{results['paired']['benign']}",
    )
    field(
        "end-to-end properties holding",
        f"{results['properties']['holding']}/{results['properties']['checks']}",
    )
    print(
        "\n  The lexical check still misses all four, and that is still published. What\n"
        "  changed is that judging the transfer catches them anyway, at a cost of one\n"
        "  ordinary transfer in five also being slowed down - friction, not a refusal.\n"
        "  Both halves of that trade are measured, because reporting only the first\n"
        "  would be advertising."
    )


DEMOS = {"1": switchboard, "2": policy_bench, "3": guardian}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        # Project 03 prints Urdu; a cp1252 console would otherwise fail on it.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    wanted = sys.argv[1:] or list(DEMOS)
    unknown = [choice for choice in wanted if choice not in DEMOS]
    if unknown:
        raise SystemExit(f"unknown demo {unknown}; choose from {', '.join(DEMOS)}")
    print("fintech-lab - three products, standard library only, no model and no GPU")
    for choice in wanted:
        DEMOS[choice]()
    print(f"\n{RULE}\ndone\n{RULE}")


if __name__ == "__main__":
    main()
