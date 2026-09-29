package tokenizer

import java.nio.charset.{Charset, StandardCharsets}
import java.time.{LocalDate, LocalDateTime, ZoneOffset}
import java.time.format.DateTimeFormatter

import scala.collection.immutable.Seq
import scala.collection.mutable

/**
 * Scala 2.13 port of the Python `Tokenizer` module.
 *
 * Public API required by `task.md`:
 *   - TokenType, Token, BaseTokenizer
 *   - StringTokenizer, NumericTokenizer, TemporalTokenizer
 *   - UniversalTokenizer, WhitespaceTokenizer, TokenizerBuilder
 *   - tokenize, tokenizeBatch, toToken, withMetadata
 */

/**
 * Categories of token emitted by the various tokenizers.
 *
 * Each member carries the canonical lower-case name as a `val value: String`,
 * mirroring the Python `TokenType(str, Enum)` API (`TokenType.STRING.value == "string"`).
 */
sealed trait TokenType extends Product with Serializable {
  def value: String
}

object TokenType {
  case object STRING    extends TokenType { val value: String = "string" }
  case object NUMERIC   extends TokenType { val value: String = "numeric" }
  case object TEMPORAL  extends TokenType { val value: String = "temporal" }
  case object STRUCTURED extends TokenType { val value: String = "structured" }
  case object BINARY    extends TokenType { val value: String = "binary" }
  case object NULL      extends TokenType { val value: String = "null" }

  val all: Seq[TokenType] = Seq(STRING, NUMERIC, TEMPORAL, STRUCTURED, BINARY, NULL)
}

/**
 * Immutable token representation. Mirrors the Python `@dataclass(frozen=True) Token`.
 */
final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: Map[String, Any] = Map.empty
) {

  /** Return a new token with additional metadata entries merged in. */
  def withMetadata(entries: (String, Any)*): Token =
    if (entries.isEmpty) this
    else copy(metadata = metadata ++ entries.toMap)
}

/**
 * Companion helpers that work on any value the tokenizers understand.
 */
object Token {

  /** A `NULL` token, used as the canonical representation for absent values. */
  val nullToken: Token = Token("NULL", TokenType.NULL)

  /** Convenience constructor for binary tokens. */
  def binary(bytes: Array[Byte], encoding: Charset = StandardCharsets.UTF_8): Token =
    Token(new String(bytes, encoding), TokenType.BINARY, Map("length" -> bytes.length))
}

/**
 * Generic base contract for tokenizers. Mirrors `BaseTokenizer(ABC, Generic[T])`.
 */
trait BaseTokenizer[T] {

  /** Convert a single value to a [[Token]]. */
  def tokenize(value: T): Token

  /** Convert an iterable of values, returning a materialized sequence of tokens. */
  def tokenizeBatch(values: Iterable[T]): Seq[Token] =
    values.iterator.map(tokenize).toList
}

/**
 * Type class that bounds which numeric types may be tokenized.
 */
sealed trait NumericValue {
  def render(precision: Int): String
  def typeName: String
}

object NumericValue {
  def of(value: Any): Option[NumericValue] = value match {
    case i: Int     => Some(IntValue(i))
    case l: Long    => Some(LongValue(l))
    case s: Short   => Some(ShortValue(s))
    case b: Byte    => Some(ByteValue(b))
    case d: Double  => Some(DoubleValue(d))
    case f: Float   => Some(FloatValue(f))
    case bd: BigDecimal => Some(BigDecimalValue(bd))
    case bd: java.math.BigDecimal => Some(BigDecimalValue(BigDecimal(bd)))
    case _ => None
  }

  final case class IntValue(v: Int)        extends NumericValue { override def render(p: Int) = v.toString; override def typeName = "Int" }
  final case class LongValue(v: Long)      extends NumericValue { override def render(p: Int) = v.toString; override def typeName = "Long" }
  final case class ShortValue(v: Short)    extends NumericValue { override def render(p: Int) = v.toString; override def typeName = "Short" }
  final case class ByteValue(v: Byte)      extends NumericValue { override def render(p: Int) = v.toString; override def typeName = "Byte" }
  final case class DoubleValue(v: Double)  extends NumericValue { override def render(p: Int) = BigDecimal(v).setScale(p, BigDecimal.RoundingMode.HALF_UP).toString; override def typeName = "Double" }
  final case class FloatValue(v: Float)    extends NumericValue { override def render(p: Int) = BigDecimal(v.toDouble).setScale(p, BigDecimal.RoundingMode.HALF_UP).toString; override def typeName = "Float" }
  final case class BigDecimalValue(v: BigDecimal) extends NumericValue {
    override def render(p: Int) = v.setScale(p, BigDecimal.RoundingMode.HALF_UP).toString
    override def typeName = "BigDecimal"
  }
}

/**
 * Tokenizer for numeric values with precision handling.
 */
final case class NumericTokenizer(
    precision: Int = 6,
    formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[NumericValue] {

  override def tokenize(value: NumericValue): Token = {
    val meta: Map[String, Any] = Map[String, Any](
      "original_type" -> value.typeName,
      "precision" -> precision
    ) ++ formatOptions
    Token(value.render(precision), TokenType.NUMERIC, meta)
  }
}

/**
 * Sealed hierarchy for date/time values handled by [[TemporalTokenizer]].
 */
sealed trait TemporalValue {
  def format(formatter: DateTimeFormatter): String
  def typeName: String
}

object TemporalValue {
  def of(value: Any): Option[TemporalValue] = value match {
    case d: LocalDateTime => Some(DateTimeWrapper(d))
    case d: LocalDate     => Some(DateWrapper(d))
    case d: java.time.Instant =>
      Some(DateTimeWrapper(LocalDateTime.ofInstant(d, ZoneOffset.UTC)))
    case d: java.util.Date =>
      Some(DateTimeWrapper(LocalDateTime.ofInstant(d.toInstant, ZoneOffset.UTC)))
    case _ => None
  }

  final case class DateTimeWrapper(value: LocalDateTime) extends TemporalValue {
    override def format(formatter: DateTimeFormatter): String = value.format(formatter)
    override def typeName: String = "LocalDateTime"
  }

  final case class DateWrapper(value: LocalDate) extends TemporalValue {
    override def format(formatter: DateTimeFormatter): String = value.format(formatter)
    override def typeName: String = "LocalDate"
  }
}

/**
 * Tokenizer for date/time values. Uses an explicit formatter instead of Python's
 * `strftime` format codes to stay on the `java.time` API.
 */
final case class TemporalTokenizer(
    format: Option[DateTimeFormatter] = None,
    isoFormat: DateTimeFormatter =
      DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss"),
    dateFormat: DateTimeFormatter =
      DateTimeFormatter.ofPattern("yyyy-MM-dd")
) extends BaseTokenizer[TemporalValue] {

  override def tokenize(value: TemporalValue): Token = {
    val formatter = format.getOrElse {
      value match {
        case _: TemporalValue.DateTimeWrapper => isoFormat
        case _: TemporalValue.DateWrapper     => dateFormat
      }
    }
    Token(value.format(formatter), TokenType.TEMPORAL, Map[String, Any]("original_type" -> value.typeName))
  }
}

/**
 * Tokenizer for textual values (`String` or `Array[Byte]`).
 */
final case class StringTokenizer(
    encoding: Charset = StandardCharsets.UTF_8,
    normalizer: String => String = identity
) extends BaseTokenizer[Either[String, Array[Byte]]] {

  /** Tokenize a raw string. */
  def apply(value: String): Token = tokenize(Left(value))

  /** Tokenize a byte array using the configured encoding. */
  def apply(bytes: Array[Byte]): Token = tokenize(Right(bytes))

  override def tokenize(value: Either[String, Array[Byte]]): Token = value match {
    case Left(str)    => Token(normalizer(str), TokenType.STRING)
    case Right(bytes) => Token(
      normalizer(new String(bytes, encoding)),
      TokenType.STRING,
      Map[String, Any]("encoding" -> encoding.name(), "byteLength" -> bytes.length)
    )
  }
}

/**
 * A tokenizer that knows how to render itself as a token. Mirrors `Tokenizable`.
 */
trait Tokenizable {
  def toToken: String
}

/**
 * Adapter turning any `toString`-able value into a [[Tokenizable]].
 */
final case class ToTokenizable(value: Any) extends Tokenizable {
  override def toToken: String = String.valueOf(value)
}

/**
 * Tokenizer that dispatches a value to the most specific concrete tokenizer it can
 * find. Equivalent to the Python `UniversalTokenizer` runtime dispatch.
 */
final class UniversalTokenizer(
    val stringTokenizer: StringTokenizer = StringTokenizer(),
    val numericTokenizer: NumericTokenizer = NumericTokenizer(),
    val temporalTokenizer: TemporalTokenizer = TemporalTokenizer()
) extends BaseTokenizer[Any] {

  override def tokenize(value: Any): Token = value match {
    case null                                => Token.nullToken
    case t: Tokenizable                      => Token(t.toToken, TokenType.STRUCTURED, Map[String, Any]("source" -> "Tokenizable"))
    case t: Token                            => t
    case Left(_) | Right(_)                  => stringTokenizer.tokenize(value.asInstanceOf[Either[String, Array[Byte]]])
    case s: String                           => stringTokenizer.tokenize(Left(s))
    case bytes: Array[Byte]                  => stringTokenizer.tokenize(Right(bytes))
    case nv: NumericValue                    => numericTokenizer.tokenize(nv)
    case tv: TemporalValue                   => temporalTokenizer.tokenize(tv)
    case other if NumericValue.of(other).isDefined   => numericTokenizer.tokenize(NumericValue.of(other).get)
    case other if TemporalValue.of(other).isDefined  => temporalTokenizer.tokenize(TemporalValue.of(other).get)
    case _                                   => fallback(value)
  }

  private def fallback(value: Any): Token =
    Token(String.valueOf(value), TokenType.STRING, Map[String, Any]("fallback" -> true))

  /** The canonical NULL token — mirrors the Python `tokenize_none` style helper. */
  def tokenizeNull: Token = Token.nullToken
}

object UniversalTokenizer {
  def apply(): UniversalTokenizer = new UniversalTokenizer()
}

/**
 * Covariant container for tokenizable inputs. Mirrors the Python `TokenContainer`.
 */
final class TokenContainer[T](items: Seq[T]) {
  private val _items: Vector[T] = items.toVector

  def getAll: Seq[T] = _items

  def mapTokens(func: T => String): Seq[String] = _items.map(func).toList

  def size: Int = _items.size
}

/**
 * Registry mapping string keys to typed token containers and a list of handlers.
 * Mirrors the Python `TokenRegistry`.
 */
final class TokenRegistry[T] {

  private val registry = mutable.Map.empty[String, TokenContainer[T]]
  private val handlers = mutable.ArrayBuffer.empty[T => Option[Token]]

  def register(key: String, container: TokenContainer[T]): Unit = {
    registry(key) = container
  }

  def addHandler(handler: T => Option[Token]): Unit = {
    handlers += handler
  }

  def process(key: String): Seq[Option[Token]] = {
    registry.get(key) match {
      case None => Seq.empty
      case Some(container) =>
        val handlerList = handlers.toList
        container.getAll.map { item =>
          val matched = handlerList.iterator.flatMap(_(item)).take(1).toList
          matched.headOption
        }
    }
  }
}

/**
 * Simple functor wrapper around a value. Mirrors `TokenFunctor`.
 */
final class TokenFunctor[T](value: T) {
  private val _value: T = value

  def map[U](func: T => U): TokenFunctor[U] = new TokenFunctor(func(_value))

  def flatMap[U](func: T => TokenFunctor[U]): TokenFunctor[U] = func(_value)

  def getOrElse(default: => T): T =
    if (_value == null) default else _value

  def valueOrNull: T = _value

  /** Unwraps the wrapped value, throwing if absent. */
  def get: T = _value
}

/**
 * Whitespace tokenizer. Splits input text by runs of whitespace and applies
 * optional normalization (lowercasing, punctuation stripping, length filtering).
 */
final case class WhitespaceTokenizer(
    lowercase: Boolean = false,
    minLength: Int = 0,
    maxLength: Option[Int] = None,
    stripPunctuation: Boolean = false,
    punctuation: String = ".,!?;:'\"()[]{}"
) {

  private val punctuationSet: Set[Char] = punctuation.toSet

  private def processWord(word: String): Option[String] = {
    val stripped =
      if (stripPunctuation) word.filterNot(punctuationSet.contains)
      else word
    val lowered =
      if (lowercase) stripped.toLowerCase
      else stripped
    if (lowered.length < minLength) None
    else {
      val truncated = maxLength.filter(lowered.length > _).map(lowered.take).getOrElse(lowered)
      if (truncated.isEmpty) None else Some(truncated)
    }
  }

  def tokenize(text: String): Seq[Token] = {
    val words = text.split("\\s+").toIndexedSeq
    words.zipWithIndex.collect {
      case (word, idx) if processWord(word).isDefined =>
        Token(
          value = processWord(word).get,
          tokenType = TokenType.STRING,
          metadata = Map[String, Any]("position" -> idx, "original" -> word)
        )
    }
  }

  def tokenizeToStrings(text: String): Seq[String] =
    tokenize(text).map(_.value)

  /** Return `(token, startPosition, endPosition)` tuples, matching Python `tokenize_with_positions`. */
  def tokenizeWithPositions(text: String): Seq[(String, Int, Int)] = {
    val result = mutable.ArrayBuffer.empty[(String, Int, Int)]
    var current = 0
    for (word <- text.split("\\s+")) {
      val start = text.indexOf(word, current)
      if (start >= 0) {
        val end = start + word.length
        processWord(word).foreach(t => result += ((t, start, end)))
        current = end
      } else {
        current += word.length + 1
      }
    }
    result.toList
  }

  def countTokens(text: String): Int = tokenize(text).size
}

/**
 * Fluent builder for creating a tokenizer function. Mirrors `TokenizerBuilder`.
 */
final class TokenizerBuilder[T] {

  private val normalizers = mutable.ArrayBuffer.empty[String => String]
  private val validators = mutable.ArrayBuffer.empty[T => Boolean]
  private val metadata   = mutable.Map.empty[String, Any]

  def withNormalizer(f: String => String): this.type = {
    normalizers += f
    this
  }

  def withValidator(f: T => Boolean): this.type = {
    validators += f
    this
  }

  def withMetadata(entries: (String, Any)*): this.type = {
    metadata ++= entries.toMap
    this
  }

  def build(): T => Token = {
    val normSnapshot = normalizers.toList
    val validSnapshot = validators.toList
    val metaSnapshot = metadata.toMap

    (value: T) => {
      if (!validSnapshot.forall(_(value))) {
        throw new IllegalArgumentException(s"Validation failed for $value")
      }
      var str = String.valueOf(value)
      normSnapshot.foreach(f => str = f(str))
      Token(str, TokenType.STRING, metaSnapshot)
    }
  }
}

object TokenizerBuilder {
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]
}

/**
 * Recursive JSON value type. Equivalent to the Python recursive `JsonValue` alias.
 */
sealed trait JsonValue
object JsonValue {
  final case class JString(value: String)    extends JsonValue
  final case class JNumber(value: BigDecimal) extends JsonValue
  final case class JBoolean(value: Boolean)  extends JsonValue
  case object JNull                           extends JsonValue
  final case class JArray(items: List[JsonValue]) extends JsonValue
  final case class JObject(fields: Map[String, JsonValue]) extends JsonValue

  private def escape(s: String): String =
    s.flatMap {
      case '"'  => "\\\""
      case '\\' => "\\\\"
      case '\n' => "\\n"
      case '\r' => "\\r"
      case '\t' => "\\t"
      case c    => c.toString
    }

  /** Encode a [[JsonValue]] using a minimal hand-rolled renderer. */
  def render(value: JsonValue, pretty: Boolean = false): String = {
    val sb = new StringBuilder
    val indentUnit = "  "
    def write(v: JsonValue, depth: Int): Unit = v match {
      case JString(s)  => sb.append('"').append(escape(s)).append('"')
      case JNumber(n)  => sb.append(n.toString)
      case JBoolean(b) => sb.append(b.toString)
      case JNull       => sb.append("null")
      case JArray(items) =>
        if (items.isEmpty) sb.append("[]")
        else {
          sb.append('[')
          if (pretty) sb.append('\n')
          items.zipWithIndex.foreach { case (it, i) =>
            if (pretty) sb.append(indentUnit * (depth + 1))
            write(it, depth + 1)
            if (i < items.size - 1) sb.append(',')
            if (pretty) sb.append('\n')
          }
          if (pretty) sb.append(indentUnit * depth)
          sb.append(']')
        }
      case JObject(fields) =>
        if (fields.isEmpty) sb.append("{}")
        else {
          sb.append('{')
          if (pretty) sb.append('\n')
          val entries = fields.toList
          entries.zipWithIndex.foreach { case ((k, v), i) =>
            if (pretty) sb.append(indentUnit * (depth + 1))
            sb.append('"').append(escape(k)).append('"').append(':')
            if (pretty) sb.append(' ')
            write(v, depth + 1)
            if (i < entries.size - 1) sb.append(',')
            if (pretty) sb.append('\n')
          }
          if (pretty) sb.append(indentUnit * depth)
          sb.append('}')
        }
    }
    write(value, 0)
    sb.toString
  }
}

/**
 * Tokenizer for JSON-like structures. Mirrors `JsonTokenizer`.
 */
final case class JsonTokenizer(pretty: Boolean = false) {

  /** Convert any value to a [[JsonValue]] before rendering. */
  def tokenize(value: Any): Token =
    Token(JsonValue.render(toJsonValue(value), pretty), TokenType.STRUCTURED, Map[String, Any]("json" -> true))

  /** Tokenize a value located at a dotted JSON path. */
  def tokenizePath(value: JsonValue, path: String): Option[Token] = {
    val parts = path.split('.').toList
    @scala.annotation.tailrec
    def loop(current: JsonValue, rest: List[String]): Option[JsonValue] = rest match {
      case Nil => Some(current)
      case head :: tail => current match {
        case JsonValue.JObject(fields) if fields.contains(head) =>
          loop(fields(head), tail)
        case JsonValue.JArray(items) if head.forall(_.isDigit) =>
          val idx = head.toInt
          if (idx >= 0 && idx < items.size) loop(items(idx), tail)
          else None
        case _ => None
      }
    }
    loop(value, parts).map(tokenize)
  }

  private def toJsonValue(v: Any): JsonValue = v match {
    case null                     => JsonValue.JNull
    case s: String                => JsonValue.JString(s)
    case b: Boolean               => JsonValue.JBoolean(b)
    case i: Int                   => JsonValue.JNumber(BigDecimal(i))
    case l: Long                  => JsonValue.JNumber(BigDecimal(l))
    case d: Double                => JsonValue.JNumber(BigDecimal(d))
    case f: Float                 => JsonValue.JNumber(BigDecimal(f.toDouble))
    case bd: BigDecimal           => JsonValue.JNumber(bd)
    case bd: java.math.BigDecimal => JsonValue.JNumber(BigDecimal(bd))
    case list: Seq[_]             => JsonValue.JArray(list.toList.map(toJsonValue))
    case map: Map[_, _]           => JsonValue.JObject(
      map.toList.map { case (k, v) => String.valueOf(k) -> toJsonValue(v) }.toMap
    )
    case Some(inner)              => toJsonValue(inner)
    case None                     => JsonValue.JNull
    case other                    => JsonValue.JString(String.valueOf(other))
  }
}

/**
 * Top-level helpers required by the task description:
 *   `tokenize`, `tokenizeBatch`, `toToken`, `withMetadata`, `tokenizeNull`.
 */
object api {
  private val universal = UniversalTokenizer()

  /** Tokenize a single value using the universal dispatcher. */
  def tokenize(value: Any): Token = universal.tokenize(value)

  /** Tokenize a batch of values. */
  def tokenizeBatch(values: Iterable[Any]): Seq[Token] =
    universal.tokenizeBatch(values)

  /** Convenience: get the underlying string value of a token. */
  def toToken(value: Any): String =
    universal.tokenize(value).value

  /** Build a new token with additional metadata merged on top. */
  def withMetadata(token: Token, entries: (String, Any)*): Token =
    token.withMetadata(entries: _*)

  /** Return the canonical NULL token. */
  def tokenizeNull: Token = Token.nullToken
}