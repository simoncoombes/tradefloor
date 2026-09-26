"""The scenario reader on hostile input: it answers in bounded time and
refuses with its own error.

`tradefloor scenario validate` is offered as a pre-commit or CI check over a
directory of scenario files, so the reader meets files nobody has looked
at. Before 0.8.5 was tagged a review found three ways one such file broke
it:

- one long line made a refusal pattern backtrack quadratically: a value of
  `1`, 16,000 underscores and an `x` took seven seconds, and a 100 KB line
  about five minutes;
- blocks nested about a thousand deep raised RecursionError;
- an integer of more than 4,300 digits raised ValueError from int().

The last two are not ValidationError, so `validate a.yml b.yml` stopped at
the bad file with a traceback and never read the rest.

Kept apart from `test_yaml_subset.py`, which skips whole without pyyaml:
none of these needs a second parser.
"""

import re
import time

import pytest

from tradefloor.__main__ import main
from tradefloor.yaml_subset import MAX_DEPTH, YamlSubsetError, read

#: The digit-separator refusal as it was written before the fix. It is the
#: oracle for the replacement, which must refuse exactly the same values.
OLD_SEPARATOR = re.compile(r"^[-+]?[0-9][0-9_]*_[0-9_.eE+-]*$")


def _separator_pattern():
    from tradefloor import yaml_subset

    for pattern, why in yaml_subset._AMBIGUOUS:
        if "digit separator" in why:
            return pattern
    raise AssertionError("the digit-separator refusal is gone")


def test_the_separator_refusal_matches_what_it_matched_before():
    import itertools
    import random

    new = _separator_pattern()
    alphabet = "1_0.eE+-x"
    # Every string of up to five characters, then longer random ones.
    corpus = ["".join(chars) for n in range(1, 6)
              for chars in itertools.product(alphabet, repeat=n)]
    rng = random.Random(20260926)
    corpus += ["".join(rng.choice(alphabet) for _ in range(rng.randint(6, 16)))
               for _ in range(20_000)]
    refused = 0
    for text in corpus:
        old = bool(OLD_SEPARATOR.match(text))
        assert bool(new.match(text)) == old, text
        refused += old
    assert refused > 1_000


def test_a_long_run_of_underscores_is_read_in_linear_time():
    # 50,000 underscores: the old pattern needed well over a minute here.
    text = "a: 1" + "_" * 50_000 + "x\n"
    started = time.perf_counter()
    assert read(text) == {"a": "1" + "_" * 50_000 + "x"}
    assert time.perf_counter() - started < 2.0


def test_a_long_digit_separated_number_is_still_refused_quickly():
    text = "a: 1" + "_0" * 25_000 + "\n"
    started = time.perf_counter()
    with pytest.raises(YamlSubsetError, match="digit separator") as exc:
        read(text)
    assert time.perf_counter() - started < 2.0
    # The message quotes the value and the line, but not 50 KB of them.
    assert len(str(exc.value)) < 1_000


def _nested_mappings(levels):
    return "".join(" " * (2 * i) + f"k{i}:\n" for i in range(levels)) + \
        " " * (2 * levels) + "v: 1\n"


def _nested_sequences(levels):
    return "".join(" " * (2 * i) + f"- k{i}:\n" for i in range(levels)) + \
        " " * (2 * levels) + "- 1\n"


@pytest.mark.parametrize("build", [_nested_mappings, _nested_sequences])
def test_nesting_past_the_limit_is_refused_not_a_recursion_error(build):
    with pytest.raises(YamlSubsetError, match="nested more than") as exc:
        read(build(1_000))
    # The line that would have opened block MAX_DEPTH + 1.
    assert exc.value.line == MAX_DEPTH + 2


@pytest.mark.parametrize("build", [_nested_mappings, _nested_sequences])
def test_nesting_up_to_the_limit_still_reads(build):
    value = read(build(MAX_DEPTH))
    depth = 0
    while isinstance(value, (dict, list)):
        value = next(iter(value.values())) if isinstance(value, dict) \
            else value[0]
        depth += 1
    assert value == 1 and depth > MAX_DEPTH


def test_an_integer_past_pythons_digit_limit_is_refused():
    with pytest.raises(YamlSubsetError, match="more digits than Python"):
        read("a: " + "9" * 5_000 + "\n")
    # A short one is still a number, and a quoted long one is text.
    assert read("a: 12345") == {"a": 12345}
    assert read("a: '" + "9" * 5_000 + "'") == {"a": "9" * 5_000}


def test_other_scripts_digits_are_text_as_yaml_reads_them():
    # int() read the Arabic-Indic three as 3 and raised on a superscript
    # two; YAML reads both as strings.
    assert read("a: ٣") == {"a": "٣"}
    assert read("a: ²") == {"a": "²"}


def test_validate_reports_each_bad_file_and_reads_the_next(tmp_path, capsys):
    deep = tmp_path / "deep.yml"
    deep.write_text(_nested_mappings(1_000), encoding="utf-8")
    long_int = tmp_path / "long_int.yml"
    long_int.write_text("version: " + "1" * 5_000 + "\n", encoding="utf-8")
    binary = tmp_path / "binary.yml"
    binary.write_bytes(b"version: 1\nscenario:\n  name: \xff\xfe\n")

    code = main(["scenario", "validate", str(deep), str(long_int),
                 str(binary), "liquidity_crisis"])

    out = capsys.readouterr().out
    assert code == 1
    assert out.count("Scenario invalid.") == 3
    assert "nested more than" in out
    assert "more digits than Python" in out
    assert "utf-8" in out
    assert out.count("Scenario valid.") == 1
