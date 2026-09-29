package tokenizer

import java.time.{LocalDate, LocalDateTime}
import java.time.format.DateTimeFormatter

import io.circe.Json
import scala.collection.mutable
import scala.util.Try

/**
 * Scala 2.13 port of the Python `Tokenizer` module.
 *
 * Provides a small hierarchy of tokenizers that convert heterogeneous
 * inputs (strings, numbers, dates, custom [[Tokenizable]] objects,
 * JSON structures, …) into a uniform [[Token]] ADT suitable for
 * downstream text/feature pipelines.
 *
 * Compared with the Python original the Scala version favours:
 *   - immutability for value types (`case class Token`, immutable `Map` metadata)
 *   - sealed hierarchies for closed domains (`TokenType`, `JsonValue`)
 *   - `Option` / explicit error handling instead of nullable returns
 *   - pattern matching over `isinstance` chains
 *   - trait / abstract-class polymorphism instead of runtime `Protocol` checks
 *   - variance annotations (`+`, `-`) on generic containers
 *
 * Public surface (matching the source Python module):
 * {{{
 *   TokenType, Token, Tokenizable, HasLength, TokenProcessor,
 *   MutableTokenBatch,
 *   TokenContainer, TokenSink, BivariantHandler,
 *   BaseTokenizer,
 *   StringTokenizer, NumericTokenizer, TemporalTokenizer, UniversalTokenizer,
 *   TokenRegistry, TokenFunctor, TokenMonad,
 *   JsonValue, JsonTokenizer,
 *   WhitespaceTokenizer, TokenizerBuilder,
 *   tokenize, tokenizeBatch, toToken, withMetadata
 * }}}
 */

// =====================================================================
// TokenType – closed set of token categories
// =====================================================================

/**
 * Closed enumeration of token categories. The string `value` mirrors the
 * original Python enum value so payloads remain inter-operable.
 */
sealed trait TokenType {
  def value: String
}

object TokenType {
  case object STRING     extends TokenType { val value: String = "string"     }
  case object NUMERIC    extends TokenType { val value: String = "numeric"    }
  case object TEMPORAL   extends TokenType { val value: String = "temporal"   }
  case object STRUCTURED extends TokenType { val value: String = "structured" }
  case object BINARY     extends TokenType { val value: String = "binary"     }
  case object NULL       extends TokenType { val value: String = "null"       }
}

// =====================================================================
// Token – immutable token value
// =====================================================================

/**
 * Immutable token returned by every tokenizer. Metadata is carried as an
 * immutable [[scala.collection.immutable.Map]]; use [[withMetadata]] to
 * derive a new instance with additional or overriding entries.
 */
final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: Map[String, Any] = Map.empty
) {

  /** Return a new `Token` with additional metadata entries (varargs form). */
  def withMetadata(entries: (String, Any)*): Token =
    if (entries.isEmpty) this
    else copy(metadata = metadata ++ entries.toMap)

  /** `Map`-based variant of [[withMetadata]] for callers that already hold a `Map`. */
  def withMetadataMap(extra: Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)

  /** Convenience renderer that returns the token's string content. */
  def toToken: String = value

  // -- Java-bean-style accessors ----------------------------------------

  /** Java-bean accessor for [[value]]. */
  def getValue: String = value

  /** Java-bean accessor for [[tokenType]]. */
  def getTokenType: TokenType = tokenType

  /** Java-bean accessor for [[metadata]]. */
  def getMetadata: Map[String, Any] = metadata
}

// =====================================================================
// Protocols / type-classes – structural capabilities
// =====================================================================

/** Mix-in for objects that can self-describe as a token string. */
trait Tokenizable {
  def toToken: String
}

/** Mix-in for objects that report a length (e.g. strings, collections). */
trait HasLength {
  def length: Int
}

/** Contravariant processor that consumes tokens of type `T`. */
trait TokenProcessor[-T] {
  def process(item: T): Unit
}

// =====================================================================
// MutableTokenBatch – contrast with immutable `Token`
// =====================================================================

/**
 * Mutable batch of [[Token]]s. Throws after [[markProcessed]] has been
 * called, mirroring the Python `RuntimeError` behaviour.
 */
final class MutableTokenBatch {

  private val buf       = mutable.ListBuffer.empty[Token]
  private var processed = false

  /** Append a token; raises if the batch has been sealed. */
  def add(token: Token): Unit = {
    if (processed) throw new RuntimeException("Batch already processed")
    buf += token
  }

  /** Seal the batch: further `add` calls will raise. */
  def markProcessed(): Unit = { processed = true }

  /** Snapshot of the accumulated tokens. */
  def tokens: List[Token] = buf.toList

  /** Whether [[markProcessed]] has been invoked. */
  def isProcessed: Boolean = processed
}

object MutableTokenBatch {
  def apply(): MutableTokenBatch = new MutableTokenBatch
}

// =====================================================================
// Generic container classes
// =====================================================================

/**
 * Covariant container that returns its items as a `Vector`. The `+T`
 * variance allows `TokenContainer[String]` to flow where
 * `TokenContainer[Any]` is expected, mirroring the Python `T_co` intent.
 */
class TokenContainer[+T](items: Iterable[T]) {

  private val backing: Vector[T] = items.toVector

  /** Materialized view of the stored items. */
  def getAll: Vector[T] = backing

  /** Alias for [[getAll]] for callers expecting Java-bean-style access. */
  def get: Vector[T] = backing

  /** Apply `func` to every item and collect the resulting strings. */
  def mapTokens(func: T => String): List[String] = backing.iterator.map(func).toList

  /** Number of stored items. */
  def size: Int = backing.size
}

object TokenContainer {
  def apply[T](items: Iterable[T]): TokenContainer[T] = new TokenContainer[T](items)

  def empty[T]: TokenContainer[T] = new TokenContainer[T](Vector.empty[T])
}

/**
 * Contravariant sink that buffers incoming items. The `-T` variance
 * allows a `TokenSink[Any]` to receive `String`s, matching the Python
 * `T_contra` spirit.
 */
class TokenSink[-T] {

  private val received = mutable.ListBuffer.empty[Any]

  /** Accept an item into the buffer. */
  def receive(item: T): Unit = { received += item }

  /** Drain the buffer, returning the previously received items in order. */
  def drain(): List[Any] = {
    val snapshot = received.toList
    received.clear()
    snapshot
  }
}

object TokenSink {
  def apply[T](): TokenSink[T] = new TokenSink[T]()
}

/**
 * Invariant handler holding a single value of type `T`. The class uses
 * Scala's standard mutator pattern (`get` / `set`) rather than mutable
 * properties, expressing the Python `BivariantHandler` (sic) directly.
 */
final class BivariantHandler[T](default: T) {

  private var current: T = default

  /** Read the current value. */
  def get: T = current

  /** Replace the stored value. */
  def set(value: T): Unit = { current = value }

  /** Apply `func` to the stored value, store and return the new value. */
  def transform(func: T => T): T = {
    val next = func(current)
    current = next
    next
  }
}

object BivariantHandler {
  def apply[T](default: T): BivariantHandler[T] = new BivariantHandler[T](default)
}

// =====================================================================
// BaseTokenizer – generic contract for tokenizers
// =====================================================================

/**
 * Abstract base for tokenizers turning values of type `T` into [[Token]]s.
 *
 * @tparam T the accepted input type
 */
abstract class BaseTokenizer[T] {

  /** Convert a single value into a [[Token]]. */
  def tokenize(value: T): Token

  /** Lazily tokenize a collection of values; preserves the source order. */
  def tokenizeBatch(values: Iterable[T]): Iterator[Token] =
    values.iterator.map(tokenize)
}

// =====================================================================
// StringTokenizer
// =====================================================================

/**
 * Tokenizes `String` or `Array[Byte]` inputs, optionally running the
 * decoded text through a user-supplied `normalizer`. The Python
 * `StrOrBytes` union is expressed by `BaseTokenizer[Any]` plus pattern
 * matching – true static union types are not expressible in Scala 2.13
 * without an explicit ADT.
 */
class StringTokenizer(
    val encoding: String = "UTF-8",
    val normalizer: String => String = identity
) extends BaseTokenizer[Any] {

  /** Tokenize a `String` directly. */
  def tokenizeString(value: String): Token =
    Token(normalizer(value), TokenType.STRING)

  /** Tokenize raw bytes by decoding them with [[encoding]]. */
  def tokenizeBytes(value: Array[Byte]): Token =
    Token(normalizer(new String(value, encoding)), TokenType.STRING)

  override def tokenize(value: Any): Token = value match {
    case s: String      => tokenizeString(s)
    case b: Array[Byte] => tokenizeBytes(b)
    case null           =>
      throw new IllegalArgumentException("StringTokenizer cannot tokenize null")
    case other          =>
      throw new IllegalArgumentException(
        s"StringTokenizer expects String or Array[Byte], got ${other.getClass.getName}"
      )
  }
}

// =====================================================================
// NumericTokenizer
// =====================================================================

/**
 * Tokenizes numeric values, formatting them with a configurable
 * `precision`. The Python class's mutable-default `format_options`
 * dict is replaced with an immutable [[scala.collection.immutable.Map]].
 */
class NumericTokenizer(
    val precision: Int = 6,
    val formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[Any] {

  require(precision >= 0, s"precision must be non-negative, got $precision")

  /**
   * Format a [[scala.math.BigDecimal]] through a `java.math.BigDecimal`
   * so we can pin the scale and emit a non-scientific representation.
   */
  private def scaledDecimal(d: BigDecimal): String = {
    val jbd = new java.math.BigDecimal(d.toString)
    jbd.setScale(precision, java.math.RoundingMode.HALF_UP).toPlainString
  }

  override def tokenize(value: Any): Token = {
    val (text, originalType): (String, String) = value match {
      case bd: BigDecimal => (scaledDecimal(bd),                     "Decimal")
      case d:  Double     => (scaledDecimal(BigDecimal(d)),          "float")
      case f:  Float      => (scaledDecimal(BigDecimal(f.toDouble)), "float")
      case n:  Int        => (n.toString,                            "int")
      case n:  Long       => (n.toString,                            "int")
      case n:  Short      => (n.toString,                            "int")
      case n:  Byte       => (n.toString,                            "int")
      case null           =>
        throw new IllegalArgumentException("NumericTokenizer cannot tokenize null")
      case other          =>
        throw new IllegalArgumentException(
          s"NumericTokenizer expects a numeric value, got ${other.getClass.getName}"
        )
    }
    Token(text, TokenType.NUMERIC, Map("original_type" -> originalType))
  }
}

// =====================================================================
// TemporalTokenizer
// =====================================================================

/**
 * Tokenizes `java.time.LocalDate` / `LocalDateTime` using a configurable
 * pattern (defaulting to ISO). We use `java.time` because Scala has no
 * first-class temporal types like Python's `datetime` / `date`.
 */
class TemporalTokenizer(
    val formatStr: Option[String] = None
) extends BaseTokenizer[Any] {

  import TemporalTokenizer._

  override def tokenize(value: Any): Token = {
    val pattern = formatStr.getOrElse(defaultPattern(value))
    val text = value match {
      case d: LocalDateTime => d.format(DateTimeFormatter.ofPattern(pattern))
      case d: LocalDate     => d.format(DateTimeFormatter.ofPattern(pattern))
      case null             =>
        throw new IllegalArgumentException("TemporalTokenizer cannot tokenize null")
      case other            =>
        throw new IllegalArgumentException(
          s"TemporalTokenizer expects a java.time temporal, got ${other.getClass.getName}"
        )
    }
    Token(text, TokenType.TEMPORAL)
  }

  private def defaultPattern(value: Any): String = value match {
    case _: LocalDateTime => IsoPattern
    case _: LocalDate     => DatePattern
    case _                =>
      throw new IllegalArgumentException(
        s"TemporalTokenizer defaults only defined for LocalDate/LocalDateTime, got ${value.getClass.getName}"
      )
  }
}

object TemporalTokenizer {
  /** ISO-8601 instant format (`yyyy-MM-dd'T'HH:mm:ss`). */
  val IsoPattern: String = "yyyy-MM-dd'T'HH:mm:ss"

  /** ISO-8601 date format (`yyyy-MM-dd`). */
  val DatePattern: String = "yyyy-MM-dd"
}

// =====================================================================
// UniversalTokenizer – type-dispatching facade
// =====================================================================

/**
 * A tokenizer that accepts any value and dispatches to a category-
 * specific tokenizer based on its runtime type. Mirrors the duck-typed
 * Python original via `Any` plus exhaustive pattern matching.
 */
class UniversalTokenizer {

  protected val stringTokenizer:  StringTokenizer   = new StringTokenizer()
  protected val numericTokenizer: NumericTokenizer  = new NumericTokenizer()
  protected val temporalTokenizer: TemporalTokenizer = new TemporalTokenizer()

  /**
   * Return a `NULL` token. Mirrors the Python `tokenize(None)` overload.
   * Useful as a no-arg, side-effect-free default for tests.
   */
  def tokenizeNull: Token = Token("NULL", TokenType.NULL)

  /**
   * Dispatch entry point. Returns:
   *   - a `NULL` token for `null` / Scala `None`
   *   - a `STRUCTURED` token for [[Tokenizable]] values
   *   - category-specific tokens for strings, bytes, numbers, dates
   *   - a fallback `STRING` token carrying `{"fallback": true}` otherwise
   */
  def tokenize(value: Any): Token = value match {
    case null             => tokenizeNull
    case None             => tokenizeNull
    case t: Tokenizable   => Token(t.toToken, TokenType.STRUCTURED)
    case s: String        => stringTokenizer.tokenize(s)
    case b: Array[Byte]   => stringTokenizer.tokenize(b)
    case n: Int           => numericTokenizer.tokenize(n)
    case n: Long          => numericTokenizer.tokenize(n)
    case n: Short         => numericTokenizer.tokenize(n)
    case n: Byte          => numericTokenizer.tokenize(n)
    case d: Double        => numericTokenizer.tokenize(d)
    case f: Float         => numericTokenizer.tokenize(f)
    case bd: BigDecimal   => numericTokenizer.tokenize(bd)
    case d: LocalDateTime => temporalTokenizer.tokenize(d)
    case d: LocalDate     => temporalTokenizer.tokenize(d)
    case other            =>
      Token(other.toString, TokenType.STRING, Map("fallback" -> true))
  }

  /** Tokenize a batch of values eagerly into a `List`. */
  def tokenizeBatch(values: Iterable[Any]): List[Token] =
    values.iterator.map(tokenize).toList
}

object UniversalTokenizer {
  /** Convenience constructor matching the Python module's idiom. */
  def apply(): UniversalTokenizer = new UniversalTokenizer()
}

// =====================================================================
// TokenRegistry – nested-generic registry of `TokenContainer`s + handlers
// =====================================================================

/**
 * Registry keyed by `String`; each key resolves to a [[TokenContainer]]
 * whose items are routed through a list of handlers. Each item produces
 * exactly one [[Token]] (or `None` if no handler matches).
 *
 * @tparam T the type of items held in this registry's containers
 */
final class TokenRegistry[T] {

  private val registry = mutable.Map.empty[String, TokenContainer[T]]
  private val handlers = mutable.ListBuffer.empty[T => Option[Token]]

  /** Associate `key` with a container. */
  def register(key: String, container: TokenContainer[T]): Unit = {
    registry(key) = container
  }

  /** Append a handler producing an optional [[Token]]. */
  def addHandler(handler: T => Option[Token]): Unit = {
    handlers += handler
  }

  /**
   * Process every item in the container registered under `key` through
   * the registered handlers, returning `None` for items that no handler
   * accepts. If the key is not registered, returns the empty list.
   */
  def process(key: String): List[Option[Token]] = registry.get(key) match {
    case None => List.empty
    case Some(container) =>
      val hs = handlers.toList
      container.getAll.toList.map { item =>
        hs.iterator.map(_.apply(item)).find(_.isDefined).getOrElse(None)
      }
  }

  /** Whether the registry currently stores anything under `key`. */
  def contains(key: String): Boolean = registry.contains(key)

  /** Number of items currently held across all registered containers. */
  def size: Int = registry.values.iterator.map(_.size).sum
}

object TokenRegistry {
  def apply[T](): TokenRegistry[T] = new TokenRegistry[T]()
}

// =====================================================================
// Functional wrappers: TokenFunctor / TokenMonad
// =====================================================================

/**
 * Simple functor wrapper around an arbitrary value. The Python code
 * simulated a higher-kinded type with a TypeVar; the Scala version is
 * a plain generic class with `map` / `flatMap` that propagate `T`.
 */
class TokenFunctor[T](val value: T) {

  /** Java-bean-style accessor that returns the wrapped value. */
  def get: T = value

  /** Apply `func` to the wrapped value. */
  def map[R](func: T => R): TokenFunctor[R] = new TokenFunctor(func(value))

  /** Replace the wrapped value using `func`. */
  def flatMap[R](func: T => TokenFunctor[R]): TokenFunctor[R] = func(value)

  /** Return the wrapped value or `default` when the wrapped value is `null`. */
  def getOrElse[U >: T](default: U): U = Option(value).getOrElse(default)
}

object TokenFunctor {
  def apply[T](value: T): TokenFunctor[T] = new TokenFunctor[T](value)
}

/**
 * Monad wrapper adding `pure` and `ap` to [[TokenFunctor]].
 */
final class TokenMonad[T](value0: T) extends TokenFunctor[T](value0) {

  /** Lift a value into the monad. */
  def pure[U](value: U): TokenMonad[U] = new TokenMonad(value)

  /**
   * Applicative apply: apply a function wrapped inside a [[TokenMonad]]
   * to the value wrapped in this monad.
   */
  def ap[U](funcWrapped: TokenMonad[T => U]): TokenMonad[U] =
    new TokenMonad(funcWrapped.value(value0))
}

object TokenMonad {
  def apply[T](value: T): TokenMonad[T] = new TokenMonad[T](value)

  def pure[T](value: T): TokenMonad[T] = new TokenMonad[T](value)
}

// =====================================================================
// JsonValue / JsonTokenizer – recursive JSON tree + serialization
// =====================================================================

/**
 * Recursive ADT for JSON-shaped values. Mirrors the Python recursive
 * `Union` alias `JsonValue`. Useful for callers that want a typed AST
 * rather than passing raw `Any`.
 */
sealed trait JsonValue

object JsonValue {

  /** JSON string. */
  final case class Str(s: String)        extends JsonValue
  /** JSON number (stored as `BigDecimal` for precision). */
  final case class Num(n: BigDecimal)    extends JsonValue
  /** JSON boolean. */
  final case class Bool(b: Boolean)      extends JsonValue
  /** JSON `null` literal – companion-object form. */
  case object Null                                   extends JsonValue
  /** JSON array. */
  final case class Arr(items: List[JsonValue])         extends JsonValue
  /** JSON object. */
  final case class Obj(fields: Map[String, JsonValue]) extends JsonValue
}

/**
 * Tokenizer for JSON-shaped values. Accepts both [[JsonValue]] ADT
 * instances and raw Scala values (`String`, `Int`, `Map`, `List`, …).
 */
class JsonTokenizer(val pretty: Boolean = false) {

  /**
   * Serialize `value` to JSON and wrap it in a [[Token]]. Uses
   * `circe` internally for safe encoding of nested structures.
   */
  def tokenize(value: Any): Token = {
    val json = toCirce(value)
    val str  = if (pretty) json.spaces2 else json.noSpaces
    Token(str, TokenType.STRUCTURED, Map("json" -> true))
  }

  /**
   * Extract the value at `path` (a `.`-separated list of keys/indices)
   * and tokenize the result. Returns [[scala.None]] if any segment is
   * missing or malformed, matching the original Python semantics.
   */
  def tokenizePath(value: Any, path: String): Option[Token] = {
    val parts = path.split('.').toList
    navigate(value, parts).map(tokenize)
  }

  private def navigate(current: Any, parts: List[String]): Option[Any] = parts match {
    case Nil => Some(current)
    case part :: rest =>
      current match {
        case m: scala.collection.Map[_, _] =>
          val anyMap = m.asInstanceOf[scala.collection.Map[String, Any]]
          anyMap.get(part).flatMap(v => navigate(v, rest))
        case l: List[_] =>
          Try(part.toInt).toOption.flatMap { idx =>
            if (idx >= 0 && idx < l.size) navigate(l(idx), rest) else None
          }
        case _ => None
      }
  }

  /** Lift any Scala value (including null/None) into a `circe` `Json`. */
  private def toCirce(v: Any): Json = v match {
    case null              => Json.Null
    case None              => Json.Null
    case Some(inner)       => toCirce(inner)
    case JsonValue.Null    => Json.Null
    case s: JsonValue.Str  => Json.fromString(s.s)
    case n: JsonValue.Num  => Json.fromBigDecimal(n.n)
    case b: JsonValue.Bool => Json.fromBoolean(b.b)
    case a: JsonValue.Arr  => Json.fromValues(a.items.map(toCirce))
    case o: JsonValue.Obj  =>
      val pairs = o.fields.toList.map { case (k, vv) => k -> toCirce(vv) }
      Json.obj(pairs: _*)
    case b: Boolean        => Json.fromBoolean(b)
    case s: String         => Json.fromString(s)
    case n: Int            => Json.fromInt(n)
    case n: Long           => Json.fromLong(n)
    case d: Double         => Json.fromDoubleOrNull(d)
    case f: Float          => Json.fromFloatOrNull(f)
    case bd: BigDecimal    => Json.fromBigDecimal(bd)
    case m: scala.collection.Map[_, _] =>
      val pairs = m.toList.map { case (k, vv) => k.toString -> toCirce(vv) }
      Json.obj(pairs: _*)
    case it: Iterable[_]   => Json.fromValues(it.toList.map(toCirce))
    case other             => Json.fromString(other.toString)
  }
}

object JsonTokenizer {
  def apply(pretty: Boolean = false): JsonTokenizer = new JsonTokenizer(pretty)
}

// =====================================================================
// WhitespaceTokenizer
// =====================================================================

/**
 * Splits text on whitespace into word-level tokens, with optional
 * punctuation stripping, lowercasing and length filtering.
 */
class WhitespaceTokenizer(
    val lowercase: Boolean = false,
    val minLength: Int = 0,
    val maxLength: Option[Int] = None,
    val stripPunctuation: Boolean = false
) {

  import WhitespaceTokenizer.Punctuation

  require(minLength >= 0, s"minLength must be non-negative, got $minLength")
  maxLength.foreach(m => require(m >= 0, s"maxLength must be non-negative, got $m"))

  /** Apply the configured transformations to a single word. */
  private def processToken(word: String): Option[String] = {
    val stripped =
      if (stripPunctuation)
        word.dropWhile(Punctuation.contains).reverse
          .dropWhile(Punctuation.contains).reverse
      else word
    val lowered = if (lowercase) stripped.toLowerCase else stripped
    if (lowered.length < minLength) None
    else {
      val clipped = maxLength.fold(lowered)(lowered.take)
      Option(clipped).filter(_.nonEmpty)
    }
  }

  /** Split text into word-level tokens, each tagged with its position. */
  def tokenize(text: String): List[Token] =
    WhitespaceTokenizer.splitWords(text).zipWithIndex.flatMap {
      case (word, idx) =>
        processToken(word).map { processed =>
          Token(processed, TokenType.STRING, Map("position" -> idx, "original" -> word))
        }
    }

  /** Convenience: return only the token string values. */
  def tokenizeToStrings(text: String): List[String] =
    tokenize(text).map(_.value)

  /** Tokenize and report `(processed, start, end)` character offsets. */
  def tokenizeWithPositions(text: String): List[(String, Int, Int)] = {
    var currentPos = 0
    WhitespaceTokenizer.splitWords(text).flatMap { word =>
      val start = text.indexOf(word, currentPos)
      if (start < 0) None
      else {
        val end = start + word.length
        currentPos = end
        processToken(word).map(p => (p, start, end))
      }
    }
  }

  /** Number of surviving tokens in `text`. */
  def countTokens(text: String): Int = tokenize(text).length
}

object WhitespaceTokenizer {

  /** Characters that are treated as punctuation when `stripPunctuation` is on. */
  val Punctuation: Set[Char] =
    Set('.', ',', '!', '?', ';', ':', '\'', '"', '(', ')', '[', ']', '{', '}')

  /** Split text on runs of whitespace, dropping empty fragments. */
  private[tokenizer] def splitWords(text: String): List[String] =
    text.split("\\s+").iterator.filter(_.nonEmpty).toList
}

// =====================================================================
// TokenizerBuilder
// =====================================================================

/**
 * Fluent builder for composing a `T => Token` function out of arbitrary
 * normalizers and validators. Mirrors the Python fluent API.
 */
class TokenizerBuilder[T] {

  private val normalizersBuffer = mutable.ListBuffer.empty[String => String]
  private val validatorsBuffer  = mutable.ListBuffer.empty[T => Boolean]
  private var metadataAcc: Map[String, Any] = Map.empty

  /** Append a string normalizer. Returns `this` for fluent chaining. */
  def withNormalizer(f: String => String): this.type = {
    normalizersBuffer += f
    this
  }

  /** Append a predicate validator. Returns `this` for fluent chaining. */
  def withValidator(f: T => Boolean): this.type = {
    validatorsBuffer += f
    this
  }

  /** Add metadata entries attached to every produced token. */
  def withMetadata(entries: (String, Any)*): this.type = {
    if (entries.nonEmpty) metadataAcc = metadataAcc ++ entries.toMap
    this
  }

  /** Build an immutable `T => Token` function. */
  def build(): T => Token = {
    val normalizers = normalizersBuffer.toList
    val validators  = validatorsBuffer.toList
    val baseMeta    = metadataAcc
    (value: T) => {
      validators.foreach { v =>
        if (!v(value))
          throw new IllegalArgumentException(
            s"Validation failed for ${value.toString}"
          )
      }
      var s = value.toString
      normalizers.foreach { n => s = n(s) }
      Token(s, TokenType.STRING, baseMeta)
    }
  }
}

object TokenizerBuilder {
  /** Companion `apply` for the idiomatic `TokenizerBuilder[Int]()` form. */
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]()
}

// =====================================================================
// Module-level convenience functions
// =====================================================================

/**
 * Top-level convenience aliases for the two main operations. These make
 * `tokenize(...)` and `tokenizeBatch(...)` directly importable from
 * `tokenizer._`, matching the public API of the original Python module.
 */
package object tokenizer {

  private val DefaultUniversal: UniversalTokenizer = new UniversalTokenizer()

  /** Tokenize a single value with the default [[UniversalTokenizer]]. */
  def tokenize(value: Any): Token = DefaultUniversal.tokenize(value)

  /** Tokenize a batch of values with the default [[UniversalTokenizer]]. */
  def tokenizeBatch(values: Iterable[Any]): List[Token] =
    values.iterator.map(tokenize).toList
}
