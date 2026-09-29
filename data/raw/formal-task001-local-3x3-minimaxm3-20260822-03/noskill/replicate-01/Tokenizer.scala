package tokenizer

import java.time.{Instant, LocalDate, LocalDateTime, ZoneOffset}
import java.time.format.DateTimeFormatter

import scala.collection.mutable
import scala.util.{Failure, Success, Try}

import io.circe.Json

/**
 * Tokenizer module for converting various input types to standardized string tokens.
 *
 * Scala 2.13 port of the original Python Tokenizer. The translation favours:
 *   - immutable data ([[Token]], [[TokenType]]) and case classes
 *   - [[scala.Option]] for absence, [[scala.util.Try]] / [[scala.util.Either]] for errors
 *   - traits for structural interfaces ([[Tokenizable]], [[HasLength]], [[TokenProcessor]])
 *   - covariant / contravariant variance where the Python source used TypeVar bounds
 *   - Scala's standard library collections in place of hand-rolled containers
 *   - circe's [[Json]] for the recursive JSON tokenization API
 */

// ============================================================================
// TokenType
// ============================================================================

/** Enumeration of the different kinds of token we can produce. */
sealed trait TokenType

object TokenType {
  case object STRING extends TokenType
  case object NUMERIC extends TokenType
  case object TEMPORAL extends TokenType
  case object STRUCTURED extends TokenType
  case object BINARY extends TokenType
  case object NULL extends TokenType

  /** Look up a [[TokenType]] by its wire name, returning `None` for unknowns. */
  def fromName(name: String): Option[TokenType] = name match {
    case "string"    => Some(STRING)
    case "numeric"   => Some(NUMERIC)
    case "temporal"  => Some(TEMPORAL)
    case "structured" => Some(STRUCTURED)
    case "binary"    => Some(BINARY)
    case "null"      => Some(NULL)
    case _           => None
  }

  /** Wire name for the token type, matching the Python enum values. */
  def nameOf(t: TokenType): String = t match {
    case STRING    => "string"
    case NUMERIC   => "numeric"
    case TEMPORAL  => "temporal"
    case STRUCTURED => "structured"
    case BINARY    => "binary"
    case NULL      => "null"
  }
}

// ============================================================================
// Structural interfaces (Python Protocol equivalents)
// ============================================================================

/** Anything that can render itself to a token string. */
trait Tokenizable {
  def toToken: String
}

/** Anything that has a length. */
trait HasLength {
  def length: Int
}

/** Consumer of tokens; contravariant so a `TokenProcessor[Token]` accepts any subtype. */
trait TokenProcessor[-T] {
  def process(item: T): Unit
}

// ============================================================================
// Token
// ============================================================================

/**
 * Immutable token carrying a string value, its [[TokenType]], and arbitrary metadata.
 *
 * @param value     the string representation
 * @param tokenType what kind of token this is
 * @param metadata  optional structured metadata (originally Python `dict[str, Any]`)
 */
final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: Map[String, Any] = Map.empty
) {
  /** Return a new token with `entries` merged into its metadata. */
  def withMetadata(entries: (String, Any)*): Token =
    copy(metadata = metadata ++ entries)

  /** Return a new token with an existing map merged into its metadata. */
  def withMetadata(extra: Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)
}

// ============================================================================
// MutableTokenBatch
// ============================================================================

/** A mutable batch of tokens with a single-shot "processed" flag. */
final class MutableTokenBatch {
  private val tokens: mutable.ListBuffer[Token] = mutable.ListBuffer.empty
  private var processedFlag: Boolean = false

  def add(token: Token): Unit = {
    if (processedFlag)
      throw new IllegalStateException("Batch already processed")
    tokens += token
  }

  def markProcessed(): Unit = { processedFlag = true }

  def getTokens: List[Token] = tokens.toList

  def isProcessed: Boolean = processedFlag
}

// ============================================================================
// Generic containers
// ============================================================================

/** Covariant container: a snapshot of a sequence of `T`. */
final class TokenContainer[+T] private (private val stored: IndexedSeq[T]) {
  def this(items: Seq[T]) = this(items.toIndexedSeq)

  def getAll: IndexedSeq[T] = stored

  def mapTokens(f: T => String): List[String] = stored.iterator.map(f).toList
}

object TokenContainer {
  def apply[T](items: Seq[T]): TokenContainer[T] = new TokenContainer[T](items)
  def empty[T]: TokenContainer[T] = new TokenContainer[T](Vector.empty)
}

/** Contravariant sink: collects received items until [[TokenSink.drain]] is called. */
final class TokenSink[-T] {
  private val received: mutable.ListBuffer[Any] = mutable.ListBuffer.empty

  def receive(item: T): Unit = received += item

  def drain(): List[Any] = {
    val snapshot = received.toList
    received.clear()
    snapshot
  }
}

/** Invariant handler: store a single value of type `T`. */
final class BivariantHandler[T](initial: T) {
  private var current: T = initial

  def get: T = current

  def set(value: T): Unit = { current = value }

  def transform(f: T => T): T = {
    current = f(current)
    current
  }
}

// ============================================================================
// BaseTokenizer & concrete tokenizers
// ============================================================================

/** Abstract base for all tokenizers. */
abstract class BaseTokenizer[T] {
  /** Convert a single value into a [[Token]]. */
  def tokenize(value: T): Token

  /** Lazy tokenization of multiple values. */
  def tokenizeBatch(values: Iterable[T]): Iterator[Token] =
    values.iterator.map(tokenize)
}

/** Tokenizer for `String` (and `Array[Byte]`) values. */
final class StringTokenizer(
    val encoding: String = "UTF-8",
    val normalizer: String => String = (s: String) => s
) extends BaseTokenizer[String] {

  override def tokenize(value: String): Token =
    Token(normalizer(value), TokenType.STRING)

  /** Convenience for bytes; not part of [[BaseTokenizer]] but useful. */
  def tokenizeBytes(bytes: Array[Byte]): Token = {
    val decoded = new String(bytes, encoding)
    Token(normalizer(decoded), TokenType.STRING)
  }
}

/**
 * Tokenizer for numeric values, formatting with a fixed precision.
 *
 * `formatOptions` is retained for parity with the Python source even though the
 * current implementation does not consume it.
 */
final class NumericTokenizer(
    val precision: Int = 6,
    val formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[Any] {

  override def tokenize(value: Any): Token = {
    val strValue: String = value match {
      case bd: BigDecimal =>
        bd.setScale(precision, BigDecimal.RoundingMode.HALF_UP).toString
      case d: Double =>
        BigDecimal(d)
          .setScale(precision, BigDecimal.RoundingMode.HALF_UP)
          .toString
      case f: Float =>
        BigDecimal(f.toDouble)
          .setScale(precision, BigDecimal.RoundingMode.HALF_UP)
          .toString
      case other =>
        other.toString
    }
    Token(
      value = strValue,
      tokenType = TokenType.NUMERIC,
      metadata = Map[String, Any]("original_type" -> value.getClass.getSimpleName)
    )
  }
}

/**
 * Tokenizer for temporal values (date / time).
 *
 * Accepts [[LocalDate]], [[LocalDateTime]], [[Instant]] as well as their
 * `java.sql` equivalents. If `formatStr` is set it overrides the defaults.
 */
final class TemporalTokenizer(val formatStr: Option[String] = None)
    extends BaseTokenizer[Any] {

  import TemporalTokenizer._

  override def tokenize(value: Any): Token = {
    val formatter: DateTimeFormatter = formatStr match {
      case Some(pattern) => DateTimeFormatter.ofPattern(pattern)
      case None =>
        value match {
          case _: LocalDate | _: java.sql.Date       => DATE_FORMAT
          case _: LocalDateTime | _: Instant |
               _: java.sql.Timestamp                  => ISO_FORMAT
          case _                                      => ISO_FORMAT
        }
    }

    val formatted: String = value match {
      case d: LocalDate         => d.format(formatter)
      case dt: LocalDateTime    => dt.format(formatter)
      case i: Instant           => i.atOffset(ZoneOffset.UTC).format(formatter)
      case ts: java.sql.Timestamp => ts.toLocalDateTime.format(formatter)
      case sd: java.sql.Date    => sd.toLocalDate.format(formatter)
      case other                => other.toString
    }

    Token(formatted, TokenType.TEMPORAL)
  }
}

object TemporalTokenizer {
  val ISO_FORMAT: DateTimeFormatter  = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss")
  val DATE_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")
}

// ============================================================================
// UniversalTokenizer
// ============================================================================

/**
 * Tokenizer that dispatches at runtime to specialized tokenizers based on the
 * runtime type of the input.
 */
final class UniversalTokenizer {
  private val stringTokenizer: StringTokenizer   = new StringTokenizer()
  private val numericTokenizer: NumericTokenizer = new NumericTokenizer()
  private val temporalTokenizer: TemporalTokenizer = new TemporalTokenizer()

  def tokenize(value: Any): Token = value match {
    case null                       => Token("NULL", TokenType.NULL)
    case t: Tokenizable             => Token(t.toToken, TokenType.STRUCTURED)
    case s: String                  => stringTokenizer.tokenize(s)
    case b: Array[Byte]             => stringTokenizer.tokenizeBytes(b)
    case bd: BigDecimal             => numericTokenizer.tokenize(bd)
    case i: Int                     => numericTokenizer.tokenize(i)
    case l: Long                    => numericTokenizer.tokenize(l)
    case d: Double                  => numericTokenizer.tokenize(d)
    case f: Float                   => numericTokenizer.tokenize(f)
    case dt: LocalDateTime          => temporalTokenizer.tokenize(dt)
    case d: LocalDate               => temporalTokenizer.tokenize(d)
    case i: Instant                 => temporalTokenizer.tokenize(i)
    case other                      =>
        Token(other.toString, TokenType.STRING, Map("fallback" -> true))
  }

  def tokenizeBatch(values: Iterable[Any]): Iterator[Token] =
    values.iterator.map(tokenize)
}

// ============================================================================
// WhitespaceTokenizer
// ============================================================================

/**
 * Basic tokenizer that splits text by whitespace and applies a small set of
 * common normalisations (lowercasing, punctuation stripping, length filtering,
 * position tracking).
 */
final class WhitespaceTokenizer(
    val lowercase: Boolean = false,
    val minLength: Int = 0,
    val maxLength: Option[Int] = None,
    val stripPunctuation: Boolean = false
) {
  import WhitespaceTokenizer._

  /** Process a single word into a token string, or `None` if it should be dropped. */
  def processToken(word: String): Option[String] = {
    var w: String = word
    if (stripPunctuation) w = stripPunctuationFromEnds(w)
    if (lowercase)        w = w.toLowerCase

    if (w.length < minLength) None
    else maxLength match {
      case Some(max) if w.length > max => Some(w.take(max))
      case _ if w.isEmpty              => None
      case _                           => Some(w)
    }
  }

  /** Split `text` on whitespace and emit a [[Token]] for each surviving word. */
  def tokenize(text: String): List[Token] = {
    val words = text.split("\\s+").toList
    words.zipWithIndex.flatMap { case (word, idx) =>
      processToken(word).map { processed =>
        Token(
          value     = processed,
          tokenType = TokenType.STRING,
          metadata  = Map[String, Any]("position" -> idx, "original" -> word)
        )
      }
    }
  }

  /** Convenience returning only the string values of [[tokenize]]. */
  def tokenizeToStrings(text: String): List[String] =
    tokenize(text).map(_.value)

  /**
   * Tokenize `text` and return `(token, start, end)` tuples with the
   * character offsets of each word in the original text.
   */
  def tokenizeWithPositions(text: String): List[(String, Int, Int)] = {
    val result = mutable.ListBuffer.empty[(String, Int, Int)]
    var currentPos = 0
    val words = text.split("\\s+")
    var i = 0
    while (i < words.length) {
      val word = words(i)
      val start = text.indexOf(word, currentPos)
      val end   = start + word.length
      processToken(word).foreach(p => result += ((p, start, end)))
      currentPos = end
      i += 1
    }
    result.toList
  }

  /** Number of tokens produced by [[tokenize]]. */
  def countTokens(text: String): Int = tokenize(text).length
}

object WhitespaceTokenizer {
  private val PUNCTUATION: Set[Char] =
    Set('.', ',', '!', '?', ';', ':', '\'', '"', '(', ')', '[', ']', '{', '}')

  /** Strip leading and trailing punctuation characters from `s`. */
  private def stripPunctuationFromEnds(s: String): String = {
    var start = 0
    var end   = s.length
    while (start < end && PUNCTUATION.contains(s.charAt(start))) start += 1
    while (end > start && PUNCTUATION.contains(s.charAt(end - 1))) end -= 1
    s.substring(start, end)
  }
}

// ============================================================================
// TokenizerBuilder
// ============================================================================

/** Fluent builder for assembling a string-coercing tokenizer with validators and metadata. */
final class TokenizerBuilder[T] {
  private val normalizers: mutable.ListBuffer[String => String] = mutable.ListBuffer.empty
  private val validators:  mutable.ListBuffer[T => Boolean]     = mutable.ListBuffer.empty
  private val metadata:    mutable.Map[String, Any]             = mutable.Map.empty

  def withNormalizer(f: String => String): TokenizerBuilder[T] = {
    normalizers += f
    this
  }

  def withValidator(f: T => Boolean): TokenizerBuilder[T] = {
    validators += f
    this
  }

  def withMetadata(entries: (String, Any)*): TokenizerBuilder[T] = {
    metadata ++= entries
    this
  }

  def withMetadata(extra: Map[String, Any]): TokenizerBuilder[T] = {
    metadata ++= extra
    this
  }

  /** Build a `T => Token` function from the configured normalizers, validators and metadata. */
  def build(): T => Token = {
    val normals  = normalizers.toList
    val valids   = validators.toList
    val meta     = metadata.toMap
    (value: T) => {
      val validation: Try[Unit] = valids.foldLeft(Try(())) { (acc, v) =>
        acc.flatMap(_ => if (v(value)) Success(()) else
          Failure(new IllegalArgumentException(s"Validation failed for $value")))
      }
      validation.get

      var str: String = value.toString
      normals.foreach(f => str = f(str))

      Token(str, TokenType.STRING, meta)
    }
  }
}

// ============================================================================
// TokenRegistry
// ============================================================================

/** Registry mapping a string key to a [[TokenContainer]] together with a list of handlers. */
final class TokenRegistry[T] {
  private val registry: mutable.Map[String, TokenContainer[T]]          = mutable.Map.empty
  private val handlers: mutable.ListBuffer[T => Option[Token]]           = mutable.ListBuffer.empty

  def register(key: String, container: TokenContainer[T]): Unit = {
    registry(key) = container
  }

  def addHandler(handler: T => Option[Token]): Unit = {
    handlers += handler
  }

  /**
   * Run every registered handler against every item of the container registered
   * under `key`. The first handler that returns `Some` wins; otherwise the
   * result for that item is `None`.
   */
  def process(key: String): List[Option[Token]] = registry.get(key) match {
    case None        => Nil
    case Some(container) =>
      container.getAll.iterator.map { item =>
        handlers.iterator.map(_(item)).find(_.isDefined).flatten
      }.toList
  }
}

// ============================================================================
// TokenFunctor / TokenMonad
// ============================================================================

/** Minimal functor wrapping a single value. */
class TokenFunctor[T](val value: T) {

  def map[U](f: T => U): TokenFunctor[U] =
    new TokenFunctor(f(value))

  def flatMap[U](f: T => TokenFunctor[U]): TokenFunctor[U] =
    f(value)

  def getOrElse(default: => T): T =
    if (value == null) default else value
}

/** Monad extension with applicative apply and a `pure` factory. */
final class TokenMonad[T](override val value: T) extends TokenFunctor[T](value) {
  /** Applicative apply: `TokenMonad[T => U]` applied to `TokenMonad[T]`. */
  def ap[U](f: TokenMonad[T => U]): TokenMonad[U] =
    new TokenMonad(f.value(value))
}

object TokenMonad {
  def pure[T](value: T): TokenMonad[T] = new TokenMonad(value)
}

// ============================================================================
// JsonTokenizer
// ============================================================================

/** Tokenizer for JSON-like structures backed by [[io.circe.Json]]. */
final class JsonTokenizer(val pretty: Boolean = false) {

  /** Tokenize a circe [[Json]] value. */
  def tokenizeJson(value: Json): Token = {
    val rendered = if (pretty) value.spaces2 else value.noSpaces
    Token(rendered, TokenType.STRUCTURED, Map[String, Any]("json" -> true))
  }

  /**
   * Tokenize any value by first coercing it to [[Json]]. Supports nested
   * `Map`, `List` and primitive types.
   */
  def tokenize(value: Any): Token = tokenizeJson(JsonTokenizer.toJson(value))

  /**
   * Walk a dot-separated path through the JSON structure and tokenize the
   * value at that path. Returns `None` if any step is missing.
   */
  def tokenizePath(value: Any, path: String): Option[Token] = {
    val parts = path.split("\\.").toList
    parts.foldLeft(Option(JsonTokenizer.toJson(value))) { (acc, part) =>
      acc.flatMap { json =>
        json.asObject.flatMap(_.apply(part)).orElse {
          part.toIntOption.flatMap(idx => json.asArray.flatMap(_.lift(idx)))
        }
      }
    }.map(tokenizeJson)
  }
}

object JsonTokenizer {
  /** Recursively turn a Scala value into a circe [[Json]]. */
  private[tokenizer] def toJson(value: Any): Json = value match {
    case null                          => Json.Null
    case s: String                     => Json.fromString(s)
    case b: Boolean                    => Json.fromBoolean(b)
    case i: Int                        => Json.fromInt(i)
    case l: Long                       => Json.fromLong(l)
    case d: Double                     => Json.fromDoubleOrNull(d)
    case f: Float                      => Json.fromDoubleOrNull(f.toDouble)
    case n: scala.math.BigDecimal      => Json.fromBigDecimal(n.bigDecimal)
    case bd: java.math.BigDecimal      => Json.fromBigDecimal(bd)
    case m: Map[_, _]                  =>
      Json.obj(m.toSeq.map { case (k, v) => k.toString -> toJson(v) }: _*)
    case l: List[_]                    => Json.fromValues(l.map(toJson))
    case s: Seq[_]                     => Json.fromValues(s.iterator.map(toJson).toList)
    case other                         => Json.fromString(other.toString)
  }
}

// ============================================================================
// Top-level convenience helpers
// ============================================================================

/** Top-level helpers that delegate to a default [[UniversalTokenizer]]. */
object Tokenizer {

  private val defaultUniversal: UniversalTokenizer = new UniversalTokenizer()

  /** Tokenize a single value using the default [[UniversalTokenizer]]. */
  def tokenize(value: Any): Token = defaultUniversal.tokenize(value)

  /** Tokenize a batch of values using the default [[UniversalTokenizer]]. */
  def tokenizeBatch(values: Iterable[Any]): List[Token] =
    values.iterator.map(defaultUniversal.tokenize).toList

  /** Tokenize a batch of values using the supplied [[BaseTokenizer]]. */
  def tokenizeBatch[T](values: Iterable[T], tokenizer: BaseTokenizer[T]): List[Token] =
    values.iterator.map(tokenizer.tokenize).toList

  /** Tokenize a single value using the supplied [[BaseTokenizer]]. */
  def tokenize[T](value: T, tokenizer: BaseTokenizer[T]): Token =
    tokenizer.tokenize(value)
}