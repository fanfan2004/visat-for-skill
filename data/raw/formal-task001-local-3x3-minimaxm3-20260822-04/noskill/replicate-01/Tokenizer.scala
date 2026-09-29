package tokenizer

import java.nio.charset.Charset
import java.time.{LocalDate, LocalDateTime, OffsetDateTime, ZonedDateTime}
import java.time.format.DateTimeFormatter
import java.time.temporal.TemporalAccessor

import io.circe.Json

import scala.collection.mutable
import scala.collection.immutable

// ============================================================================
//  Type tokens
// ============================================================================

/**
 * Enumerates the high-level categories produced by every tokenizer in this
 * module. Modelled as a sealed family so the compiler can verify exhaustive
 * pattern matches while still allowing us to surface the human readable
 * label used by the original Python implementation.
 */
sealed trait TokenType extends Product with Serializable {
  def value: String
}

object TokenType {

  case object STRING     extends TokenType { val value = "string"     }
  case object NUMERIC    extends TokenType { val value = "numeric"    }
  case object TEMPORAL   extends TokenType { val value = "temporal"   }
  case object STRUCTURED extends TokenType { val value = "structured" }
  case object BINARY     extends TokenType { val value = "binary"     }
  case object NULL       extends TokenType { val value = "null"       }

  /** Alias kept for readability; the Python module exposed the same `label`. */
  def label(t: TokenType): String = t.value

  /** All token types, useful for iteration and JSON helpers. */
  val values: immutable.IndexedSeq[TokenType] =
    immutable.IndexedSeq(STRING, NUMERIC, TEMPORAL, STRUCTURED, BINARY, NULL)

  /** Look up a [[TokenType]] by its stable label. */
  def fromLabel(label: String): Option[TokenType] =
    values.find(_.value == label)
}

// ============================================================================
//  Core token model
// ============================================================================

/**
 * Immutable, fully populated representation of a single token. Mirrors the
 * Python `Token` dataclass: every value carries its textual form, the type
 * tag and an arbitrary metadata map.
 */
final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: immutable.Map[String, Any] = immutable.Map.empty
) {

  /** Returns a new [[Token]] with additional metadata merged in. */
  def withMetadata(extra: (String, Any)*): Token =
    copy(metadata = metadata ++ extra)

  /** Returns a new [[Token]] with a metadata map merged in. */
  def withMetadata(extra: immutable.Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)

  /** Returns a new [[Token]] with the supplied metadata key removed. */
  def withoutMetadata(key: String): Token =
    copy(metadata = metadata - key)
}

object Token {

  /** Convenience constructor for the overwhelmingly common case with no metadata. */
  def apply(value: String, tokenType: TokenType): Token =
    Token(value, tokenType, immutable.Map.empty)
}

/** A `Tokenizable` is any type that knows how to render itself as a token string. */
trait Tokenizable {
  def toToken: String
}

/** Anything that exposes a length (string, list, array, …). */
trait HasLength {
  def length: Int
}

/** A contravariant consumer of tokens. */
trait TokenProcessor[-T] {
  def process(item: T): Unit
}

// ============================================================================
//  Mutable batch
// ============================================================================

/**
 * Mutable counterpart of [[Token]]. Demonstrates an API that exposes a
 * pre-allocation buffer with a strict "process once" invariant.
 */
final class MutableTokenBatch {

  private var _processed: Boolean = false
  val tokens: mutable.ListBuffer[Token] = mutable.ListBuffer.empty

  def add(token: Token): Unit =
    if (_processed) throw new IllegalStateException("Batch already processed")
    else tokens += token

  def markProcessed(): Unit = { _processed = true }

  def isProcessed: Boolean = _processed

  def snapshot: immutable.List[Token] = tokens.toList
}

// ============================================================================
//  Generic containers (covariant, contravariant, invariant)
// ============================================================================

/** A read-only, covariant container of tokenizable items. */
final class TokenContainer[+T](val items: immutable.Seq[T]) {

  def getAll: immutable.Seq[T] = items

  def size: Int = items.size

  def length: Int = items.size

  def isEmpty: Boolean = items.isEmpty

  def mapTokens(func: T => String): immutable.IndexedSeq[String] =
    items.map(func).toIndexedSeq
}

object TokenContainer {

  def apply[T](items: immutable.Seq[T]): TokenContainer[T] =
    new TokenContainer(items.toVector)

  def empty[T]: TokenContainer[T] = new TokenContainer(immutable.Vector.empty)
}

/** A contravariant sink — anything you put in, you can later pull out as `Any`. */
final class TokenSink[-T] {

  private val received: mutable.ListBuffer[Any] = mutable.ListBuffer.empty

  def receive(item: T): Unit = received += item

  def drain(): immutable.List[Any] = {
    val snapshot = received.toList
    received.clear()
    snapshot
  }

  def peek: immutable.List[Any] = received.toList
}

/** A tiny invariant reference cell. */
final class BivariantHandler[T](default: T) {

  private var _value: T = default

  def get: T = _value

  def set(value: T): Unit = { _value = value }

  def transform(func: T => T): T = {
    _value = func(_value)
    _value
  }
}

// ============================================================================
//  Base tokenizer
// ============================================================================

/**
 * Common contract for tokenizers. Every concrete tokenizer knows how to turn
 * a single value of its input type into a [[Token]] and, thanks to the
 * default implementation of [[tokenizeBatch]], a batch of them.
 */
abstract class BaseTokenizer[T] {

  def tokenize(value: T): Token

  /** Strict, eager batch tokenization. */
  def tokenizeBatch(values: Iterable[T]): immutable.IndexedSeq[Token] =
    values.iterator.map(tokenize).toIndexedSeq

  /** Lazy variant, useful for very large inputs. */
  def tokenizeLazy(values: Iterable[T]): Iterator[Token] =
    values.iterator.map(tokenize)
}

// ============================================================================
//  String tokenizer
// ============================================================================

/**
 * Tokenizer for `str`/`bytes`-like inputs. Bytes are decoded with the
 * configured character set and the resulting string is run through an
 * optional normalizer before being wrapped in a [[Token]].
 */
class StringTokenizer(
    val encoding: String = "UTF-8",
    val normalizer: String => String = identity
) extends BaseTokenizer[StringTokenizer.StrOrBytes] {

  private val charset: Charset = Charset.forName(encoding)

  override def tokenize(value: StringTokenizer.StrOrBytes): Token = {
    val strValue = value match {
      case Right(s)        => s
      case Left(rawBytes)  => new String(rawBytes, charset)
    }
    Token(normalizer(strValue), TokenType.STRING)
  }
}

object StringTokenizer {

  /** Either a raw UTF string or a byte array. The Python `StrOrBytes` union. */
  type StrOrBytes = Either[Array[Byte], String]
}

// ============================================================================
//  Numeric tokenizer
// ============================================================================

/** NumericT-like union: integers, floating point values and arbitrary precision decimals. */
sealed trait NumericValue extends Product with Serializable
object NumericValue {
  final case class OfInt(value: Int)               extends NumericValue
  final case class OfLong(value: Long)             extends NumericValue
  final case class OfFloat(value: Float)           extends NumericValue
  final case class OfDouble(value: Double)         extends NumericValue
  final case class OfDecimal(value: BigDecimal)    extends NumericValue
}

/**
 * Tokenizer for any [[NumericValue]]. Floats and decimals are formatted with
 * a fixed precision while integers keep their natural representation.
 */
class NumericTokenizer(
    val precision: Int = 6,
    formatOptions: immutable.Map[String, Any] = immutable.Map.empty
) extends BaseTokenizer[NumericValue] {

  val formatOptionsView: immutable.Map[String, Any] = formatOptions

  override def tokenize(value: NumericValue): Token = {
    val strValue = value match {
      case NumericValue.OfDecimal(d) =>
        val bd = d.setScale(precision, BigDecimal.RoundingMode.HALF_UP)
        bd.toString
      case NumericValue.OfFloat(f)  => String.format(s".${precision}f", f.asInstanceOf[AnyRef])
      case NumericValue.OfDouble(d) => String.format(s".${precision}f", d.asInstanceOf[AnyRef])
      case NumericValue.OfInt(i)    => i.toString
      case NumericValue.OfLong(l)   => l.toString
    }

    val typeName = value match {
      case NumericValue.OfInt(_)     => "int"
      case NumericValue.OfLong(_)    => "int"
      case NumericValue.OfFloat(_)   => "float"
      case NumericValue.OfDouble(_)  => "float"
      case NumericValue.OfDecimal(_) => "Decimal"
    }

    Token(strValue, TokenType.NUMERIC, immutable.Map("original_type" -> typeName))
  }
}

// ============================================================================
//  Temporal tokenizer
// ============================================================================

/** TemporalT-like union: any JDK time type that can format itself as a string. */
sealed trait TemporalValue {
  private[tokenizer] def format(pattern: String): String
}

object TemporalValue {
  final case class OfLocalDate(value: LocalDate) extends TemporalValue {
    private[tokenizer] def format(pattern: String): String =
      value.format(DateTimeFormatter.ofPattern(pattern))
  }
  final case class OfLocalDateTime(value: LocalDateTime) extends TemporalValue {
    private[tokenizer] def format(pattern: String): String =
      value.format(DateTimeFormatter.ofPattern(pattern))
  }
  final case class OfZonedDateTime(value: ZonedDateTime) extends TemporalValue {
    private[tokenizer] def format(pattern: String): String =
      value.format(DateTimeFormatter.ofPattern(pattern))
  }
  final case class OfOffsetDateTime(value: OffsetDateTime) extends TemporalValue {
    private[tokenizer] def format(pattern: String): String =
      value.format(DateTimeFormatter.ofPattern(pattern))
  }
  final case class OfTemporal(value: TemporalAccessor) extends TemporalValue {
    private[tokenizer] def format(pattern: String): String =
      DateTimeFormatter.ofPattern(pattern).format(value)
  }
}

/**
 * Tokenizer for date / time inputs. The format pattern is taken from the
 * caller when set, otherwise an ISO date/datetime pattern is used depending on
 * whether the value carries time information.
 */
class TemporalTokenizer(formatStr: Option[String] = None) extends BaseTokenizer[TemporalValue] {

  import TemporalTokenizer._

  val format: Option[String] = formatStr

  override def tokenize(value: TemporalValue): Token = {
    val pattern = formatStr.getOrElse {
      value match {
        case TemporalValue.OfLocalDate(_) => DateFormat
        case _                            => IsoFormat
      }
    }
    Token(value.format(pattern), TokenType.TEMPORAL)
  }
}

object TemporalTokenizer {

  /** "yyyy-MM-dd'T'HH:mm:ss" */
  val IsoFormat: String = "yyyy-MM-dd'T'HH:mm:ss"

  /** "yyyy-MM-dd" */
  val DateFormat: String = "yyyy-MM-dd"
}

// ============================================================================
//  Universal tokenizer (runtime dispatch)
// ============================================================================

/**
 * Tokenizer that, at runtime, picks the right concrete tokenizer based on
 * the dynamic type of the input. Handles `null`, [[Tokenizable]] values and
 * the four built-in categories. Falls back to `String.valueOf` for unknown
 * types.
 */
class UniversalTokenizer(
    val stringTokenizer: StringTokenizer = new StringTokenizer(),
    val numericTokenizer: NumericTokenizer = new NumericTokenizer(),
    val temporalTokenizer: TemporalTokenizer = new TemporalTokenizer()
) {

  /** Convenience: produces a NULL token. Mirrors the UniversalTokenizer.null_token Python pattern. */
  def tokenizeNull: Token = Token("NULL", TokenType.NULL)

  def tokenize(value: Any): Token = value match {
    case null =>
      tokenizeNull

    case t: Tokenizable =>
      Token(t.toToken, TokenType.STRUCTURED)

    case j: Json =>
      Token(j.noSpaces, TokenType.STRUCTURED, immutable.Map("json" -> true))

    case s: String =>
      stringTokenizer.tokenize(Right(s))

    case bytes: Array[Byte] =>
      stringTokenizer.tokenize(Left(bytes))

    case d: BigDecimal =>
      numericTokenizer.tokenize(NumericValue.OfDecimal(d))

    case d: java.math.BigDecimal =>
      numericTokenizer.tokenize(NumericValue.OfDecimal(BigDecimal(d)))

    case i: Int =>
      numericTokenizer.tokenize(NumericValue.OfInt(i))

    case l: Long =>
      numericTokenizer.tokenize(NumericValue.OfLong(l))

    case f: Float =>
      numericTokenizer.tokenize(NumericValue.OfFloat(f))

    case d: Double =>
      numericTokenizer.tokenize(NumericValue.OfDouble(d))

    case ld: LocalDate =>
      temporalTokenizer.tokenize(TemporalValue.OfLocalDate(ld))

    case ldt: LocalDateTime =>
      temporalTokenizer.tokenize(TemporalValue.OfLocalDateTime(ldt))

    case zdt: ZonedDateTime =>
      temporalTokenizer.tokenize(TemporalValue.OfZonedDateTime(zdt))

    case odt: OffsetDateTime =>
      temporalTokenizer.tokenize(TemporalValue.OfOffsetDateTime(odt))

    case other =>
      Token(String.valueOf(other), TokenType.STRING, immutable.Map("fallback" -> true))
  }
}

// ============================================================================
//  Token registry
// ============================================================================

/**
 * Registry of [[TokenContainer]]s keyed by string, with a stack of handlers
 * that can produce optional tokens from items in a container.
 */
final class TokenRegistry[T] {

  private val registry: mutable.Map[String, TokenContainer[T]] = mutable.Map.empty
  private val handlers:  mutable.ListBuffer[T => Option[Token]]  = mutable.ListBuffer.empty

  def register(key: String, container: TokenContainer[T]): Unit =
    registry(key) = container

  def addHandler(handler: T => Option[Token]): Unit =
    handlers += handler

  /** Returns one result per item in the container, in input order. */
  def process(key: String): immutable.List[Option[Token]] = {
    registry.get(key) match {
      case None => immutable.Nil
      case Some(container) =>
        container.getAll.toList.map { item =>
          handlers.iterator.map(_.apply(item)).find(_.isDefined).flatten
        }
    }
  }
}

// ============================================================================
//  Functor / monad simulations
// ============================================================================

/**
 * Minimal functor + monad wrapper. Demonstrates that the generic type
 * system in Scala can carry the same `map`/`flatMap` shape without resorting
 * to Python's duck typing.
 */
class TokenFunctor[T] private[tokenizer] (private[tokenizer] val value: T) {

  def get: T = value

  def map[U](func: T => U): TokenFunctor[U] = new TokenFunctor(func(value))

  def flatMap[U](func: T => TokenFunctor[U]): TokenFunctor[U] = func(value)

  def getOrElse[U >: T](default: => U): U = value match {
    case null => default
    case v    => v.asInstanceOf[U]
  }
}

object TokenFunctor {
  def apply[T](value: T): TokenFunctor[T] = new TokenFunctor(value)
  def pure[T](value: T): TokenFunctor[T] = new TokenFunctor(value)
}

/** Applicative-style monad that knows how to lift and apply wrapped functions. */
class TokenMonad[T](value: T) extends TokenFunctor[T](value) {

  def pure[U](value: U): TokenMonad[U] = new TokenMonad(value)

  def ap[U](funcWrapped: TokenMonad[T => U]): TokenMonad[U] =
    new TokenMonad(funcWrapped.value(value))
}

object TokenMonad {
  def pure[T](value: T): TokenMonad[T] = new TokenMonad(value)
}

// ============================================================================
//  JSON value & tokenizer
// ============================================================================

/** Recursive ADT representation of a JSON-compatible value. */
sealed trait JsonValue
object JsonValue {
  final case class Str(value: String)             extends JsonValue
  final case class Num(value: BigDecimal)         extends JsonValue
  final case class Bool(value: Boolean)           extends JsonValue
  case object Null                                extends JsonValue
  final case class Arr(values: immutable.List[JsonValue])         extends JsonValue
  final case class Obj(entries: immutable.Map[String, JsonValue]) extends JsonValue
}

/**
 * Tokenizer for [[JsonValue]] trees. Supports dotted path lookups that mirror
 * the Python `JsonTokenizer.tokenize_path`.
 */
class JsonTokenizer(val pretty: Boolean = false) {

  import JsonTokenizer._

  def tokenize(value: JsonValue): Token =
    Token(
      if (pretty) encodePretty(value) else encode(value),
      TokenType.STRUCTURED,
      immutable.Map("json" -> true)
    )

  /** Overload that accepts an `io.circe.Json` tree directly. */
  def tokenize(json: Json): Token =
    Token(
      if (pretty) json.spaces2 else json.noSpaces,
      TokenType.STRUCTURED,
      immutable.Map("json" -> true)
    )

  /** Resolve a dotted path and tokenize the value at that path, if any. */
  def tokenizePath(value: JsonValue, path: String): Option[Token] = {
    val parts = path.split('.').toList
    if (parts.isEmpty || (parts.length == 1 && parts.head.isEmpty)) None
    else navigate(value, parts).map(tokenize)
  }

  private def navigate(value: JsonValue, parts: List[String]): Option[JsonValue] = parts match {
    case Nil => Some(value)
    case part :: rest =>
      value match {
        case JsonValue.Obj(entries) =>
          entries.get(part).flatMap(child => navigate(child, rest))
        case JsonValue.Arr(values) if part.forall(_.isDigit) =>
          val idx = part.toInt
          if (idx >= 0 && idx < values.size) navigate(values(idx), rest)
          else None
        case _ => None
      }
  }
}

object JsonTokenizer {

  /** Compact encoder. */
  def encode(value: JsonValue): String = value match {
    case JsonValue.Null                => "null"
    case JsonValue.Bool(true)          => "true"
    case JsonValue.Bool(false)         => "false"
    case JsonValue.Num(n)              => n.toString
    case JsonValue.Str(s)              => quote(s)
    case JsonValue.Arr(values)         => values.iterator.map(encode).mkString("[", ",", "]")
    case JsonValue.Obj(entries)        =>
      entries.iterator
        .map { case (k, v) => quote(k) + ":" + encode(v) }
        .mkString("{", ",", "}")
  }

  /** Pretty printer, two-space indent, mirrors `json.dumps(value, indent=2)`. */
  def encodePretty(value: JsonValue): String = {
    val sb = new StringBuilder
    renderPretty(value, sb, indent = 0)
    sb.toString
  }

  private def renderPretty(value: JsonValue, sb: StringBuilder, indent: Int): Unit = {
    val pad    = "  " * indent
    val padOne = "  " * (indent + 1)
    value match {
      case JsonValue.Null        => sb.append("null")
      case JsonValue.Bool(true)  => sb.append("true")
      case JsonValue.Bool(false) => sb.append("false")
      case JsonValue.Num(n)      => sb.append(n.toString)
      case JsonValue.Str(s)      => sb.append(quote(s))
      case JsonValue.Arr(values) =>
        if (values.isEmpty) sb.append("[]")
        else {
          sb.append("[\n")
          values.zipWithIndex.foreach { case (v, idx) =>
            sb.append(padOne)
            renderPretty(v, sb, indent + 1)
            if (idx < values.size - 1) sb.append(",")
            sb.append("\n")
          }
          sb.append(pad).append("]")
        }
      case JsonValue.Obj(entries) =>
        if (entries.isEmpty) sb.append("{}")
        else {
          sb.append("{\n")
          val list = entries.toList
          list.zipWithIndex.foreach { case ((k, v), idx) =>
            sb.append(padOne).append(quote(k)).append(": ")
            renderPretty(v, sb, indent + 1)
            if (idx < list.size - 1) sb.append(",")
            sb.append("\n")
          }
          sb.append(pad).append("}")
        }
    }
  }

  private def quote(s: String): String = {
    val sb = new StringBuilder(s.length + 2)
    sb.append('"')
    s.foreach {
      case '"'  => sb.append("\\\"")
      case '\\' => sb.append("\\\\")
      case '\n' => sb.append("\\n")
      case '\r' => sb.append("\\r")
      case '\t' => sb.append("\\t")
      case c if c < ' ' => sb.append(f"\\u${c.toInt}%04x")
      case c => sb.append(c)
    }
    sb.append('"')
    sb.toString
  }
}

// ============================================================================
//  Whitespace tokenizer
// ============================================================================

/**
 * Splits a string on whitespace and emits one [[Token]] per surviving word.
 * Useful as a quick-and-dirty first step in a text processing pipeline.
 */
class WhitespaceTokenizer(
    val lowercase: Boolean = false,
    val minLength: Int = 0,
    val maxLength: Option[Int] = None,
    val stripPunctuation: Boolean = false
) {

  private val punctuation: Set[Char] =
    Set('.', ',', '!', '?', ';', ':', '\'', '"', '(', ')', '[', ']', '{', '}')

  /** Process a single word into a token string, returning `None` to drop it. */
  def processToken(word: String): Option[String] = {
    var w = word
    if (stripPunctuation) w = w.filterNot(punctuation.contains)
    if (lowercase)        w = w.toLowerCase
    if (w.length < minLength) return None
    maxLength match {
      case Some(max) if w.length > max => Some(w.take(max)).filter(_.nonEmpty)
      case _                           => Option(w).filter(_.nonEmpty)
    }
  }

  def tokenize(text: String): immutable.IndexedSeq[Token] = {
    val out = mutable.ListBuffer.empty[Token]
    var i = 0
    text.split("\\s+").foreach { word =>
      processToken(word) match {
        case Some(processed) =>
          out += Token(
            value     = processed,
            tokenType = TokenType.STRING,
            metadata  = immutable.Map("position" -> i, "original" -> word)
          )
        case None => ()
      }
      i += 1
    }
    out.toIndexedSeq
  }

  def tokenizeToStrings(text: String): immutable.IndexedSeq[String] =
    tokenize(text).map(_.value)

  def tokenizeWithPositions(text: String): immutable.IndexedSeq[(String, Int, Int)] = {
    val out = mutable.ListBuffer.empty[(String, Int, Int)]
    var cursor = 0
    text.split("\\s+").foreach { word =>
      val start = text.indexOf(word, cursor)
      val end   = start + word.length
      processToken(word).foreach(processed => out += ((processed, start, end)))
      cursor = end
    }
    out.toIndexedSeq
  }

  def countTokens(text: String): Int = tokenize(text).size
}

// ============================================================================
//  Builder
// ============================================================================

/**
 * Fluent builder that produces a single `T => Token` function after a chain
 * of normalizer, validator and metadata configuration calls.
 */
class TokenizerBuilder[T] {

  private val _normalizers: mutable.ListBuffer[String => String] = mutable.ListBuffer.empty
  private val _validators:  mutable.ListBuffer[T => Boolean]      = mutable.ListBuffer.empty
  private val _metadata:    mutable.Map[String, Any]             = mutable.Map.empty

  def withNormalizer(normalizer: String => String): this.type = {
    _normalizers += normalizer
    this
  }

  def withValidator(validator: T => Boolean): this.type = {
    _validators += validator
    this
  }

  def withMetadata(entries: (String, Any)*): this.type = {
    _metadata ++= entries
    this
  }

  /** Snapshot the current builder state so subsequent calls don't mutate the produced function. */
  def build(): T => Token = {
    val normalizers = _normalizers.toList
    val validators  = _validators.toList
    val metadata    = _metadata.toMap

    (value: T) => {
      validators.foreach { v =>
        if (!v(value))
          throw new IllegalArgumentException(s"Validation failed for $value")
      }
      var current = String.valueOf(value)
      normalizers.foreach(n => current = n(current))
      Token(current, TokenType.STRING, metadata)
    }
  }
}

object TokenizerBuilder {

  /** Sugar for `new TokenizerBuilder[T]()` so callers can write `TokenizerBuilder[T]()`. */
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]()
}

// ============================================================================
//  Top-level convenience API
// ============================================================================

/**
 * Free-standing helpers that mirror the original Python module's
 * module-level functions. Most users should be able to do all they need by
 * importing `Tokenizer._`.
 */
object Tokenizer {

  private val universal: UniversalTokenizer = new UniversalTokenizer()

  /** Run the universal tokenizer over an arbitrary value. */
  def tokenize(value: Any): Token = universal.tokenize(value)

  /** Convenience: returns the canonical NULL token. */
  def tokenizeNull: Token = universal.tokenizeNull

  /** Same as [[tokenize]] but eagerly applies it to a collection of values. */
  def tokenizeBatch(values: Iterable[Any]): immutable.IndexedSeq[Token] =
    values.iterator.map(tokenize).toIndexedSeq

  /** Lazily iterate tokens for a collection, equivalent to `tokenizeBatch(...).iterator`. */
  def tokenizeLazy(values: Iterable[Any]): Iterator[Token] =
    values.iterator.map(tokenize)

  /** Best-effort string coercion, the moral equivalent of the `to_token` helper. */
  def toToken(value: Any): String = value match {
    case null          => "NULL"
    case t: Tokenizable => t.toToken
    case other         => String.valueOf(other)
  }

  /** Build a new token by adding metadata to an existing one — top-level form. */
  def withMetadata(token: Token, entries: (String, Any)*): Token =
    token.withMetadata(entries: _*)
}