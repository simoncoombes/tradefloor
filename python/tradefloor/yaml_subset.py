"""A reader for the boring part of YAML, and nothing else.

The library depends on nothing, and that is a promise rather than an
accident: `pip install tradefloor` pulls one wheel, and a research
environment does not acquire a transitive tree because somebody wanted a
config file. Scenario documents are configuration, so they had to be readable
without a dependency.

The alternative was `pyyaml` behind an optional extra, which would have made
the documented first line of the feature -- `Scenario.from_yaml(...)` -- fail
on a default install. So this reads the subset the scenario schema actually
uses:

    version: 1

    scenario:
      name: liquidity_crisis
      description: >
        Folded block text.

      shocks:
        - target: market.liquidity
          operation: multiply
          value: 0.40
          at: 50
          duration: 25

Block mappings, block sequences, plain and quoted scalars, `>` and `|` block
text, `#` comments, `null`/`true`/`false`, integers and floats. That is the
whole grammar.

## Everything else is refused by name

This is the important half. A hand-written parser that GUESSES at a construct
it does not implement is worse than no parser: it reads a document as
something other than what it says. So every YAML feature outside the subset
raises, and says which feature it was:

- tags (`!!python/object`, or any `!`) -- the construct behind every YAML
  deserialisation CVE, and this reader has no code that could build an object
  from one;
- anchors and aliases (`&`, `*`), including merge keys (`<<`);
- flow collections (`{...}`, `[...]`);
- more than one document;
- tabs, which YAML forbids as indentation and which look identical to spaces
  in a diff;
- complex keys, and any key that is not a plain or quoted scalar;
- duplicate keys, which YAML permits and which silently discard one of the
  two.

Because none of those is implemented, none of them is reachable. A scenario
file cannot name a Python type, cannot import, cannot construct, and cannot
alias one part of the document into another. The output is dicts, lists,
strings, numbers, booleans and None -- and the scenario loader then refuses
every key it does not recognise, so the reachable surface is the schema.

Numbers are narrower than YAML 1.1 on purpose, and the narrowing is a
REFUSAL rather than a different answer. `1:30` is ninety to a YAML parser and
one-thirty to a reader; `007` is seven; `2026-08-29` is a datetime object;
`1e3` and `1.0e3` are TEXT, because YAML 1.1 wants a decimal point and a
signed exponent. Each of those is a value that would mean something other
than it looks like, so each raises and says what to write instead. Silently
picking either answer is the quietest defect a configuration reader can
have.

## What it does not claim

It is not a YAML implementation. A document this reader accepts is read the
way a compliant parser would read it, or it raises; a document it refuses may
still be valid YAML. That asymmetry is the safe direction, and it is what
`tests/test_yaml_subset.py` pins: where pyyaml happens to be installed, the
suite compares this reader against `yaml.safe_load` on every shipped scenario
and on a corpus of accepted fragments.
"""

from __future__ import annotations

import re
from typing import Any

from ._core import ValidationError

__all__ = ["read", "YamlSubsetError"]


class YamlSubsetError(ValidationError):
    """A document this reader will not guess at, with the line that caused it."""

    def __init__(self, message: str, *, line: int, text: str = "") -> None:
        detail = f"line {line}: {message}"
        if text.strip():
            detail += f"\n  {text.rstrip()}"
        super().__init__(detail)
        self.line = line


_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"null", "~", ""}
_BLOCK_STYLES = (">", "|", ">-", "|-", ">+", "|+")


class _Line:
    """One significant line: its number, its indent and its content."""

    __slots__ = ("number", "indent", "text", "raw")

    def __init__(self, number: int, raw: str) -> None:
        self.number = number
        self.raw = raw
        stripped = raw.lstrip(" ")
        self.indent = len(raw) - len(stripped)
        self.text = stripped.rstrip()


class _Doc:
    """The significant lines, plus the raw ones a block scalar has to re-read.

    Blank lines and comments are dropped before parsing, which is what makes
    the indent logic short. A block scalar cannot use the dropped version --
    a blank line inside `>` is a paragraph break and a `#` inside `|` is
    text -- so it reads the raw lines back by number instead.

    ``ends_with_newline`` is carried for the same reader. YAML's default
    chomping keeps ONE trailing line break, and a block that runs to the end
    of a file with no final newline has none to keep. Getting that wrong is
    a one-character difference in a description, which is also a different
    fingerprint.
    """

    __slots__ = ("lines", "raw", "ends_with_newline")

    def __init__(self, lines: list[_Line], raw: list[str],
                 ends_with_newline: bool) -> None:
        self.lines = lines
        self.raw = raw
        self.ends_with_newline = ends_with_newline


def read(text: str) -> Any:
    """Parse a scenario document. Returns dicts, lists and scalars."""
    doc = _scan(text)
    if not doc.lines:
        return None
    value, index = _parse_block(doc, 0, doc.lines[0].indent)
    if index != len(doc.lines):
        line = doc.lines[index]
        raise YamlSubsetError(
            "content continues after the document ended, at an indent that "
            "belongs to nothing above it",
            line=line.number, text=line.raw,
        )
    return value


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _scan(text: str) -> _Doc:
    raw = text.splitlines()
    out: list[_Line] = []
    seen_document = False
    for number, line in enumerate(raw, start=1):
        if "\t" in line:
            raise YamlSubsetError(
                "tab character. YAML forbids tabs as indentation, and a tab "
                "inside a value is invisible in a diff -- use spaces",
                line=number, text=line,
            )
        stripped = line.strip()
        if stripped == "---" or stripped.startswith("--- "):
            if seen_document or out:
                raise YamlSubsetError(
                    "a second document. A scenario file holds exactly one",
                    line=number, text=line,
                )
            seen_document = True
            continue
        if stripped == "...":
            break
        if not stripped or stripped.startswith("#"):
            continue
        out.append(_Line(number, line))
    return _Doc(out, raw, text.endswith((chr(10), chr(13))))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_block(doc: _Doc, index: int, indent: int) -> tuple[Any, int]:
    line = doc.lines[index]
    if line.text.startswith("- "):
        return _parse_sequence(doc, index, indent)
    if line.text == "-":
        raise YamlSubsetError(
            "a sequence item with nothing on the line. Write the item on the "
            "same line as its dash",
            line=line.number, text=line.raw,
        )
    return _parse_mapping(doc, index, indent)


def _parse_sequence(doc: _Doc, index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(doc.lines):
        line = doc.lines[index]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise YamlSubsetError(
                f"unexpected indent: this line sits {line.indent - indent} "
                f"space(s) deeper than the sequence it is in",
                line=line.number, text=line.raw,
            )
        if not line.text.startswith("- "):
            break
        items.append(None)
        items[-1], index = _parse_item(doc, index, line, indent)
    return items, index


def _parse_item(doc: _Doc, index: int, line: _Line,
                indent: int) -> tuple[Any, int]:
    """One `- ...` entry, and every line indented under it."""
    body = line.text[2:]
    # The item's own indent is where its content actually starts, so
    # `-   target: x` puts its remaining keys at that column rather than at a
    # column nothing is written in.
    item_indent = indent + 2 + (len(body) - len(body.lstrip(" ")))
    body = body.lstrip(" ")
    if body.startswith("- "):
        raise YamlSubsetError(
            "a sequence directly inside a sequence. The scenario schema has "
            "no such shape",
            line=line.number, text=line.raw,
        )

    key, is_pair, rest = _split_key(body, line)
    index += 1
    if not is_pair:
        return _scalar(body, line), index

    mapping: dict[str, Any] = {}
    mapping[key], index = _finish_pair(doc, index, rest, item_indent, line)

    while index < len(doc.lines):
        nxt = doc.lines[index]
        if nxt.indent < item_indent or nxt.text.startswith("- "):
            break
        if nxt.indent > item_indent:
            raise YamlSubsetError(
                "unexpected indent inside a sequence item",
                line=nxt.number, text=nxt.raw,
            )
        k, is_pair, rest = _split_key(nxt.text, nxt)
        if not is_pair:
            raise YamlSubsetError(
                "expected `key: value` inside a sequence item",
                line=nxt.number, text=nxt.raw,
            )
        if k in mapping:
            raise YamlSubsetError(
                f"duplicate key {k!r} in one item. One of the two would be "
                f"discarded, silently",
                line=nxt.number, text=nxt.raw,
            )
        index += 1
        mapping[k], index = _finish_pair(doc, index, rest, item_indent, nxt)
    return mapping, index


def _parse_mapping(doc: _Doc, index: int, indent: int) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(doc.lines):
        line = doc.lines[index]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise YamlSubsetError(
                f"unexpected indent: this line sits {line.indent - indent} "
                f"space(s) deeper than the mapping it is in, and nothing "
                f"above it opens a block",
                line=line.number, text=line.raw,
            )
        if line.text.startswith("- "):
            break
        key, is_pair, rest = _split_key(line.text, line)
        if not is_pair:
            raise YamlSubsetError(
                "expected `key: value`. A bare scalar cannot sit where a "
                "mapping key belongs",
                line=line.number, text=line.raw,
            )
        if key in mapping:
            raise YamlSubsetError(
                f"duplicate key {key!r}. One of the two would be discarded, "
                f"silently",
                line=line.number, text=line.raw,
            )
        index += 1
        mapping[key], index = _finish_pair(doc, index, rest, indent, line)
    return mapping, index


def _finish_pair(doc: _Doc, index: int, rest: str, indent: int,
                 line: _Line) -> tuple[Any, int]:
    """The value half of one `key:` line, which may be a whole block."""
    rest = rest.strip()
    if rest in _BLOCK_STYLES:
        return _block_scalar(doc, index, indent, rest, line)
    if rest:
        return _scalar(rest, line), index
    if index >= len(doc.lines):
        return None, index
    nxt = doc.lines[index]
    if nxt.indent > indent:
        return _parse_block(doc, index, nxt.indent)
    # A sequence may sit at its key's own indent -- `shocks:` on one line and
    # `- target: ...` at the same column on the next is the commonest way
    # anybody writes YAML, and reading it as an empty value would drop every
    # intervention in the file without an error.
    if nxt.indent == indent and nxt.text.startswith("- "):
        return _parse_sequence(doc, index, indent)
    return None, index


def _block_scalar(doc: _Doc, index: int, indent: int, style: str,
                  line: _Line) -> tuple[str, int]:
    """`>` folds line breaks into spaces; `|` keeps them.

    Read from the RAW lines, because the body may contain blank lines (a
    paragraph break under `>`) and `#` (text, not a comment, inside a block)
    and both are gone from the parsed line list.
    """
    folded = style[0] == ">"
    chomp = style[1:]

    start = line.number  # 1-based: the key's line. The body begins after it.
    body_raw: list[str] = []
    number = start
    while number < len(doc.raw):
        candidate = doc.raw[number]  # doc.raw[number] is line number+1
        if candidate.strip() and (len(candidate) - len(candidate.lstrip(" "))) <= indent:
            break
        body_raw.append(candidate)
        number += 1

    while body_raw and not body_raw[-1].strip():
        body_raw.pop()
    if not body_raw:
        raise YamlSubsetError(
            f"`{style}` opens a block of text and none followed",
            line=line.number, text=line.raw,
        )

    body_indent = min(len(b) - len(b.lstrip(" ")) for b in body_raw if b.strip())
    stripped = [b[body_indent:].rstrip() if b.strip() else "" for b in body_raw]

    if folded:
        paragraphs: list[str] = []
        current: list[str] = []
        for part in stripped:
            if part:
                current.append(part)
            elif current:
                paragraphs.append(" ".join(current))
                current = []
        if current:
            paragraphs.append(" ".join(current))
        text = "\n".join(paragraphs)
    else:
        text = "\n".join(stripped)
    # Clip (the default) keeps one trailing break, and strip removes it. A
    # block that runs to the end of a file with no final newline has no break
    # to keep either way.
    ran_to_end = start + len(body_raw) >= len(doc.raw)
    if chomp != "-" and not (ran_to_end and not doc.ends_with_newline):
        text += "\n"

    # Advance past every parsed line the body consumed.
    last = start + len(body_raw)
    while index < len(doc.lines) and doc.lines[index].number <= last:
        index += 1
    return text, index


# ---------------------------------------------------------------------------
# Scalars and keys
# ---------------------------------------------------------------------------


def _split_key(text: str, line: _Line) -> tuple[str, bool, str]:
    """Split `key: value`, refusing the constructs this reader does not read."""
    _refuse_unread(text, line)

    if text[:1] in "'\"":
        quote = text[0]
        end = _closing_quote(text, quote, line)
        key = _unquote(text[: end + 1], quote, line)
        after = text[end + 1:]
        if not after.startswith(":"):
            return key, False, ""
        return key, True, _strip_comment(after[1:])

    head, sep, rest = text.partition(":")
    if not sep:
        return text, False, ""
    # `a:b` is a plain scalar in YAML, not a mapping entry. Only `a: b` and a
    # bare `a:` open a pair.
    if rest and not rest.startswith(" "):
        return text, False, ""
    key = head.strip()
    if not key:
        raise YamlSubsetError(
            "a mapping entry with an empty key",
            line=line.number, text=line.raw,
        )
    return key, True, _strip_comment(rest)


def _refuse_unread(text: str, line: _Line) -> None:
    """Every construct outside the subset, named rather than guessed at."""
    if text.startswith(("&", "*")):
        raise YamlSubsetError(
            "an anchor or alias. This reader has no code to resolve one, so "
            "it refuses rather than reading the document as something else",
            line=line.number, text=line.raw,
        )
    if text.startswith("<<"):
        raise YamlSubsetError(
            "a merge key. Scenario composition happens in Python, not in the "
            "configuration file",
            line=line.number, text=line.raw,
        )
    if text.startswith("? "):
        raise YamlSubsetError(
            "an explicit complex key. Scenario keys are plain names",
            line=line.number, text=line.raw,
        )
    if text.startswith("!"):
        raise YamlSubsetError(
            "a tag. Tags are how a YAML loader is talked into constructing "
            "objects, and this reader has no constructor to reach -- a "
            "scenario names registered targets and nothing else",
            line=line.number, text=line.raw,
        )
    if text.startswith(("{", "[")):
        raise YamlSubsetError(
            "a flow collection. Write it as an indented block: this reader "
            "implements block style only, and guessing at flow style is how "
            "a parser reads a document as something other than what it says",
            line=line.number, text=line.raw,
        )


def _closing_quote(text: str, quote: str, line: _Line) -> int:
    i = 1
    while i < len(text):
        c = text[i]
        if c == "\\" and quote == '"':
            i += 2
            continue
        if c == quote:
            if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            return i
        i += 1
    raise YamlSubsetError(
        f"unterminated {quote} string", line=line.number, text=line.raw,
    )


def _strip_comment(text: str) -> str:
    """Drop a trailing `# ...`, but only where YAML would.

    A `#` starts a comment when it begins the line or follows whitespace; one
    inside a word (`pt-v14#1`) is text. Quoted regions are skipped whole, so a
    `#` inside a string survives.
    """
    out: list[str] = []
    i = 0
    previous_space = True
    while i < len(text):
        c = text[i]
        if c in "'\"":
            quote = c
            j = i + 1
            while j < len(text):
                if text[j] == "\\" and quote == '"':
                    j += 2
                    continue
                if text[j] == quote:
                    if quote == "'" and j + 1 < len(text) and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(text[i: j + 1])
            i = j + 1
            previous_space = False
            continue
        if c == "#" and previous_space:
            break
        out.append(c)
        previous_space = c in " \t"
        i += 1
    return "".join(out)


def _scalar(text: str, line: _Line) -> Any:
    text = _strip_comment(text).strip()
    if not text:
        return None
    if text[0] in "'\"":
        end = _closing_quote(text, text[0], line)
        if text[end + 1:].strip():
            raise YamlSubsetError(
                "trailing content after a quoted value",
                line=line.number, text=line.raw,
            )
        return _unquote(text[: end + 1], text[0], line)
    _refuse_unread(text, line)

    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    if lowered in _NULL:
        return None
    _refuse_ambiguous_number(text, line)
    number = _number(text)
    return text if number is None else number


#: Tokens that a person reads as one thing and YAML 1.1 reads as another.
#:
#: Every one of these is refused rather than resolved, because both answers
#: are defensible and a scenario value that silently became a different number
#: is the quietest defect available. The message says how to write what you
#: meant. `yaml.safe_load` resolves each of them differently from a plain
#: reading: `1:30` is ninety, `007` is seven, `2026-08-29` is a `datetime`
#: object, `1e3` and `1.0e3` are TEXT because YAML 1.1 wants a decimal point
#: and a signed exponent, and `.inf` is infinity.
_AMBIGUOUS = (
    (re.compile(r"^[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?$"),
     "YAML 1.1 reads a colon-separated number as base 60, so this is not "
     "the digits you wrote. Quote it if it is text"),
    (re.compile(r"^[-+]?[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}"),
     "YAML reads this as a date and builds a datetime object. Quote it"),
    (re.compile(r"^[-+]?0[0-9_]+$"),
     "YAML 1.1 reads a leading zero as octal, so 010 is eight. Write it "
     "without the zero, or quote it"),
    (re.compile(r"^[-+]?[0-9][0-9_]*[eE][-+]?[0-9]+$"),
     "YAML 1.1 needs a decimal point in a float, so 1e3 is text rather "
     "than a thousand. Write 1000.0"),
    (re.compile(r"^[-+]?(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9_]+)[eE][0-9]"),
     "YAML 1.1 needs a SIGNED exponent, so 1.5e3 is text to a parser and a "
     "number to a reader. Write 1500.0, or 1.5e+3"),
    (re.compile(r"^[-+]?\.(?:inf|nan)$", re.I),
     "infinity and not-a-number are not values a scenario can mean"),
)


def _refuse_ambiguous_number(text: str, line: _Line) -> None:
    for pattern, why in _AMBIGUOUS:
        if pattern.match(text):
            raise YamlSubsetError(
                f"{text!r} is ambiguous: {why}",
                line=line.number, text=line.raw,
            )


def _number(text: str) -> int | float | None:
    """A number, or None for anything that is a string.

    Deliberately YAML 1.1's own reading, minus everything ambiguous, which
    `_refuse_ambiguous_number` has already rejected by the time this runs. An
    integer is plain digits; a float needs a decimal point and, if it carries
    an exponent, a sign on it.
    """
    body = text[1:] if text[:1] in "+-" else text
    if not body:
        return None
    if body.isdigit():
        return int(text)
    if "." not in text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _unquote(text: str, quote: str, line: _Line) -> str:
    body = text[1:-1]
    if quote == "'":
        return body.replace("''", "'")
    out: list[str] = []
    i = 0
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
               "/": "/", "0": "\0"}
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(body):
            raise YamlSubsetError(
                "a string ending in a lone backslash",
                line=line.number, text=line.raw,
            )
        nxt = body[i + 1]
        if nxt not in escapes:
            raise YamlSubsetError(
                f"escape \\{nxt} is not one this reader implements. "
                "Supported: " + ", ".join("\\" + k for k in escapes),
                line=line.number, text=line.raw,
            )
        out.append(escapes[nxt])
        i += 2
    return "".join(out)
