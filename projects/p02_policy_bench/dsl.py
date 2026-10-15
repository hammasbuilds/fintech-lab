"""The policy language: tokenizer, parser and abstract syntax tree.

A policy is a text file. It compiles to a tree, and the tree is what runs - so the
document the credit team argues over and the thing that decides applications cannot
drift apart, and two versions can be diffed as structure rather than as prose.

Every error carries a line number and says what was expected. A policy language whose
parser reports "syntax error" has failed at the only job that makes it better than a
spreadsheet.

Grammar, in full::

    policy      "Nano loan v3"
    version     3
    currency    PKR

    decline if  age < 18 because "under legal age"
    refer   if  prior_defaults == 1 because "one default on file, needs an officer"
    score  +25 if wallet_months >= 12 because "a year of wallet history"
    approve if  score >= 45
    limit       min(0.30 * monthly_income, 25000)
    fee         2.5% of limit
    late_fee    1% of limit per day

Conditions combine with ``and`` and ``or``; ``and`` binds tighter. Comparisons are
``< <= > >= == !=`` against a number or a quoted string. Limit expressions allow
``+ - * /``, parentheses, and ``min``/``max`` of two expressions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

KEYWORDS = {
    "policy",
    "version",
    "currency",
    "decline",
    "refer",
    "score",
    "approve",
    "limit",
    "fee",
    "late_fee",
    "if",
    "because",
    "and",
    "or",
    "of",
    "per",
    "min",
    "max",
}

COMPARATORS = ("<=", ">=", "==", "!=", "<", ">")

_TOKEN = re.compile(
    r"""
    (?P<space>[ \t]+)
  | (?P<comment>\#[^\n]*)
  | (?P<newline>\n)
  | (?P<string>"[^"\n]*")
  | (?P<percent>\d+(?:\.\d+)?%)
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<signed>[+-]\d+(?:\.\d+)?)
  | (?P<op><=|>=|==|!=|<|>|\(|\)|,|\*|/|\+|-)
  | (?P<word>[A-Za-z_][A-Za-z_0-9]*)
    """,
    re.VERBOSE,
)


class PolicyError(Exception):
    """A policy that will not compile. ``line`` is 1-based."""

    def __init__(self, message: str, line: int) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    line: int

    def __repr__(self) -> str:  # keeps parser errors readable
        return f"{self.kind}:{self.text!r}@{self.line}"


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    line = 1
    position = 0
    while position < len(source):
        match = _TOKEN.match(source, position)
        if match is None:
            raise PolicyError(f"cannot read {source[position]!r}", line)
        kind = match.lastgroup
        text = match.group()
        position = match.end()
        if kind == "newline":
            tokens.append(Token("newline", "\\n", line))
            line += 1
            continue
        if kind in {"space", "comment"}:
            continue
        if kind == "word":
            tokens.append(Token("keyword" if text in KEYWORDS else "name", text, line))
            continue
        if kind == "string":
            tokens.append(Token("string", text[1:-1], line))
            continue
        tokens.append(Token(kind, text, line))
    tokens.append(Token("end", "", line))
    return tokens


# ------------------------------------------------------------------------- AST


@dataclass(frozen=True)
class Comparison:
    field_name: str
    op: str
    value: float | str
    line: int

    def render(self) -> str:
        value = f'"{self.value}"' if isinstance(self.value, str) else _number(self.value)
        return f"{self.field_name} {self.op} {value}"


@dataclass(frozen=True)
class Combination:
    op: Literal["and", "or"]
    parts: tuple[Condition, ...]

    def render(self) -> str:
        return f" {self.op} ".join(part.render() for part in self.parts)


Condition = Comparison | Combination


@dataclass(frozen=True)
class Number:
    value: float

    def render(self) -> str:
        return _number(self.value)


@dataclass(frozen=True)
class FieldRef:
    name: str
    line: int

    def render(self) -> str:
        return self.name


@dataclass(frozen=True)
class BinOp:
    op: str
    left: Expr
    right: Expr

    def render(self) -> str:
        return f"({self.left.render()} {self.op} {self.right.render()})"


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[Expr, ...]

    def render(self) -> str:
        return f"{self.name}({', '.join(arg.render() for arg in self.args)})"


Expr = Number | FieldRef | BinOp | Call


@dataclass(frozen=True)
class Gate:
    outcome: Literal["decline", "refer"]
    condition: Condition
    reason: str
    line: int


@dataclass(frozen=True)
class ScoreRule:
    points: float
    condition: Condition
    reason: str
    line: int


@dataclass(frozen=True)
class Charge:
    """A fee. ``period`` is set only when the policy prices it per unit of time."""

    kind: Literal["fee", "late_fee"]
    rate: float | None
    base: str | None
    flat: float | None
    period: str | None
    line: int

    def render(self) -> str:
        if self.flat is not None:
            body = _number(self.flat)
        else:
            body = f"{_number((self.rate or 0) * 100)}% of {self.base}"
        return f"{self.kind} {body}" + (f" per {self.period}" if self.period else "")


@dataclass
class Policy:
    name: str = "untitled"
    version: str = "0"
    currency: str = "PKR"
    gates: list[Gate] = field(default_factory=list)
    score_rules: list[ScoreRule] = field(default_factory=list)
    approve_at: float | None = None
    limit: Expr | None = None
    charges: list[Charge] = field(default_factory=list)
    source: str = ""

    @property
    def fields_used(self) -> list[str]:
        names: set[str] = set()
        for gate in self.gates:
            names |= _condition_fields(gate.condition)
        for rule in self.score_rules:
            names |= _condition_fields(rule.condition)
        if self.limit is not None:
            names |= _expr_fields(self.limit)
        names.discard("score")
        return sorted(names)

    def render(self) -> str:
        """Print the policy back from the tree. A policy that does not round-trip is a
        policy whose text and behaviour have diverged, which is the whole thing this is
        meant to prevent."""
        lines = [
            f'policy "{self.name}"',
            f"version {self.version}",
            f"currency {self.currency}",
            "",
        ]
        for gate in self.gates:
            lines.append(f'{gate.outcome} if {gate.condition.render()} because "{gate.reason}"')
        if self.gates:
            lines.append("")
        for rule in self.score_rules:
            points = f"+{_number(rule.points)}" if rule.points >= 0 else _number(rule.points)
            text = f"score {points} if {rule.condition.render()}"
            if rule.reason:
                text += f' because "{rule.reason}"'
            lines.append(text)
        if self.score_rules:
            lines.append("")
        if self.approve_at is not None:
            lines.append(f"approve if score >= {_number(self.approve_at)}")
        if self.limit is not None:
            lines.append(f"limit {self.limit.render()}")
        for charge in self.charges:
            lines.append(charge.render())
        return "\n".join(lines) + "\n"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _condition_fields(condition: Condition) -> set[str]:
    if isinstance(condition, Comparison):
        return {condition.field_name}
    out: set[str] = set()
    for part in condition.parts:
        out |= _condition_fields(part)
    return out


def _expr_fields(expr: Expr) -> set[str]:
    if isinstance(expr, FieldRef):
        return {expr.name}
    if isinstance(expr, BinOp):
        return _expr_fields(expr.left) | _expr_fields(expr.right)
    if isinstance(expr, Call):
        out: set[str] = set()
        for arg in expr.args:
            out |= _expr_fields(arg)
        return out
    return set()


# ---------------------------------------------------------------------- parser


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    # -- token helpers --
    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.tokens[self.index]
        if token.kind != "end":
            self.index += 1
        return token

    def skip_newlines(self) -> None:
        while self.current.kind == "newline":
            self.advance()

    def expect(self, kind: str, text: str | None = None) -> Token:
        token = self.current
        if token.kind != kind or (text is not None and token.text != text):
            wanted = text or kind
            found = "end of policy" if token.kind == "end" else repr(token.text)
            raise PolicyError(f"expected {wanted}, found {found}", token.line)
        return self.advance()

    def at_keyword(self, *words: str) -> bool:
        return self.current.kind == "keyword" and self.current.text in words

    # -- grammar --
    def parse(self) -> Policy:
        policy = Policy()
        seen: set[str] = set()
        while True:
            self.skip_newlines()
            if self.current.kind == "end":
                break
            token = self.current
            if token.kind != "keyword":
                raise PolicyError(
                    f"expected a statement keyword such as decline, score or limit, "
                    f"found {token.text!r}",
                    token.line,
                )
            word = token.text
            if word in {"policy", "version", "currency", "approve", "limit"} and word in seen:
                raise PolicyError(f"{word} is already set earlier in this policy", token.line)
            seen.add(word)
            if word == "policy":
                self.advance()
                policy.name = self.expect("string").text
            elif word == "version":
                self.advance()
                policy.version = self.advance().text
            elif word == "currency":
                self.advance()
                policy.currency = self.expect("name").text.upper()
            elif word in {"decline", "refer"}:
                policy.gates.append(self.parse_gate())
            elif word == "score":
                policy.score_rules.append(self.parse_score())
            elif word == "approve":
                policy.approve_at = self.parse_approve()
            elif word == "limit":
                self.advance()
                policy.limit = self.parse_expr()
            elif word in {"fee", "late_fee"}:
                policy.charges.append(self.parse_charge())
            else:
                raise PolicyError(f"{word!r} cannot start a statement", token.line)
            self.end_of_statement()
        self.validate(policy)
        return policy

    def end_of_statement(self) -> None:
        if self.current.kind in {"newline", "end"}:
            return
        raise PolicyError(
            f"unexpected {self.current.text!r} after the end of the statement",
            self.current.line,
        )

    def parse_gate(self) -> Gate:
        token = self.advance()
        outcome = token.text
        self.expect("keyword", "if")
        condition = self.parse_condition()
        self.expect("keyword", "because")
        reason = self.expect("string").text
        if not reason.strip():
            raise PolicyError("a gate needs a reason the applicant can be given", token.line)
        return Gate(outcome, condition, reason, token.line)  # type: ignore[arg-type]

    def parse_score(self) -> ScoreRule:
        token = self.advance()
        points_token = self.current
        if points_token.kind == "signed" or points_token.kind == "number":
            self.advance()
            points = float(points_token.text)
        else:
            raise PolicyError(
                f"score needs a signed number such as +25 or -10, found {points_token.text!r}",
                points_token.line,
            )
        self.expect("keyword", "if")
        condition = self.parse_condition()
        reason = ""
        if self.at_keyword("because"):
            self.advance()
            reason = self.expect("string").text
        return ScoreRule(points, condition, reason, token.line)

    def parse_approve(self) -> float:
        token = self.advance()
        self.expect("keyword", "if")
        # ``score`` is a statement keyword as well as the name of the running total, so
        # it arrives here as a keyword token rather than a name.
        name = self.advance()
        if name.text != "score":
            raise PolicyError(
                f"approve compares the score, not {name.text!r}; write 'approve if score >= 45'",
                name.line,
            )
        op = self.expect("op")
        if op.text not in {">=", ">"}:
            raise PolicyError(
                f"approve uses >= or >, not {op.text!r} - a policy that approves *below* a "
                f"score is almost always a typo",
                op.line,
            )
        threshold = float(self.expect("number").text)
        del token
        return threshold

    def parse_charge(self) -> Charge:
        token = self.advance()
        kind = token.text
        rate: float | None = None
        base: str | None = None
        flat: float | None = None
        if self.current.kind == "percent":
            rate = float(self.advance().text.rstrip("%")) / 100
            self.expect("keyword", "of")
            base_token = self.current
            if base_token.kind == "keyword" and base_token.text == "limit":
                self.advance()
                base = "limit"
            else:
                base = self.expect("name").text
        elif self.current.kind == "number":
            flat = float(self.advance().text)
        else:
            raise PolicyError(
                f"{kind} takes a percentage such as 2.5% of limit, or a flat amount",
                self.current.line,
            )
        period: str | None = None
        if self.at_keyword("per"):
            self.advance()
            period = self.expect("name").text
        return Charge(kind, rate, base, flat, period, token.line)  # type: ignore[arg-type]

    # -- conditions --
    def parse_condition(self) -> Condition:
        parts = [self.parse_and()]
        while self.at_keyword("or"):
            self.advance()
            parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else Combination("or", tuple(parts))

    def parse_and(self) -> Condition:
        parts = [self.parse_comparison()]
        while self.at_keyword("and"):
            self.advance()
            parts.append(self.parse_comparison())
        return parts[0] if len(parts) == 1 else Combination("and", tuple(parts))

    def parse_comparison(self) -> Comparison:
        name = self.current
        if name.kind != "name":
            raise PolicyError(
                f"a condition starts with an applicant field, found {name.text!r}", name.line
            )
        self.advance()
        op = self.current
        if op.kind != "op" or op.text not in COMPARATORS:
            raise PolicyError(
                f"expected one of {' '.join(COMPARATORS)} after {name.text!r}, found {op.text!r}",
                op.line,
            )
        self.advance()
        value_token = self.current
        if value_token.kind == "number":
            self.advance()
            value: float | str = float(value_token.text)
        elif value_token.kind == "string":
            self.advance()
            value = value_token.text
            if op.text not in {"==", "!="}:
                raise PolicyError(
                    f"{op.text} cannot compare text; use == or != for {value!r}", op.line
                )
        else:
            raise PolicyError(
                f"expected a number or a quoted value, found {value_token.text!r}",
                value_token.line,
            )
        return Comparison(name.text, op.text, value, name.line)

    # -- expressions --
    def parse_expr(self) -> Expr:
        left = self.parse_term()
        while self.current.kind == "op" and self.current.text in {"+", "-"}:
            op = self.advance().text
            left = BinOp(op, left, self.parse_term())
        return left

    def parse_term(self) -> Expr:
        left = self.parse_atom()
        while self.current.kind == "op" and self.current.text in {"*", "/"}:
            op = self.advance().text
            left = BinOp(op, left, self.parse_atom())
        return left

    def parse_atom(self) -> Expr:
        token = self.current
        if token.kind == "number":
            self.advance()
            return Number(float(token.text))
        if token.kind == "percent":
            self.advance()
            return Number(float(token.text.rstrip("%")) / 100)
        if token.kind == "signed":
            self.advance()
            return Number(float(token.text))
        if token.kind == "keyword" and token.text in {"min", "max"}:
            self.advance()
            self.expect("op", "(")
            first = self.parse_expr()
            self.expect("op", ",")
            second = self.parse_expr()
            self.expect("op", ")")
            return Call(token.text, (first, second))
        if token.kind == "op" and token.text == "(":
            self.advance()
            inner = self.parse_expr()
            self.expect("op", ")")
            return inner
        if token.kind == "name":
            self.advance()
            return FieldRef(token.text, token.line)
        raise PolicyError(f"cannot read an amount from {token.text!r}", token.line)

    # -- whole-policy checks --
    def validate(self, policy: Policy) -> None:
        last = self.tokens[-1].line
        if policy.approve_at is None:
            raise PolicyError(
                "the policy never says when to approve; add 'approve if score >= <n>'", last
            )
        if policy.limit is None:
            raise PolicyError("the policy never sets a limit; add 'limit <expression>'", last)
        if not policy.score_rules and policy.approve_at > 0:
            raise PolicyError(
                f"approve requires a score of {_number(policy.approve_at)} but the policy "
                f"awards no points, so nothing can ever be approved",
                last,
            )
        reachable = sum(rule.points for rule in policy.score_rules if rule.points > 0)
        if policy.approve_at > reachable:
            raise PolicyError(
                f"approve requires {_number(policy.approve_at)} points but at most "
                f"{_number(reachable)} can be awarded, so nothing can ever be approved",
                last,
            )


def compile_policy(source: str) -> Policy:
    """Parse ``source`` into a policy. Raises :class:`PolicyError` with a line number."""
    policy = Parser(tokenize(source)).parse()
    policy.source = source
    return policy
