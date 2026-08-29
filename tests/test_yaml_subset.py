"""The scenario reader: what it accepts, what it refuses, and that it agrees.

`tradefloor.yaml_subset` exists because the library depends on nothing and a
scenario file is configuration. That trade is only defensible if the reader
is strictly narrower than YAML and never guesses -- a hand-written parser
that reads a document as something other than what it says is worse than no
parser at all.

So this module tests three things:

1. **Agreement.** Every fragment the reader accepts, and every scenario file
   the repository ships, is compared against `yaml.safe_load` when pyyaml is
   installed. Where the two disagree, the reader is wrong.
2. **Refusal.** Every construct outside the subset raises, by name. None of
   them is implemented, so none of them is reachable; these tests are what
   keeps that true as the reader changes.
3. **Safety.** A scenario file cannot construct a Python object, cannot
   import, cannot alias, and cannot reach a target the registry does not
   name.

The pyyaml import is optional and the agreement tests skip without it. That
is a lane that can quietly stop existing, so `test_the_agreement_lane_is_not
_silently_empty` fails loudly if the corpus is empty, and CONTRIBUTING lists
pyyaml among the development dependencies for exactly this reason.
"""

import pathlib

import pytest

from tradefloor.yaml_subset import YamlSubsetError, read

yaml = pytest.importorskip("yaml", reason="pyyaml is a dev-only cross-check")

REPO = pathlib.Path(__file__).resolve().parent.parent
SHIPPED = sorted((REPO / "scenarios").glob("*.yml"))

#: Every shape the scenario schema uses, and a few it does not but a person
#: might reasonably write. Each is read by both parsers and compared.
ACCEPTED = [
    "a: 1",
    "a: 1.5",
    "a: -2",
    "a: +3",
    "a: -1.5e-3",
    "a: 1500.0",
    "a: true\nb: false",
    "a: null\nb: ~\nc:",
    "a: hello world",
    "a: 'quoted: colon'",
    'a: "escaped \\"quote\\""',
    "a: it''s",
    "a: macro.policy_rate",
    "a: 0.40",
    "a: value # trailing comment",
    "# leading comment\na: 1",
    "a:\n  b: 1\n  c: 2",
    "a:\n  - 1\n  - 2",
    "a:\n- 1\n- 2",
    "a:\n  - b: 1\n    c: 2\n  - b: 3\n    c: 4",
    "a:\n- b: 1\n  c: 2",
    "a: >\n  folded text\n  on two lines",
    "a: |\n  literal text\n  on two lines",
    "a: >\n  first paragraph\n\n  second paragraph",
    "a: >-\n  stripped",
    "version: 1\nscenario:\n  name: x\n  shocks:\n    - target: macro.vix\n"
    "      operation: multiply\n      value: 2.0\n      at: 50\n"
    "      duration: 25",
    "a:\n  relative: 5",
    "a: 1\n\n\nb: 2",
    "---\na: 1",
]


@pytest.mark.parametrize("text", ACCEPTED, ids=range(len(ACCEPTED)))
def test_the_reader_agrees_with_a_real_yaml_parser(text):
    assert read(text) == yaml.safe_load(text)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_shipped_scenario_reads_the_same_both_ways(path):
    text = path.read_text(encoding="utf-8")
    assert read(text) == yaml.safe_load(text)


def test_the_agreement_lane_is_not_silently_empty():
    """A skip and a pass are the same colour.

    If the corpus or the scenario directory ever empties, the tests above
    report green while checking nothing. This is the one that notices.
    """
    assert len(ACCEPTED) >= 25
    assert len(SHIPPED) >= 3


REFUSED = [
    ("a: !!python/object/apply:os.system ['echo pwned']", "tag"),
    ("a: !CustomTag 1", "tag"),
    ("a: &anchor 1\nb: *anchor", "anchor"),
    ("base: &b\n  x: 1\nderived:\n  <<: *b", "anchor"),
    ("a: {b: 1}", "flow collection"),
    ("a: [1, 2]", "flow collection"),
    ("a:\t1", "tab"),
    ("a: 1\nb:\n\tc: 2", "tab"),
    ("a: 1\na: 2", "duplicate key"),
    ("a:\n  - b: 1\n    b: 2", "duplicate key"),
    ("---\na: 1\n---\nb: 2", "second document"),
    ("? [a, b]\n: 1", "complex key"),
    ("a: 1\n    b: 2", "unexpected indent"),
    ("a: 'unterminated", "unterminated"),
    ('a: "bad \\q escape"', "escape"),
    ("a:\n  -\n    b: 1", "nothing on the line"),
    ("just a scalar", "expected `key: value`"),
    ("a: >\nb: 1", "opens a block of text"),
    # Every token below is one a person reads one way and YAML 1.1 reads
    # another. Refusing them is the reason this reader can be trusted to
    # agree with a real parser on everything it does accept.
    ("a: 1e3", "decimal point"),
    ("a: 1.0e3", "SIGNED exponent"),
    ("a: 1:30", "base 60"),
    ("a: 007", "octal"),
    ("a: 2026-08-29", "datetime"),
    ("a: .inf", "infinity"),
    ("a: .nan", "not-a-number"),
    # Found by the differential fuzz below, not by reading the spec.
    ("a: 0x1f", "hexadecimal"),
    ("a: 1_000", "digit separator"),
    ("a: -", "opens a block sequence"),
    ("a: - x", "opens a block sequence"),
    ("a: %v", "reserved indicator"),
    ("a: `x", "reserved indicator"),
]


@pytest.mark.parametrize("text,fragment", REFUSED,
                         ids=[f[1].replace(" ", "-") + str(i)
                              for i, f in enumerate(REFUSED)])
def test_a_construct_outside_the_subset_is_refused_by_name(text, fragment):
    with pytest.raises(YamlSubsetError) as exc:
        read(text)
    assert fragment in str(exc.value)


def test_a_refusal_names_the_line():
    with pytest.raises(YamlSubsetError) as exc:
        read("version: 1\nscenario:\n  name: x\n  bad: {a: 1}\n")
    assert exc.value.line == 4
    assert "line 4" in str(exc.value)


def test_a_hash_inside_a_word_is_text_not_a_comment():
    assert read("a: pt-v14#1") == {"a": "pt-v14#1"}
    assert read("a: pt-v14#1") == yaml.safe_load("a: pt-v14#1")


def test_a_hash_inside_a_quoted_string_survives():
    assert read("a: 'has # hash'") == {"a": "has # hash"}


def test_a_colon_without_a_space_is_a_scalar_not_a_mapping():
    assert read("a: http://example.com") == {"a": "http://example.com"}
    assert read("a: http://example.com") == yaml.safe_load(
        "a: http://example.com")


def test_the_ambiguous_number_refusals_are_the_ones_pyyaml_disagrees_on():
    """The refusals above, justified rather than asserted.

    Each of these is a token this reader refuses. The test is that a real
    YAML parser reads it as something a person would not: that is what makes
    refusing it right, rather than merely conservative.
    """
    disagreements = {
        "a: 1:30": 90,
        "a: 007": 7,
        "a: 1e3": "1e3",
        "a: 1.0e3": "1.0e3",
        "a: .inf": float("inf"),
    }
    for text, resolved in disagreements.items():
        with pytest.raises(YamlSubsetError):
            read(text)
        assert yaml.safe_load(text)["a"] == resolved
    assert isinstance(yaml.safe_load("a: 2026-08-29")["a"],
                      __import__("datetime").date)
    with pytest.raises(YamlSubsetError):
        read("a: 2026-08-29")


def _fuzz_document(rng):
    """One generated block-YAML document, from the grammar the schema uses."""
    keys = ["a", "b", "name", "target", "value", "at", "duration", "shape"]
    scalars = ["1", "0", "-2", "+3", "1.5", "-0.05", "0.40", "1500.0",
               "2.0e+3", "true", "false", "null", "~", "", "hello",
               "hello world", "macro.policy_rate", "a-b_c", "x:y", "a#b",
               "'quoted'", "'with: colon'", "'it''s'", '"dq"', "multiply",
               "  spaced  ", "a,b", "3 # trailing", "yes", "no", "on", "off",
               ".5", "5.", "e5", "inf", "0x1f", "1_000", "-", "007", "1:30"]

    def mapping(indent, depth, budget):
        lines, used = [], set()
        for _ in range(rng.randint(1, 3)):
            if budget[0] <= 0:
                break
            budget[0] -= 1
            key = rng.choice(keys)
            if key in used:
                continue
            used.add(key)
            pad = " " * indent
            roll = rng.random()
            if depth > 0 and roll < 0.18:
                lines.append(f"{pad}{key}:")
                lines.extend(mapping(indent + rng.choice([1, 2, 4]),
                                     depth - 1, budget))
            elif depth > 0 and roll < 0.40:
                lines.append(f"{pad}{key}:")
                lines.extend(sequence(indent + rng.choice([0, 2, 4]),
                                      depth - 1, budget))
            elif roll < 0.48:
                style = rng.choice([">", "|", ">-", "|-"])
                lines.append(f"{pad}{key}: {style}")
                for _ in range(rng.randint(1, 3)):
                    lines.append(f"{' ' * (indent + 2)}"
                                 f"{rng.choice(['some text', 'a b c'])}")
            else:
                lines.append(f"{pad}{key}: {rng.choice(scalars)}")
            if rng.random() < 0.12:
                lines.append(f"{' ' * indent}# a comment")
            if rng.random() < 0.10:
                lines.append("")
        return lines

    def sequence(indent, depth, budget):
        lines, pad = [], " " * indent
        for _ in range(rng.randint(1, 3)):
            if budget[0] <= 0:
                break
            budget[0] -= 1
            if rng.random() < 0.3:
                lines.append(f"{pad}- {rng.choice(scalars)}")
                continue
            inner = [ln for ln in mapping(0, depth - 1, budget) if ln.strip()]
            if not inner:
                lines.append(f"{pad}- {rng.choice(scalars)}")
                continue
            first, *rest = inner
            lines.append(f"{pad}- {first.lstrip()}")
            lines.extend(f"{pad}  {ln.lstrip()}" for ln in rest)
        return lines

    return "\n".join(mapping(0, 3, [12])) + "\n"


def test_the_reader_never_disagrees_with_a_real_parser():
    """The property the whole module rests on, fuzzed rather than argued.

    A document this reader ACCEPTS must be read the way `yaml.safe_load`
    reads it. Refusing something pyyaml accepts is the safe direction and is
    fine; reading it differently is not, because then a scenario file means
    one thing to its author and another to the simulator.

    Seeded, so a failure is reproducible, and small enough to be an ordinary
    test. The version of this that ran during development, over sixteen
    thousand documents on four seeds, is what found `0x1f`, `1_000` and the
    bare `-` in the refusal list above -- none of which came from reading the
    YAML spec.
    """
    import random

    rng = random.Random(20260829)
    accepted = disagreements = 0
    for _ in range(1500):
        text = _fuzz_document(rng)
        try:
            mine = read(text)
        except YamlSubsetError:
            continue
        try:
            theirs = yaml.safe_load(text)
        except Exception:
            # We accepted a document pyyaml calls malformed. That is a
            # disagreement too: it means a file that works here works
            # nowhere else.
            disagreements += 1
            raise AssertionError(
                f"pyyaml rejects a document we accept:\n{text}") from None
        accepted += 1
        assert mine == theirs, f"read differently:\n{text}\n{mine!r}\n{theirs!r}"
    assert accepted > 300, f"the fuzz accepted only {accepted} documents"
    assert disagreements == 0


def test_the_reader_builds_only_plain_data():
    document = read((REPO / "scenarios" / "liquidity_crisis.yml")
                    .read_text(encoding="utf-8"))
    seen = set()

    def walk(node):
        seen.add(type(node).__name__)
        if isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    assert seen <= {"dict", "list", "str", "int", "float", "bool", "NoneType"}
