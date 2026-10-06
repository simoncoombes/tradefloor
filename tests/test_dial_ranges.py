"""Every dial's refusal states the set of values the validator accepts.

The parameter reference on the docs site reads each settable dial's range
from the validator's own refusal, offering the dial a fixed ladder of values
on the default preset and reading the text of what it refuses. A message that
names a range the validator does not keep publishes a wrong range. Issue #248
found four: `impact_memory_coefficient` said [0, 10] and refused 2,
`market_day_tail_df` and `overnight_idio_df` said [3, 200] and [3, 30] in a
form that hid that 0 is accepted, and `rate_close_remark` said 0 or 1 and
refused 0 on pt-v21.

This file reads the refusals the same way, with the same grammar, and holds
the reading to what the validator does: at the ladder's values and at the
edges of the range the message states, just inside and just outside each.
A refusal that names another dial first (`OTHER is v but NAME ...`, or
`OTHER is v, and ...`) belongs to that dial's rule, as the reference
records it, and is not held against this one.
"""

from __future__ import annotations

import math
import re

import pytest

import tradefloor as tf

#: The values each dial is offered, as the reference offers them.
LADDER = (-1e12, -1.0, -0.5, -1e-9, 0.0, 1e-9, 0.5, 1.0, 2.0, 1e12)

_NUM = r"-?[0-9]+(?:\.[0-9]+)?(?:e[-+]?[0-9]+)?"
_INTERVAL = rf"[\[(]\s*{_NUM}\s*,\s*{_NUM}\s*[\])]"
_JOINT = re.compile(r"REFUSED:\s*([a-z0-9_]+) is \S+(?: but|, and)\b")

DEFAULT = tf.model_preset()["name"]
SETTABLE = tuple(tf.ModelParams.settable())


def attempt(preset: str, name: str, value: float) -> tuple[bool, str]:
    try:
        tf.ModelParams.from_preset(preset, **{name: value})
        return True, ""
    except Exception as exc:  # the refusal's text is what is read
        return False, str(exc)


def _interval(raw: str) -> str:
    lo_b, lo, hi, hi_b = re.match(
        rf"([\[(])\s*({_NUM})\s*,\s*({_NUM})\s*([\])])", raw).groups()
    return f"{lo_b}{lo}, {hi}{hi_b}"


def own_rule(name: str, message: str, switch_refuses_half: bool) -> str | None:
    """The set a refusal states for its own dial, in a short fixed form:
    intervals, `> 0`, `>= 0`, `0 or 1`, `whole number in [a, b]`, each
    possibly `... or 0`. The reference's grammar, with the numbers kept at
    the precision the message prints so the edges can be probed."""
    text = re.sub(rf"^(?:REFUSED:\s*)?(?:[a-z0-9_]+ is \S+ but )?"
                  rf"{re.escape(name)}\s+(?:is\s+\S+|must be)\s*", "", message)
    rule = None
    if "inside that interval" in text:
        m = re.search(rf"outside ({_INTERVAL})", text)
        rule = _interval(m.group(1)) if m else None
    if rule is None:
        m = re.search(rf"Set (?:it|a whole number|the persistence) inside ({_INTERVAL})", text)
        if m:
            rule = _interval(m.group(1))
    if rule is None and re.search(r"Set it above zero", text):
        rule = "> 0"
    if rule is None:
        m = re.search(rf"(?:^|\b)(?:in|inside|lives in) ({_INTERVAL})", text)
        if m:
            rule = _interval(m.group(1))
    if rule is None and re.search(r"greater than zero|above zero|\bpositive\b", text):
        rule = "> 0"
    if rule is None:
        m = re.search(rf"from [a-z0-9_]+ \(({_NUM})\) to ({_NUM})", text)
        if m:
            rule = f"[{m.group(1)}, {m.group(2)}]"
            if re.search(r"\bis 0\.0\b", text):
                rule += " or 0"
    if rule is None and re.search(r"\bat least 0\b", text):
        rule = ">= 0"
    if rule is None and re.search(r"at or above one", text):
        rule = ">= 1"
    if re.search(r"\bswitch\b", text) and switch_refuses_half and (
            rule is None or rule == "[0, 1]"):
        rule = "0 or 1"
    if rule is None:
        return None
    if re.search(r"whole number", text) and rule.startswith(("[", "(")):
        rule = "whole number in " + rule
    if re.search(r",? or (?:to )?0\.0\b", text) and not accepts(rule, 0.0):
        rule = ">= 0" if rule == "> 0" else rule + " or 0"
    return rule


def _alternatives(rule: str):
    for alt in rule.split(" or "):
        alt = alt.strip()
        whole = alt.startswith("whole number in ")
        if whole:
            alt = alt[len("whole number in "):]
        yield alt, whole


def accepts(rule: str, x: float) -> bool:
    """Whether a rule in the fixed form admits x."""
    if rule == "any finite number":
        return True
    for alt, whole in _alternatives(rule):
        m = re.match(rf"([\[(])({_NUM}), ({_NUM})([\])])$", alt)
        if m:
            lo, hi = float(m.group(2)), float(m.group(3))
            ok = ((x >= lo if m.group(1) == "[" else x > lo)
                  and (x <= hi if m.group(4) == "]" else x < hi))
            if ok and (not whole or x == int(x)):
                return True
            continue
        m = re.match(rf"(>=|>) ({_NUM})$", alt)
        if m:
            if (x >= float(m.group(2))) if m.group(1) == ">=" else (x > float(m.group(2))):
                return True
            continue
        if re.match(rf"{_NUM}$", alt) and x == float(alt):
            return True
    return False


def edges(rule: str) -> list[float]:
    """Each end the rule states, and a step either side of it."""
    ends: list[float] = []
    for alt, whole in _alternatives(rule):
        m = re.match(rf"[\[(]({_NUM}), ({_NUM})[\])]$", alt)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            ends += [lo, hi]
            if whole:
                ends += [lo + 0.5, hi - 0.5]
            continue
        m = re.match(rf"(?:>=|>) ({_NUM})$", alt)
        if m:
            ends.append(float(m.group(1)))
            continue
        if re.match(rf"{_NUM}$", alt):
            ends.append(float(alt))
    out = []
    for x in ends:
        step = 1e-9 * max(1.0, abs(x))
        out += [x - step, x, x + step]
    return out


def joint(name: str, message: str) -> bool:
    m = _JOINT.match(message)
    return bool(m) and m.group(1) != name


def stated_rule(preset: str, name: str, probes: list[tuple[bool, str]]) -> str:
    """What the dial's own refusals say it accepts, read as the reference
    reads it: from its first own refusal on the ladder."""
    own = [msg for ok, msg in probes if not ok and not joint(name, msg)]
    if not own:
        if probes[0][0] and probes[-1][0]:
            # Accepted at both ends of the ladder, and refused only beside
            # another dial's value.
            return "any finite number"
        own = [msg for ok, msg in probes if not ok and re.search(
            rf"\bbut {re.escape(name)} is\b", msg)]
        if not own:
            return "any finite number"
    if all(re.search(r" and an? [a-z ]+ is set \(", m) for m in own):
        accepted = [x for x, (ok, _) in zip(LADDER, probes) if ok]
        if len(accepted) == 1:
            return f"{accepted[0]!r}"
    half = LADDER.index(0.5)
    refuses_half = not probes[half][0] and probes[half][1] in own
    rule = own_rule(name, own[0], refuses_half)
    assert rule is not None, (
        f"{preset} {name}: no range can be read from its refusal:\n  {own[0]}")
    return rule


def disagreements(preset: str, names=SETTABLE) -> list[str]:
    out = []
    for name in names:
        probes = [attempt(preset, name, x) for x in LADDER]
        rule = stated_rule(preset, name, probes)
        values = list(LADDER) + [x for x in edges(rule) if math.isfinite(x)]
        for x in values:
            ok, msg = attempt(preset, name, x)
            if not ok and joint(name, msg):
                continue
            if ok != accepts(rule, x):
                out.append(f"{preset} {name}: states {rule!r}, but {x!r} is "
                           f"{'accepted' if ok else 'refused: ' + msg}")
    return out


def test_every_dial_states_what_it_accepts_on_the_default_preset():
    assert disagreements(DEFAULT) == []


#: The four #248 named, on every shipped preset: their ranges and their
#: cross-dial conditions move with the preset, and each message states the
#: set for the preset it is read on.
NAMED = ("impact_memory_coefficient", "market_day_tail_df",
         "overnight_idio_df", "rate_close_remark")


@pytest.mark.parametrize("preset", tf.preset_names())
def test_the_four_named_dials_state_what_they_accept_on_every_preset(preset):
    assert disagreements(preset, NAMED) == []


@pytest.mark.parametrize("name,value,accepted", [
    # 0.0 is off and accepted beside a range that starts above it.
    ("market_day_tail_df", 0.0, True),
    ("market_day_tail_df", 3.0, True),
    ("market_day_tail_df", 2.999, False),
    ("overnight_idio_df", 0.0, True),
    ("overnight_idio_df", 3.5, False),
    # On pt-v21 the live rate mark is on, so the close's re-mark is 1.0.
    ("rate_close_remark", 0.0, False),
    ("rate_close_remark", 1.0, True),
    # pt-v21's book_depth_coefficient is 0.75, the memory's ceiling.
    ("impact_memory_coefficient", 0.75, True),
    ("impact_memory_coefficient", 0.7500001, False),
    # The epicentre's extra is squared in the solve, so a negative one read
    # as its own magnitude; the validator now keeps the stated interval.
    ("crisis_epicentre_extra", -1.0, False),
    ("crisis_epicentre_extra", 1.0, True),
])
def test_the_edges_issue_248_named(name, value, accepted):
    assert tf.model_preset()["name"] == "pt-v21"
    ok, msg = attempt("pt-v21", name, value)
    assert ok == accepted, msg
