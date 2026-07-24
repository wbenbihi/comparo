"""Linear, callback-free RFC 9535 JSONPath parser."""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from typing import Never
from typing import final

from comparo.core.domain.selectors.jsonpath import AndExpression
from comparo.core.domain.selectors.jsonpath import ChildSegment
from comparo.core.domain.selectors.jsonpath import ComparisonExpression
from comparo.core.domain.selectors.jsonpath import ComparisonOperator
from comparo.core.domain.selectors.jsonpath import DescendantSegment
from comparo.core.domain.selectors.jsonpath import FilterExpression
from comparo.core.domain.selectors.jsonpath import FilterSelector
from comparo.core.domain.selectors.jsonpath import FunctionExpression
from comparo.core.domain.selectors.jsonpath import IndexSelector
from comparo.core.domain.selectors.jsonpath import JsonPathAst
from comparo.core.domain.selectors.jsonpath import LiteralExpression
from comparo.core.domain.selectors.jsonpath import NameSelector
from comparo.core.domain.selectors.jsonpath import NotExpression
from comparo.core.domain.selectors.jsonpath import OrExpression
from comparo.core.domain.selectors.jsonpath import PathSegment
from comparo.core.domain.selectors.jsonpath import QueryExpression
from comparo.core.domain.selectors.jsonpath import QueryOrigin
from comparo.core.domain.selectors.jsonpath import SegmentSelector
from comparo.core.domain.selectors.jsonpath import SliceSelector
from comparo.core.domain.selectors.jsonpath import StandardFunction
from comparo.core.domain.selectors.jsonpath import WildcardSelector
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import json_number
from comparo.core.selection.errors import SelectorSyntaxCode
from comparo.core.selection.errors import SelectorSyntaxError
from comparo.core.selection.limits import SelectorParserLimits

_COMPARISONS = ("<=", ">=", "==", "!=", "<", ">")
_HEX = frozenset("0123456789abcdefABCDEF")


@final
class JsonPathParser:
    """A bounded recursive-descent parser whose work advances with input."""

    def __init__(self, text: str, limits: SelectorParserLimits) -> None:
        """Initialize cursor and explicit complexity accounting."""
        self._text = text
        self._limits = limits
        self._position = 0
        self._depth = 0
        self._segments = 0
        self._selectors = 0
        self._expressions = 0

    def parse(self) -> JsonPathAst:
        """Parse one complete root-addressed JSONPath expression."""
        if not self._take("$"):
            self._fail(SelectorSyntaxCode.EXPECTED_ROOT)
        segments = self._parse_segments()
        if not self._at_end:
            self._fail(SelectorSyntaxCode.TRAILING_INPUT)
        return JsonPathAst(segments=segments)

    def _parse_segments(self) -> tuple[PathSegment, ...]:
        segments: list[PathSegment] = []
        while True:
            before_space = self._position
            self._skip_space()
            if self._take(".."):
                selectors = (
                    self._parse_bracket_selectors()
                    if self._peek() == "["
                    else (self._parse_shorthand_selector(),)
                )
                segments.append(DescendantSegment(selectors=selectors))
            elif self._take("."):
                selector = self._parse_shorthand_selector()
                segments.append(ChildSegment(selectors=(selector,)))
            elif self._peek() == "[":
                segments.append(ChildSegment(selectors=self._parse_bracket_selectors()))
            else:
                self._position = before_space
                break
            self._segments += 1
            if self._segments > self._limits.max_segments:
                self._fail(SelectorSyntaxCode.TOO_COMPLEX)
        return tuple(segments)

    def _parse_shorthand_selector(self) -> SegmentSelector:
        if self._take("*"):
            return self._count_selector(WildcardSelector())
        start = self._position
        if not self._is_name_first(self._peek()):
            self._fail(SelectorSyntaxCode.EXPECTED_SELECTOR)
        self._position += 1
        while self._is_name_character(self._peek()):
            self._position += 1
        return self._count_selector(NameSelector(name=self._text[start : self._position]))

    def _parse_bracket_selectors(self) -> tuple[SegmentSelector, ...]:
        self._expect("[")
        selectors: list[SegmentSelector] = []
        while True:
            self._skip_space()
            selectors.append(self._parse_bracket_selector())
            self._skip_space()
            if not self._take(","):
                break
        self._expect("]")
        return tuple(selectors)

    def _parse_bracket_selector(self) -> SegmentSelector:
        character = self._peek()
        if character in {'"', "'"}:
            return self._count_selector(NameSelector(name=self._parse_string()))
        if self._take("*"):
            return self._count_selector(WildcardSelector())
        if self._take("?"):
            self._enter()
            predicate = self._parse_or_expression()
            self._leave()
            return self._count_selector(FilterSelector(predicate=predicate))
        return self._count_selector(self._parse_index_or_slice())

    def _parse_index_or_slice(self) -> IndexSelector | SliceSelector:
        start = self._parse_optional_integer()
        self._skip_space()
        if not self._take(":"):
            if start is None:
                self._fail(SelectorSyntaxCode.INVALID_NUMBER)
            return IndexSelector(index=start)
        self._skip_space()
        stop = self._parse_optional_integer()
        self._skip_space()
        step: int | None = None
        if self._take(":"):
            self._skip_space()
            step = self._parse_optional_integer()
            self._skip_space()
        return SliceSelector(start=start, stop=stop, step=step)

    def _parse_optional_integer(self) -> int | None:
        start = self._position
        if self._take("-") and not self._peek().isdigit():
            self._position = start
            return None
        while self._peek().isdigit():
            self._position += 1
        if self._position == start:
            return None
        token = self._text[start : self._position]
        digits = token.removeprefix("-")
        if len(digits) > self._limits.max_integer_digits:
            self._fail(SelectorSyntaxCode.TOO_COMPLEX, start)
        if len(digits) > 1 and digits.startswith("0"):
            self._fail(SelectorSyntaxCode.INVALID_NUMBER, start)
        if token.startswith("-0"):
            self._fail(SelectorSyntaxCode.INVALID_NUMBER, start)
        self._require_interoperable_integer(token, start)
        return int(token)

    def _parse_or_expression(self) -> FilterExpression:
        left = self._parse_and_expression()
        terms = 1
        while True:
            self._skip_space()
            if not self._take("||"):
                return left
            terms += 1
            if terms > self._limits.max_depth:
                self._fail(SelectorSyntaxCode.TOO_DEEP)
            left = self._count_expression(
                OrExpression(left=left, right=self._parse_and_expression())
            )

    def _parse_and_expression(self) -> FilterExpression:
        left = self._parse_basic_expression()
        terms = 1
        while True:
            self._skip_space()
            if not self._take("&&"):
                return left
            terms += 1
            if terms > self._limits.max_depth:
                self._fail(SelectorSyntaxCode.TOO_DEEP)
            left = self._count_expression(
                AndExpression(left=left, right=self._parse_basic_expression())
            )

    def _parse_basic_expression(self) -> FilterExpression:
        self._skip_space()
        if self._take("!"):
            if self._take("="):
                self._fail(SelectorSyntaxCode.INVALID_EXPRESSION, self._position - 2)
            self._skip_space()
            if self._peek() == "!":
                self._fail(SelectorSyntaxCode.INVALID_EXPRESSION)
            self._enter()
            operand = (
                self._parse_parenthesized_expression()
                if self._peek() == "("
                else self._parse_operand()
            )
            self._leave()
            if type(operand) not in {
                QueryExpression,
                FunctionExpression,
                OrExpression,
                AndExpression,
                ComparisonExpression,
                NotExpression,
            }:
                self._fail(SelectorSyntaxCode.INVALID_EXPRESSION)
            return self._count_expression(NotExpression(operand=operand))
        if self._peek() == "(":
            return self._parse_parenthesized_expression()
        left = self._parse_operand()
        self._skip_space()
        operator = next((candidate for candidate in _COMPARISONS if self._take(candidate)), None)
        if operator is None:
            return left
        right = self._parse_operand()
        return self._count_expression(
            ComparisonExpression(
                left=left,
                operator=ComparisonOperator(operator),
                right=right,
            )
        )

    def _parse_parenthesized_expression(self) -> FilterExpression:
        self._expect("(")
        self._enter()
        expression = self._parse_or_expression()
        self._skip_space()
        self._expect(")")
        self._leave()
        return expression

    def _parse_operand(self) -> FilterExpression:
        self._skip_space()
        character = self._peek()
        if character in {"$", "@"}:
            self._position += 1
            origin = QueryOrigin.ROOT if character == "$" else QueryOrigin.CURRENT
            return self._count_expression(
                QueryExpression(origin=origin, segments=self._parse_segments())
            )
        if character in {'"', "'"}:
            return self._count_expression(
                LiteralExpression(value=JsonString(value=self._parse_string()))
            )
        if character == "-" or character.isdigit():
            return self._count_expression(LiteralExpression(value=self._parse_number()))
        for token, value in (
            ("true", JsonBoolean(value=True)),
            ("false", JsonBoolean(value=False)),
            ("null", JsonNull()),
        ):
            if self._take_word(token):
                return self._count_expression(LiteralExpression(value=value))
        return self._parse_function()

    def _parse_function(self) -> FunctionExpression:
        start = self._position
        while self._peek().islower():
            self._position += 1
        name = self._text[start : self._position]
        try:
            function = StandardFunction(name)
        except ValueError:
            self._fail(SelectorSyntaxCode.INVALID_FUNCTION, start)
        self._skip_space()
        self._expect("(")
        self._enter()
        arguments: list[FilterExpression] = []
        self._skip_space()
        if self._peek() != ")":
            while True:
                arguments.append(self._parse_or_expression())
                self._skip_space()
                if not self._take(","):
                    break
        self._expect(")")
        self._leave()
        return self._count_expression(
            FunctionExpression(function=function, arguments=tuple(arguments))
        )

    def _parse_number(self) -> JsonInteger | JsonDecimal:
        start = self._position
        self._scan_integer_part(start)
        self._scan_fraction()
        self._scan_exponent()
        token = self._text[start : self._position]
        if sum(character.isdigit() for character in token) > self._limits.max_integer_digits:
            self._fail(SelectorSyntaxCode.TOO_COMPLEX, start)
        if "." not in token and "e" not in token.lower():
            return json_number(int(token))
        exponent_marker = max(token.find("e"), token.find("E"))
        if exponent_marker >= 0:
            exponent_text = token[exponent_marker + 1 :].removeprefix("+").removeprefix("-")
            if (
                len(exponent_text) > self._limits.max_integer_digits
                or int(exponent_text) > self._limits.max_integer_digits
            ):
                self._fail(SelectorSyntaxCode.TOO_COMPLEX, start)
        try:
            decimal = Decimal(token)
        except InvalidOperation:
            self._fail(SelectorSyntaxCode.INVALID_NUMBER, start)
        exponent = decimal.as_tuple().exponent
        if type(exponent) is not int or abs(exponent) > self._limits.max_integer_digits:
            self._fail(SelectorSyntaxCode.TOO_COMPLEX, start)
        if exponent >= 0 and decimal.adjusted() + 1 > self._limits.max_integer_digits:
            self._fail(SelectorSyntaxCode.TOO_COMPLEX, start)
        return json_number(decimal)

    def _scan_integer_part(self, start: int) -> None:
        self._take("-")
        if self._take("0"):
            if self._peek().isdigit():
                self._fail(SelectorSyntaxCode.INVALID_NUMBER, start)
        elif self._peek() in "123456789":
            while self._peek().isdigit():
                self._position += 1
        else:
            self._fail(SelectorSyntaxCode.INVALID_NUMBER, start)

    def _scan_fraction(self) -> None:
        if self._take("."):
            if not self._peek().isdigit():
                self._fail(SelectorSyntaxCode.INVALID_NUMBER)
            while self._peek().isdigit():
                self._position += 1

    def _scan_exponent(self) -> None:
        if self._peek() and self._peek() in "eE":
            self._position += 1
            if self._peek() and self._peek() in "+-":
                self._position += 1
            if not self._peek().isdigit():
                self._fail(SelectorSyntaxCode.INVALID_NUMBER)
            while self._peek().isdigit():
                self._position += 1

    def _parse_string(self) -> str:
        quote = self._peek()
        self._position += 1
        characters: list[str] = []
        while not self._at_end:
            character = self._peek()
            self._position += 1
            if character == quote:
                return "".join(characters)
            if ord(character) < 0x20:
                self._fail(SelectorSyntaxCode.INVALID_UNICODE, self._position - 1)
            if character == "\\":
                characters.append(self._parse_escape(quote))
            elif 0xD800 <= ord(character) <= 0xDFFF:
                self._fail(SelectorSyntaxCode.INVALID_UNICODE, self._position - 1)
            else:
                characters.append(character)
        return self._fail(SelectorSyntaxCode.EXPECTED_SYNTAX)

    def _parse_escape(self, quote: str) -> str:
        if self._at_end:
            self._fail(SelectorSyntaxCode.INVALID_ESCAPE)
        character = self._peek()
        self._position += 1
        simple = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "/": "/", "\\": "\\"}
        if character in simple:
            return simple[character]
        if character == quote:
            return quote
        if character == "u":
            return self._parse_unicode_escape()
        return self._fail(SelectorSyntaxCode.INVALID_ESCAPE, self._position - 2)

    def _parse_unicode_escape(self) -> str:
        first_offset = self._position - 2
        first = self._take_hex_quad(first_offset)
        if 0xDC00 <= first <= 0xDFFF:
            self._fail(SelectorSyntaxCode.INVALID_UNICODE, first_offset)
        if not 0xD800 <= first <= 0xDBFF:
            return chr(first)
        if not self._take("\\u"):
            self._fail(SelectorSyntaxCode.INVALID_UNICODE, first_offset)
        second = self._take_hex_quad(first_offset)
        if not 0xDC00 <= second <= 0xDFFF:
            self._fail(SelectorSyntaxCode.INVALID_UNICODE, first_offset)
        return chr(0x10000 + ((first - 0xD800) << 10) + second - 0xDC00)

    def _take_hex_quad(self, offset: int) -> int:
        token = self._text[self._position : self._position + 4]
        if len(token) != 4 or any(character not in _HEX for character in token):
            self._fail(SelectorSyntaxCode.INVALID_ESCAPE, offset)
        self._position += 4
        return int(token, 16)

    def _count_selector(self, selector: SegmentSelector) -> SegmentSelector:
        self._selectors += 1
        if self._selectors > self._limits.max_selectors:
            self._fail(SelectorSyntaxCode.TOO_COMPLEX)
        return selector

    def _count_expression[ExpressionT: FilterExpression](
        self,
        expression: ExpressionT,
    ) -> ExpressionT:
        self._expressions += 1
        if self._expressions > self._limits.max_expressions:
            self._fail(SelectorSyntaxCode.TOO_COMPLEX)
        return expression

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > self._limits.max_depth:
            self._fail(SelectorSyntaxCode.TOO_DEEP)

    def _leave(self) -> None:
        self._depth -= 1

    def _take_word(self, token: str) -> bool:
        if not self._text.startswith(token, self._position):
            return False
        end = self._position + len(token)
        if self._is_name_character(self._text[end : end + 1]):
            return False
        self._position = end
        return True

    def _expect(self, token: str) -> None:
        self._skip_space()
        if not self._take(token):
            self._fail(SelectorSyntaxCode.EXPECTED_SYNTAX)

    def _take(self, token: str) -> bool:
        if self._text.startswith(token, self._position):
            self._position += len(token)
            return True
        return False

    def _skip_space(self) -> None:
        while self._peek() and self._peek() in " \t\n\r":
            self._position += 1

    def _peek(self) -> str:
        return self._text[self._position : self._position + 1]

    @property
    def _at_end(self) -> bool:
        return self._position >= len(self._text)

    @staticmethod
    def _is_name_first(character: str) -> bool:
        return (
            bool(character)
            and not 0xD800 <= ord(character) <= 0xDFFF
            and (character == "_" or character.isalpha() or ord(character) >= 0x80)
        )

    @classmethod
    def _is_name_character(cls, character: str) -> bool:
        return cls._is_name_first(character) or (bool(character) and character.isdigit())

    def _require_interoperable_integer(self, token: str, offset: int) -> None:
        digits = token.removeprefix("-")
        maximum = "9007199254740991"
        if len(digits) > len(maximum) or (len(digits) == len(maximum) and digits > maximum):
            self._fail(SelectorSyntaxCode.INVALID_NUMBER, offset)

    def _fail(self, code: SelectorSyntaxCode, offset: int | None = None) -> Never:
        raise SelectorSyntaxError(code, self._position if offset is None else offset)
