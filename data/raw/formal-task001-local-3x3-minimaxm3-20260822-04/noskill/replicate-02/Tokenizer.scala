package tokenizer

import java.nio.charset.{Charset, StandardCharsets}
import java.time.format.DateTimeFormatter
import java.time.temporal.Temporal
import java.time.{Instant, LocalDate, LocalDateTime, ZonedDateTime}
import java.util.Locale

import scala.collection.mutable

/**
 * Tokenizer module for converting various input types to standardized string tokens.
 *
 * The Scala 2.13 implementation replaces Python's dynamic typing with explicit
 * pattern matching, sealed hierarchies, and type-class style dispatch while
 * preserving the public API surface (classes, methods, top-level functions).
 */

// ============================================================================
// TokenType
// ============================================================================

/** Sealed hierarchy of token categories. Mirrors the Python `TokenType` enum. */
sealed trait TokenType {
  /** Stable label matching the original Python enum values. */
  def value: String
}

object TokenType {

  case object STRING     extends TokenType { val value = "string" }
  case object NUMERIC    extends TokenType { val value = "numeric" }
  case object TEMPORAL   extends TokenType { val value = "temporal" }
  case object STRUCTURED extends TokenType { val value = "structured" }
  case object BINARY     extends TokenType { val value = "binary" }
  case object NULL       extends TokenType { val value = "null" }

  /** Resolve a [[TokenType]] by its [[value]] string. */
  def fromValue(v: String): Option[TokenType] = v match {
    case "string"     => Some(STRING)
    case "numeric"    => Some(NUMERIC)
    case "temporal"   => Some(TEMPORAL)
    case "structured" => Some(STRUCTURED)
    case "binary"     => Some(BINARY)
    case "null"       => Some(NULL)
    case _            => None
  }

  /** All defined token types in declaration order. */
  val all: Seq[TokenType] = Seq(STRING, NUMERIC, TEMPORAL, STRUCTURED, BINARY, NULL)
}

// ============================================================================
// Tokenizable
// ============================================================================

/** Structural mixin: anything that knows how to render itself as a token. */
trait Tokenizable {
  def toToken(): String
}

// ============================================================================
// Token
// ============================================================================

/**
 * Immutable token representation. Mirrors the Python frozen dataclass.
 *
 * @param value     the string value of the token
 * @param tokenType the type of token
 * @param metadata  arbitrary additional metadata
 */
final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: Map[String, Any] = Map.empty
) {

  /** Return a new token with additional metadata merged in (varargs form). */
  def withMetadata(entries: (String, Any)*): Token =
    copy(metadata = metadata ++ entries)

  /** Return a new token with metadata merged in from a map. */
  def withMetadata(extra: Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)
}

// ============================================================================
// MutableTokenBatch
// ============================================================================

/** Mutable batch of tokens. Mirrors the Python `MutableTokenBatch` dataclass. */
final class MutableTokenBatch {

  private val _tokens = mutable.ListBuffer.empty[Token]
  private var _processed: Boolean = false

  /** Append a token to the batch. Throws if already marked as processed. */
  def add(token: Token): Unit = {
    if (_processed)
      throw new IllegalStateException("Batch already processed")
    _tokens += token
  }

  /** Mark this batch as finalized, preventing further additions. */
  def markProcessed(): Unit = {
    _processed = true
  }

  /** The tokens currently held in the batch. */
  def tokens: Seq[Token] = _tokens.toList

  /** Whether the batch has been marked as processed. */
  def processed: Boolean = _processed
}

// ============================================================================
// Generic Containers
// ============================================================================

/** Covariant container of tokenizable values. Mirrors Python's `TokenContainer`. */
final class TokenContainer[+T](items: Seq[T]) {

  private val _items: Vector[T] = items.toVector

  /** All items in the container. */
  def getAll: Vector[T] = _items

  /** Apply `func` to each item, returning the resulting list of strings. */
  def mapTokens(func: T => String): List[String] = _items.iterator.map(func).toList

  /** Number of items in the container. */
  def size: Int = _items.size
}

object TokenContainer {

  /** Build a container from a sequence of items. */
  def apply[T](items: Seq[T]): TokenContainer[T] =
    new TokenContainer[T](items)
}

/** Contravariant sink that accepts items and can be drained. */
final class TokenSink[-T] {

  private val _received = mutable.ListBuffer.empty[Any]

  /** Receive an item. */
  def receive(item: T): Unit = {
    _received += item
  }

  /** Drain and return all received items as a list. */
  def drain(): List[Any] = {
    val copy = _received.toList
    _received.clear()
    copy
  }
}

/** Invariant handler that holds a value and supports transformation. */
final class BivariantHandler[T](default: T) {

  private var _value: T = default

  /** Get the current value. */
  def get: T = _value

  /** Replace the value. */
  def set(value: T): Unit = {
    _value = value
  }

  /** Transform the value via `func`. */
  def transform(func: T => T): T = {
    _value = func(_value)
    _value
  }
}

// ============================================================================
// BaseTokenizer
// ============================================================================

/**
 * Abstract base class for all tokenizers. Generic over the input type `T`.
 *
 * Equivalent to `BaseTokenizer(ABC, Generic[T])` in Python.
 */
abstract class BaseTokenizer[T] {

  /** Convert a single value of type `T` into a [[Token]]. */
  def tokenize(value: T): Token

  /** Lazily tokenize a collection of values. */
  def tokenizeBatch(values: Iterable[T]): Iterator[Token] =
    values.iterator.map(tokenize)
}

// ============================================================================
// StringTokenizer
// ============================================================================

/**
 * Tokenizer for `String` and `Array[Byte]` values.
 *
 * The Python version accepts either `str` or `bytes`; Scala has no union
 * type, so we provide two `tokenize` overloads plus a primary one for
 * [[String]] (the most common input).
 *
 * @param encoding   charset used to decode bytes (default UTF-8)
 * @param normalizer optional normalization function applied to strings
 */
class StringTokenizer(
    encoding: Charset = StandardCharsets.UTF_8,
    normalizer: String => String = identity
) extends BaseTokenizer[String] {

  /** Auxiliary constructor that accepts an encoding by name. */
  def this(encodingName: String, normalizer: String => String) =
    this(Charset.forName(encodingName), normalizer)

  /** Auxiliary constructor with just an encoding name. */
  def this(encodingName: String) =
    this(Charset.forName(encodingName), identity)

  override def tokenize(value: String): Token =
    Token(normalizer(value), TokenType.STRING)

  /** Tokenize a byte array by decoding it with the configured charset. */
  def tokenize(value: Array[Byte]): Token =
    Token(normalizer(new String(value, encoding)), TokenType.STRING)
}

// ============================================================================
// NumericTokenizer
// ============================================================================

/**
 * Tokenizer for numeric values.
 *
 * Mirrors `NumericTokenizer` from the Python module. Supports
 * `BigDecimal`, `Double`, `Float`, `Int`, `Long`, and `BigInt` through
 * method overloading.
 *
 * @param precision     number of fractional digits to keep
 * @param formatOptions additional format options; replaces Python's
 *                      dangerous mutable default argument
 */
class NumericTokenizer(
    precision: Int = 6,
    formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[BigDecimal] {

  override def tokenize(value: BigDecimal): Token = {
    val formatted = format(value)
    Token(formatted, TokenType.NUMERIC, Map("original_type" -> "Decimal"))
  }

  def tokenize(value: Double): Token = {
    val formatted =
      if (value.isNaN || value.isInfinite) value.toString
      else format(BigDecimal(value))
    Token(formatted, TokenType.NUMERIC, Map("original_type" -> "float"))
  }

  def tokenize(value: Float): Token =
    tokenize(value.toDouble)

  def tokenize(value: Int): Token =
    Token(value.toString, TokenType.NUMERIC, Map("original_type" -> "int"))

  def tokenize(value: Long): Token =
    Token(value.toString, TokenType.NUMERIC, Map("original_type" -> "long"))

  def tokenize(value: BigInt): Token =
    Token(value.toString, TokenType.NUMERIC, Map("original_type" -> "int"))

  /** Round a BigDecimal to the configured precision, matching Python's `f"%.{precision}f"`. */
  private def format(value: BigDecimal): String =
    value.setScale(precision, BigDecimal.RoundingMode.HALF_UP).toString
}

// ============================================================================
// TemporalTokenizer
// ============================================================================

/** Constants and shared defaults for [[TemporalTokenizer]]. */
object TemporalTokenizer {

  /** ISO-like date-time format used when the input is a `LocalDateTime`/`ZonedDateTime`. */
  val ISO_FORMAT: String  = "yyyy-MM-dd'T'HH:mm:ss"

  /** ISO date format used for `LocalDate` values. */
  val DATE_FORMAT: String = "yyyy-MM-dd"
}

/**
 * Tokenizer for date/time types.
 *
 * Provides overloads for `LocalDate`, `LocalDateTime`, `ZonedDateTime`, and
 * `Instant`. The primary abstract override targets `LocalDate` so the class
 * can satisfy `BaseTokenizer[LocalDate]`.
 */
class TemporalTokenizer(formatStr: Option[String] = None)
    extends BaseTokenizer[LocalDate] {

  import TemporalTokenizer._

  override def tokenize(value: LocalDate): Token = {
    val fmt = formatStr.getOrElse(DATE_FORMAT)
    Token(value.format(DateTimeFormatter.ofPattern(fmt)), TokenType.TEMPORAL)
  }

  def tokenize(value: LocalDateTime): Token = {
    val fmt = formatStr.getOrElse(ISO_FORMAT)
    Token(value.format(DateTimeFormatter.ofPattern(fmt)), TokenType.TEMPORAL)
  }

  def tokenize(value: ZonedDateTime): Token = {
    val fmt = formatStr.getOrElse(ISO_FORMAT)
    Token(value.format(DateTimeFormatter.ofPattern(fmt)), TokenType.TEMPORAL)
  }

  def tokenize(value: Instant): Token =
    Token(value.toString, TokenType.TEMPORAL, Map("original_type" -> "Instant"))
}

// ============================================================================
// UniversalTokenizer
// ============================================================================

/**
 * Dispatcher tokenizer that handles multiple input types.
 *
 * Mirrors the Python `UniversalTokenizer` using pattern matching on
 * `Any` instead of `isinstance` chains.
 */
class UniversalTokenizer {

  private val stringTokenizer  = new StringTokenizer()
  private val numericTokenizer = new NumericTokenizer()
  private val temporalTokenizer = new TemporalTokenizer()

  /**
   * Token representing a null value. Mirrors the Python module's null token.
   */
  def tokenizeNull: Token = Token("NULL", TokenType.NULL)

  /**
   * Convert any supported value to a [[Token]].
   *
   * The dispatch order mirrors the Python implementation:
   * null → Tokenizable → String/bytes → numeric → temporal → fallback string.
   */
  def tokenize(value: Any): Token = value match {
    case null                       => Token("NULL", TokenType.NULL)
    case t: Tokenizable            => Token(t.toToken(), TokenType.STRUCTURED)
    case s: String                  => stringTokenizer.tokenize(s)
    case b: Array[Byte]             => stringTokenizer.tokenize(b)
    case bd: BigDecimal             => numericTokenizer.tokenize(bd)
    case bi: BigInt                 => numericTokenizer.tokenize(bi)
    case d: Double                  => numericTokenizer.tokenize(d)
    case f: Float                   => numericTokenizer.tokenize(f)
    case i: Int                     => numericTokenizer.tokenize(i)
    case l: Long                    => numericTokenizer.tokenize(l)
    case zdt: ZonedDateTime         => temporalTokenizer.tokenize(zdt)
    case ldt: LocalDateTime         => temporalTokenizer.tokenize(ldt)
    case ld: LocalDate              => temporalTokenizer.tokenize(ld)
    case inst: Instant              => temporalTokenizer.tokenize(inst)
    case t: Temporal                => Token(t.toString, TokenType.TEMPORAL)
    case other                      => Token(other.toString, TokenType.STRING, Map("fallback" -> true))
  }
}

// ============================================================================
// TokenRegistry
// ============================================================================

/**
 * Registry that maps `String` keys to [[TokenContainer]]s and runs handlers
 * over the items contained in those containers.
 *
 * Mirrors the Python `TokenRegistry` class.
 */
final class TokenRegistry[T] {

  private val _registry = mutable.Map.empty[String, TokenContainer[T]]
  private val _handlers = mutable.ListBuffer.empty[T => Option[Token]]

  /** Register a container under `key`. */
  def register(key: String, container: TokenContainer[T]): Unit = {
    _registry(key) = container
  }

  /** Add a handler that turns an item into an `Option[Token]`. */
  def addHandler(handler: T => Option[Token]): Unit = {
    _handlers += handler
  }

  /**
   * Process all items in the container stored under `key` through the
   * registered handlers. Returns the first non-empty token from any
   * handler, or `None` if every handler returned `None`.
   */
  def process(key: String): List[Option[Token]] = {
    _registry.get(key) match {
      case None              => Nil
      case Some(container)   =>
        container.getAll.iterator.map { item =>
          _handlers.iterator.map(_(item)).find(_.isDefined).flatten
        }.toList
    }
  }
}

// ============================================================================
// TokenFunctor / TokenMonad
// ============================================================================

/**
 * Simulated functor for tokens. Mirrors the Python `TokenFunctor`.
 *
 * @param value the value held by the functor
 */
class TokenFunctor[T](value: T) {

  /** Apply `func` to the contained value, producing a new [[TokenFunctor]]. */
  def map[U](func: T => U): TokenFunctor[U] =
    new TokenFunctor[U](func(value))

  /** Monadic bind. */
  def flatMap[U](func: T => TokenFunctor[U]): TokenFunctor[U] =
    func(value)

  /** Return the contained value, or `default` if it is `null`. */
  def getOrElse(default: => T): T =
    if (value == null) default else value

  /** The contained value (may be null). */
  def get: T = value
}

/**
 * Extended monadic operations. Mirrors the Python `TokenMonad`.
 */
final class TokenMonad[T](value: T) extends TokenFunctor[T](value) {

  /** Lift a pure value into the monad. */
  def pure(value: T): TokenMonad[T] = new TokenMonad[T](value)

  /** Applicative apply. */
  def ap[U](funcWrapped: TokenMonad[T => U]): TokenMonad[U] =
    new TokenMonad[U](funcWrapped.get(value))
}

// ============================================================================
// JsonTokenizer
// ============================================================================

/** Internal helpers for [[JsonTokenizer]]. */
object JsonTokenizer {

  /** Convert any value to a JSON string using a small built-in serializer. */
  private def toJson(value: Any, pretty: Boolean, indent: Int = 0): String = {
    val eol = if (pretty) "\n" else ""
    val pad = if (pretty) "  " else ""
    val cur = pad * indent
    val nxt = pad * (indent + 1)

    value match {
      case null => "null"
      case None => "null"
      case Some(v) => toJson(v, pretty, indent)
      case b: Boolean => b.toString
      case i: Int => i.toString
      case l: Long => l.toString
      case d: Double => if (d.isNaN || d.isInfinite) "null" else d.toString
      case f: Float => if (f.isNaN || f.isInfinite) "null" else f.toString
      case bd: BigDecimal => bd.bigDecimal.toPlainString
      case bi: BigInt => bi.bigInteger.toString
      case s: String => "\"" + escape(s) + "\""
      case m: Map[_, _] =>
        if (m.isEmpty) "{}"
        else {
          val body = m.toList.map { case (k, v) =>
            s"""$nxt"${escape(k.toString)}":${if (pretty) " " else ""}${toJson(v, pretty, indent + 1)}"""
          }.mkString("," + eol)
          s"{${eol}$body${eol}$cur}"
        }
      case l: List[_] =>
        if (l.isEmpty) "[]"
        else {
          val body = l.map(v => s"$nxt${toJson(v, pretty, indent + 1)}").mkString("," + eol)
          s"[$eol$body${eol}$cur]"
        }
      case seq: Seq[_] =>
        if (seq.isEmpty) "[]"
        else {
          val body = seq.toList.map(v => s"$nxt${toJson(v, pretty, indent + 1)}").mkString("," + eol)
          s"[$eol$body${eol}$cur]"
        }
      case other => "\"" + escape(other.toString) + "\""
    }
  }

  private def escape(s: String): String = {
    val sb = new StringBuilder(s.length + 2)
    var i = 0
    while (i < s.length) {
      val c = s.charAt(i)
      c match {
        case '\\' => sb.append("\\\\")
        case '"'  => sb.append("\\\"")
        case '\n' => sb.append("\\n")
        case '\r' => sb.append("\\r")
        case '\t' => sb.append("\\t")
        case '\b' => sb.append("\\b")
        case '\f' => sb.append("\\f")
        case ch if ch < 0x20 => sb.append(f"\\u${ch.toInt}%04x")
        case ch => sb.append(ch)
      }
      i += 1
    }
    sb.toString
  }
}

/**
 * Tokenizer for JSON-compatible values.
 *
 * Recursively serializes Scala values to JSON, optionally pretty-printed.
 * Mirrors the Python `JsonTokenizer`.
 */
class JsonTokenizer(pretty: Boolean = false) {

  /** Serialize `value` to a JSON string and wrap it in a [[Token]]. */
  def tokenize(value: Any): Token = {
    val jsonStr = JsonTokenizer.toJson(value, pretty)
    Token(jsonStr, TokenType.STRUCTURED, Map("json" -> true))
  }

  /** Extract a value at the given dot-separated path, then tokenize it. */
  def tokenizePath(value: Any, path: String): Option[Token] = {
    val parts = path.split('.').toList
    val resolved = parts.foldLeft[Option[Any]](Some(value)) { (acc, part) =>
      acc.flatMap { current =>
        current match {
          case m: Map[_, _] =>
            val mapAny = m.asInstanceOf[scala.collection.Map[Any, Any]]
            mapAny.get(part)
          case l: List[_] if part.forall(_.isDigit) =>
            val idx = part.toInt
            if (idx >= 0 && idx < l.size) Some(l(idx)) else None
          case _ => None
        }
      }
    }
    resolved.map(tokenize)
  }
}

// ============================================================================
// WhitespaceTokenizer
// ============================================================================

/** Shared constants for [[WhitespaceTokenizer]]. */
object WhitespaceTokenizer {

  /** Characters that may be stripped from the start/end of tokens. */
  private val Punctuation: Set[Char] =
    Set('.', ',', '!', '?', ';', ':', '\'', '"', '(', ')', '[', ']', '{', '}')
}

/**
 * Tokenizer that splits text by whitespace.
 *
 * Unlike the other tokenizers, this one returns `List[Token]` because it
 * splits a single input into many sub-tokens.
 *
 * @param lowercase        convert each token to lowercase
 * @param minLength        filter out tokens shorter than this
 * @param maxLength        truncate tokens longer than this
 * @param stripPunctuation strip leading/trailing punctuation from each token
 */
class WhitespaceTokenizer(
    lowercase: Boolean = false,
    minLength: Int = 0,
    maxLength: Option[Int] = None,
    stripPunctuation: Boolean = false
) {

  import WhitespaceTokenizer._

  /** Apply the configured filters and transformations to a single word. */
  private def processToken(word: String): Option[String] = {
    val stripped = if (stripPunctuation) stripPunctuationChars(word) else word
    val lowered  = if (lowercase) stripped.toLowerCase(Locale.ROOT) else stripped
    if (lowered.length < minLength) None
    else {
      val truncated = maxLength match {
        case Some(max) if lowered.length > max => lowered.take(max)
        case _                                => lowered
      }
      if (truncated.isEmpty) None else Some(truncated)
    }
  }

  /** Strip leading and trailing punctuation from a word. */
  private def stripPunctuationChars(word: String): String = {
    var start = 0
    var end   = word.length
    while (start < end && Punctuation.contains(word.charAt(start))) start += 1
    while (end > start && Punctuation.contains(word.charAt(end - 1))) end -= 1
    if (start == 0 && end == word.length) word
    else word.substring(start, end)
  }

  /** Split text into a list of [[Token]]s. */
  def tokenize(text: String): List[Token] = {
    val words = text.split("\\s+").filter(_.nonEmpty)
    words.toList.zipWithIndex.flatMap { case (word, i) =>
      processToken(word).map { t =>
        Token(t, TokenType.STRING, Map("position" -> i, "original" -> word))
      }
    }
  }

  /** Convenience: return just the string values of each token. */
  def tokenizeToStrings(text: String): List[String] =
    tokenize(text).map(_.value)

  /** Tokenize and return tokens with their character offsets in the source. */
  def tokenizeWithPositions(text: String): List[(String, Int, Int)] = {
    val builder = List.newBuilder[(String, Int, Int)]
    var currentPos = 0
    for (word <- text.split("\\s+") if word.nonEmpty) {
      val start = text.indexOf(word, currentPos)
      val end   = start + word.length
      processToken(word).foreach { t => builder += ((t, start, end)) }
      currentPos = end
    }
    builder.result()
  }

  /** Count the number of tokens produced from `text`. */
  def countTokens(text: String): Int = tokenize(text).size
}

// ============================================================================
// TokenizerBuilder
// ============================================================================

/**
 * Fluent builder for constructing a `T => Token` function.
 *
 * Replaces Python's mutable builder with type-safe Scala mutators that
 * return `this` for chaining.
 */
class TokenizerBuilder[T] {

  private val normalizers = mutable.ListBuffer.empty[String => String]
  private val validators  = mutable.ListBuffer.empty[T => Boolean]
  private val metadata    = mutable.Map.empty[String, Any]

  /** Add a normalizer applied to the stringified value. */
  def withNormalizer(f: String => String): TokenizerBuilder[T] = {
    normalizers += f
    this
  }

  /** Add a validator; the value must satisfy all validators or build throws. */
  def withValidator(f: T => Boolean): TokenizerBuilder[T] = {
    validators += f
    this
  }

  /** Attach extra metadata that will be copied onto every produced token. */
  def withMetadata(entries: (String, Any)*): TokenizerBuilder[T] = {
    metadata ++= entries
    this
  }

  /** Build a `T => Token` function with the configured pipeline. */
  def build(): T => Token = {
    val normList  = normalizers.toList
    val validList = validators.toList
    val metaMap   = metadata.toMap

    (value: T) => {
      if (!validList.forall(v => v(value)))
        throw new IllegalArgumentException(s"Validation failed for $value")
      var s = value.toString
      normList.foreach(f => s = f(s))
      Token(s, TokenType.STRING, metaMap)
    }
  }
}

object TokenizerBuilder {

  /** Convenience factory method, allowing `TokenizerBuilder[T]()` syntax. */
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]()
}

// ============================================================================
// Top-level convenience functions
// ============================================================================

/**
 * Module-level entry points that match the Python module functions
 * `tokenize`, `tokenizeBatch`, and `toToken`.
 */
object Tokenizer {

  /** A reusable default [[UniversalTokenizer]] used by the convenience functions. */
  private val universal: UniversalTokenizer = new UniversalTokenizer()

  /**
   * Tokenize any supported value. Equivalent to the Python `tokenize(value)`
   * when invoked via `UniversalTokenizer.tokenize`.
   */
  def tokenize(value: Any): Token = universal.tokenize(value)

  /**
   * Tokenize an iterable of values, returning them in order.
   *
   * Mirrors the Python `tokenizeBatch` helper. The element type is
   * intentionally `Any` so callers can pass heterogeneous collections.
   */
  def tokenizeBatch(values: Iterable[_]): Seq[Token] =
    values.iterator.map(v => universal.tokenize(v)).toSeq

  /**
   * Convert any value to a [[Token]] via the [[UniversalTokenizer]] dispatcher.
   */
  def toToken(value: Any): Token = universal.tokenize(value)
}