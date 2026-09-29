package tokenizer

import java.nio.charset.{Charset, StandardCharsets}
import java.time.{LocalDate, LocalDateTime, Instant, ZonedDateTime, ZoneId}
import java.time.format.DateTimeFormatter

import io.circe.{Json, JsonObject}

import scala.collection.mutable
import scala.util.{Failure, Success, Try}

// ============================================================================
// Structural-typing protocols expressed as Scala traits
// ============================================================================

/** Anything that can be rendered as a token string. */
trait Tokenizable {
  def toToken: String
}

/** Anything that exposes a length. */
trait HasLength {
  def length: Int
}

/** Consumer side of tokens: contravariant in its argument. */
trait TokenProcessor[-T] {
  def process(item: T): Unit
}

// ============================================================================
// Token type enumeration
// ============================================================================

sealed trait TokenType {
  def value: String

  override def toString: String = s"TokenType($value)"
}

object TokenType {

  case object STRING     extends TokenType { val value: String = "string"     }
  case object NUMERIC    extends TokenType { val value: String = "numeric"    }
  case object TEMPORAL   extends TokenType { val value: String = "temporal"   }
  case object STRUCTURED extends TokenType { val value: String = "structured" }
  case object BINARY     extends TokenType { val value: String = "binary"     }
  case object NULL       extends TokenType { val value: String = "null"       }

  private val byValue: Map[String, TokenType] = Map(
    "string" -> STRING,
    "numeric" -> NUMERIC,
    "temporal" -> TEMPORAL,
    "structured" -> STRUCTURED,
    "binary" -> BINARY,
    "null" -> NULL
  )

  def fromValue(v: String): Option[TokenType] = byValue.get(v)
}

// ============================================================================
// Core token and batch types
// ============================================================================

/**
 * Immutable, fully-value token.
 *
 * `withMetadata` returns a copy with merged extra entries, mirroring the
 * `with_metadata` helper from the Python module.
 */
final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: Map[String, Any] = Map.empty
) {

  def withMetadata(extra: Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)

  /** Varargs convenience mirroring Python's `with_metadata(**kwargs)`. */
  def withMetadata(entries: (String, Any)*): Token =
    copy(metadata = metadata ++ entries.toMap)

  def withMetadataEntry(key: String, v: Any): Token =
    copy(metadata = metadata.updated(key, v))
}

/**
 * Mutable batch of tokens. Once marked processed, additional tokens may not
 * be added.
 */
final class MutableTokenBatch {

  private val buffer: mutable.ListBuffer[Token] = mutable.ListBuffer.empty
  private var processedFlag: Boolean           = false

  def tokens: List[Token] = buffer.toList

  def isProcessed: Boolean = processedFlag

  def add(token: Token): Unit = {
    if (processedFlag)
      throw new IllegalStateException("Batch already processed")
    buffer += token
  }

  def markProcessed(): Unit = {
    processedFlag = true
  }
}

object MutableTokenBatch {
  def apply(): MutableTokenBatch = new MutableTokenBatch
}

// ============================================================================
// Generic, variance-aware containers
// ============================================================================

/** Covariant holder of tokens of type T. */
final class TokenContainer[+T](items: Seq[T]) {

  private val itemsVec: Vector[T] = items.toVector

  def getAll: Vector[T] = itemsVec

  def mapTokens(f: T => String): List[String] =
    itemsVec.iterator.map(f).toList

  def size: Int = itemsVec.length
}

object TokenContainer {
  def apply[T](items: Seq[T]): TokenContainer[T] = new TokenContainer[T](items)
  def empty[T]: TokenContainer[T]                     = new TokenContainer[T](Seq.empty)
}

/** Contravariant sink accepting supertypes of T. */
final class TokenSink[-T] {

  private val received: mutable.ListBuffer[Any] = mutable.ListBuffer.empty

  def receive(item: T): Unit = received += item

  /** Drain everything received so far. Returned as `List[Any]` to match the Python implementation. */
  def drain(): List[Any] = {
    val copy = received.toList
    received.clear()
    copy
  }

  def size: Int = received.length
}

object TokenSink {
  def apply[T](): TokenSink[T] = new TokenSink[T]
}

/** Invariant handler — exact type matching required. */
final class BivariantHandler[T](initial: T) {

  private var value: T = initial

  def get: T = value

  def set(v: T): Unit = { value = v }

  def transform(f: T => T): T = {
    value = f(value)
    value
  }
}

object BivariantHandler {
  def apply[T](initial: T): BivariantHandler[T] = new BivariantHandler[T](initial)
}

// ============================================================================
// Tokenizer abstractions
// ============================================================================

abstract class BaseTokenizer[T] {

  def tokenize(value: T): Token

  /** Lazy tokenization of a sequence of values. */
  def tokenizeBatch(values: Iterable[T]): Iterator[Token] =
    values.iterator.map(tokenize)

  /** Strict version of tokenizeBatch returning a `List`. */
  def tokenizeAll(values: Iterable[T]): List[Token] =
    values.iterator.map(tokenize).toList
}

object BaseTokenizer {
  def apply[T](f: T => Token): BaseTokenizer[T] = new BaseTokenizer[T] {
    override def tokenize(value: T): Token = f(value)
  }
}

// ============================================================================
// Concrete tokenizers
// ============================================================================

/**
 * Tokenizer for `String` or `Array[Byte]` values.
 *
 * Bytes are decoded using the configured charset before any normalizer is
 * applied.
 */
final class StringTokenizer(
    val encoding: Charset = StandardCharsets.UTF_8,
    val normalizer: String => String = identity
) extends BaseTokenizer[Any] {

  def this(encodingName: String, normalizerFn: String => String) =
    this(Charset.forName(encodingName), normalizerFn)

  override def tokenize(value: Any): Token = {
    val strValue: String = value match {
      case bytes: Array[Byte] => new String(bytes, encoding)
      case s: String          => s
      case other =>
        throw new IllegalArgumentException(
          s"StringTokenizer expected String or Array[Byte] but got ${other.getClass.getName}"
        )
    }
    Token(normalizer(strValue), TokenType.STRING)
  }
}

object StringTokenizer {
  def apply(
      encoding: Charset = StandardCharsets.UTF_8,
      normalizer: String => String = identity
  ): StringTokenizer = new StringTokenizer(encoding, normalizer)

  def apply(
      encodingName: String,
      normalizerFn: String => String
  ): StringTokenizer = new StringTokenizer(encodingName, normalizerFn)
}

/**
 * Tokenizer for numeric inputs.
 *
 * The Python module's mutable default argument is naturally avoided in
 * Scala by relying on immutable `Map` defaults.
 */
final class NumericTokenizer(
    val precision: Int = 6,
    val formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[Any] {

  override def tokenize(value: Any): Token = {
    val strValue: String = value match {
      case bd: BigDecimal =>
        val rounded =
          if (formatOptions.contains("rounding"))
            bd.setScale(precision, parseRounding(formatOptions("rounding")))
          else bd.setScale(precision, BigDecimal.RoundingMode.HALF_UP)
        rounded.toString
      case d: Double =>
        String.format(s"%.${precision}f", d.asInstanceOf[AnyRef])
      case f: Float =>
        String.format(s"%.${precision}f", f.asInstanceOf[AnyRef])
      case n: Number =>
        n.toString
      case other =>
        throw new IllegalArgumentException(
          s"NumericTokenizer expected a numeric value but got ${other.getClass.getName}"
        )
    }

    val originalType: String = value match {
      case _: BigDecimal    => "Decimal"
      case _: Double        => "float"
      case _: Float         => "float"
      case _: Int | _: Long => "int"
      case _: Number        => value.getClass.getSimpleName
      case other            => other.getClass.getName
    }

    Token(strValue, TokenType.NUMERIC, Map("original_type" -> originalType))
  }

  private def parseRounding(raw: Any): BigDecimal.RoundingMode.Value = raw match {
    case s: String =>
      BigDecimal.RoundingMode.values
        .find(_.toString.equalsIgnoreCase(s))
        .getOrElse(BigDecimal.RoundingMode.HALF_UP)
    case mode: BigDecimal.RoundingMode.Value => mode
    case _ => BigDecimal.RoundingMode.HALF_UP
  }
}

object NumericTokenizer {
  def apply(precision: Int = 6): NumericTokenizer =
    new NumericTokenizer(precision, Map.empty)
}

/**
 * Tokenizer for date / time values.
 *
 * Accepts Scala's `java.time` types: `LocalDateTime`, `LocalDate`,
 * `Instant`, `ZonedDateTime`.
 */
final class TemporalTokenizer(val format: Option[String] = None)
    extends BaseTokenizer[Any] {

  import TemporalTokenizer._

  override def tokenize(value: Any): Token = {
    val resolvedFormat: String = format.getOrElse {
      value match {
        case _: LocalDateTime | _: ZonedDateTime | _: Instant => ISO_FORMAT
        case _                                                => DATE_FORMAT
      }
    }

    val formatter = DateTimeFormatter.ofPattern(resolvedFormat)

    val formatted: String = value match {
      case ld: LocalDateTime  => ld.format(formatter)
      case ld: LocalDate      => ld.format(formatter)
      case zdt: ZonedDateTime => zdt.format(formatter)
      case inst: Instant      => inst.atZone(ZoneId.systemDefault()).format(formatter)
      case other =>
        throw new IllegalArgumentException(
          s"TemporalTokenizer expected a date/time value but got ${other.getClass.getName}"
        )
    }

    Token(formatted, TokenType.TEMPORAL)
  }
}

object TemporalTokenizer {
  val ISO_FORMAT: String  = "yyyy-MM-dd'T'HH:mm:ss"
  val DATE_FORMAT: String = "yyyy-MM-dd"

  def apply(): TemporalTokenizer                    = new TemporalTokenizer(None)
  def apply(format: String): TemporalTokenizer      = new TemporalTokenizer(Some(format))
}

/**
 * Dispatches to the appropriate concrete tokenizer based on runtime type.
 * Uses Scala's pattern matching rather than Python's `isinstance` chains.
 */
final class UniversalTokenizer {

  private val stringTokenizer  = new StringTokenizer()
  private val numericTokenizer = new NumericTokenizer()
  private val temporalTokenizer = new TemporalTokenizer()

  def tokenize(value: Any): Token = Option(value) match {
    case None =>
      Token("NULL", TokenType.NULL)

    case Some(_) if isTokenizable(value) =>
      val t = value.asInstanceOf[Tokenizable]
      Token(t.toToken, TokenType.STRUCTURED)

    case Some(_: String) | Some(_: Array[Byte]) =>
      stringTokenizer.tokenize(value)

    case Some(_: BigDecimal | _: Double | _: Float | _: Int | _: Long | _: Short | _: Byte) =>
      numericTokenizer.tokenize(value)

    case Some(_: LocalDate | _: LocalDateTime | _: ZonedDateTime | _: Instant) =>
      temporalTokenizer.tokenize(value)

    case Some(other) =>
      Token(other.toString, TokenType.STRING, Map("fallback" -> true))
  }

  private def isTokenizable(v: Any): Boolean = v match {
    case _: Tokenizable => true
    case _              => false
  }

  /** Tokenize a batch of inputs lazily. */
  def tokenizeBatch(values: Iterable[Any]): Iterator[Token] =
    values.iterator.map(tokenize)

  /** Strict version returning a `List[Token]`. */
  def tokenizeAll(values: Iterable[Any]): List[Token] =
    values.iterator.map(tokenize).toList

  /** Convenience for tokenizing `null`. */
  def tokenizeNull: Token = Token("NULL", TokenType.NULL)
}

object UniversalTokenizer {
  def apply(): UniversalTokenizer = new UniversalTokenizer
}

// ============================================================================
// Registry with nested generics
// ============================================================================

final class TokenRegistry[T] {

  private val registry: mutable.Map[String, TokenContainer[T]]          = mutable.Map.empty
  private val handlers: mutable.ListBuffer[T => Option[Token]]          = mutable.ListBuffer.empty

  def register(key: String, container: TokenContainer[T]): Unit = {
    registry(key) = container
  }

  def addHandler(handler: T => Option[Token]): Unit = {
    handlers += handler
  }

  /** Process all items in the container referenced by `key` through all handlers. */
  def process(key: String): List[Option[Token]] = registry.get(key) match {
    case None => List.empty
    case Some(container) =>
      container.getAll.map { item =>
        val firstMatch = handlers.iterator.map(_.apply(item)).find(_.isDefined).flatten
        firstMatch match {
          case t @ Some(_) => t
          case None        => None
        }
      }.toList
  }

  def keys: Set[String] = registry.keySet.toSet
}

// ============================================================================
// Lightweight functor / monad
// ============================================================================

/** Functor-like wrapper offering `map`, `flatMap`, and `getOrElse`. */
class TokenFunctor[T] protected (protected val underlying: Option[T]) {

  def this(value: T) = this(Option(value))

  def map[U](f: T => U): TokenFunctor[U] =
    new TokenFunctor[U](underlying.map(f))

  def flatMap[U](f: T => TokenFunctor[U]): TokenFunctor[U] =
    underlying match {
      case Some(v) => f(v)
      case None    => new TokenFunctor[U](None)
    }

  def getOrElse(default: => T): T = underlying.getOrElse(default)

  /** Unsafe getter — returns the underlying value or throws if empty. */
  def get: T = underlying.getOrElse(
    throw new NoSuchElementException("TokenFunctor.get on empty functor")
  )

  def isEmpty: Boolean  = underlying.isEmpty
  def nonEmpty: Boolean = underlying.nonEmpty

  def value: Option[T] = underlying
}

object TokenFunctor {
  def apply[T](value: T): TokenFunctor[T]            = new TokenFunctor[T](value)
  def empty[T]: TokenFunctor[T]                      = new TokenFunctor[T](None)
  def fromOption[T](opt: Option[T]): TokenFunctor[T] = new TokenFunctor[T](opt)
}

/** Adds applicative-style operations on top of `TokenFunctor`. */
class TokenMonad[T] protected (optValue: Option[T])
    extends TokenFunctor[T](optValue) {

  def this(value: T) = this(Option(value))

  def ap[U](funcMonad: TokenMonad[T => U]): TokenMonad[U] =
    new TokenMonad[U](for {
      f <- funcMonad.underlying
      v <- this.underlying
    } yield f(v))

  /** Lift a value into the monadic context. */
  def pure(value: T): TokenMonad[T] = new TokenMonad[T](Option(value))
}

object TokenMonad {
  def apply[T](value: T): TokenMonad[T] = new TokenMonad[T](value)
  def pure[T](value: T): TokenMonad[T]  = new TokenMonad[T](value)
  def empty[T]: TokenMonad[T]           = new TokenMonad[T](None)
}

// ============================================================================
// JSON tokenizer
// ============================================================================

/**
 * Tokenizer for circe `Json` values.
 *
 * The Python source exposes a recursive `JsonValue` union type. Because the
 * build already pulls in `circe-core`, we lean on `io.circe.Json` for the
 * recursive structure rather than re-inventing a parallel hierarchy.
 */
final class JsonTokenizer(val pretty: Boolean = false) {

  def tokenize(value: Json): Token = {
    val rendered = if (pretty) value.spaces2 else value.noSpaces
    Token(rendered, TokenType.STRUCTURED, Map("json" -> true))
  }

  def tokenizePath(value: Json, path: String): Option[Token] = {
    val parts = path.split('.').toList
    val resolved: Option[Json] =
      parts.foldLeft[Option[Json]](Some(value)) { (current, part) =>
        current.flatMap { json =>
          json.asObject.flatMap(_.apply(part)).orElse {
            json.asArray.flatMap { arr =>
              if (part.matches("\\d+")) {
                val idx = part.toInt
                if (idx >= 0 && idx < arr.length) Some(arr(idx)) else None
              } else None
            }
          }
        }
      }

    resolved.map(tokenize)
  }
}

object JsonTokenizer {
  def apply(pretty: Boolean = false): JsonTokenizer = new JsonTokenizer(pretty)
}

// ============================================================================
// Whitespace tokenizer
// ============================================================================

/**
 * Splits text into whitespace-delimited tokens, with options for case
 * folding, length filtering, and punctuation stripping.
 */
final class WhitespaceTokenizer(
    val lowercase: Boolean = false,
    val minLength: Int = 0,
    val maxLength: Option[Int] = None,
    val stripPunctuation: Boolean = false
) {

  private val punctuation: Set[Char] = ".,!?;:\"'()[]{}".toSet

  private def processToken(word: String): Option[String] = {
    var w = word

    if (stripPunctuation) {
      // Strip leading and trailing punctuation, mirroring Python's
      // `word.strip("".join(self._punctuation))` behavior.
      var start = 0
      var end   = w.length - 1
      while (start <= end && punctuation.contains(w(start))) start += 1
      while (end >= start && punctuation.contains(w(end))) end -= 1
      if (start > end) return None
      w = w.substring(start, end + 1)
    }

    if (lowercase) w = w.toLowerCase

    if (w.length < minLength) return None

    val truncated = maxLength match {
      case Some(max) if w.length > max => w.substring(0, max)
      case _                            => w
    }

    if (truncated.isEmpty) None else Some(truncated)
  }

  def tokenize(text: String): List[Token] = {
    val words = text.split("\\s+").toList.filter(_.nonEmpty)
    words.zipWithIndex.flatMap { case (word, idx) =>
      processToken(word).map { processed =>
        Token(
          value = processed,
          tokenType = TokenType.STRING,
          metadata = Map("position" -> idx, "original" -> word)
        )
      }
    }
  }

  def tokenizeToStrings(text: String): List[String] =
    tokenize(text).map(_.value)

  def tokenizeWithPositions(text: String): List[(String, Int, Int)] = {
    val result = mutable.ListBuffer.empty[(String, Int, Int)]
    var currentPos = 0
    text.split("\\s+").toList.filter(_.nonEmpty).foreach { word =>
      val start = text.indexOf(word, currentPos)
      val end   = if (start >= 0) start + word.length else currentPos
      processToken(word).foreach { processed =>
        result += ((processed, start, end))
      }
      currentPos = math.max(end, currentPos)
    }
    result.toList
  }

  def countTokens(text: String): Int = tokenize(text).length
}

object WhitespaceTokenizer {
  def apply(
      lowercase: Boolean = false,
      minLength: Int = 0,
      maxLength: Option[Int] = None,
      stripPunctuation: Boolean = false
  ): WhitespaceTokenizer =
    new WhitespaceTokenizer(lowercase, minLength, maxLength, stripPunctuation)
}

// ============================================================================
// Fluent builder
// ============================================================================

/**
 * Fluent builder for composing normalizer/validator chains plus metadata
 * into a single `T => Token` function.
 */
final class TokenizerBuilder[T] {

  private val normalizers: mutable.ListBuffer[String => String] = mutable.ListBuffer.empty
  private val validators:  mutable.ListBuffer[T => Boolean]    = mutable.ListBuffer.empty
  private var metadataBag:  Map[String, Any]                   = Map.empty

  def withNormalizer(f: String => String): this.type = {
    normalizers += f
    this
  }

  def withValidator(f: T => Boolean): this.type = {
    validators += f
    this
  }

  def withMetadata(entries: (String, Any)*): this.type = {
    metadataBag = metadataBag ++ entries.toMap
    this
  }

  def withMetadataMap(extra: Map[String, Any]): this.type = {
    metadataBag = metadataBag ++ extra
    this
  }

  def build(): T => Token = {
    val frozenNormalizers = normalizers.toList
    val frozenValidators  = validators.toList
    val frozenMetadata    = metadataBag

    (value: T) => {
      frozenValidators.foreach { v =>
        if (!v(value))
          throw new IllegalArgumentException(s"Validation failed for $value")
      }

      var strValue: String = Option(value).map(_.toString).getOrElse("")
      frozenNormalizers.foreach { n => strValue = n(strValue) }

      Token(strValue, TokenType.STRING, frozenMetadata)
    }
  }
}

object TokenizerBuilder {
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]
}

// ============================================================================
// Module-level convenience helpers
//
// These functions are the Scala counterparts of the Python module-level
// helpers implied by the task description: `tokenize`, `tokenizeBatch`,
// `toToken`, and `withMetadata`.
// ============================================================================

package object tokenizer {

  /** Default shared universal tokenizer used by the module-level helpers. */
  private val defaultUniversal: UniversalTokenizer = new UniversalTokenizer

  /** Tokenize a single value using the default [[UniversalTokenizer]]. */
  def tokenize(value: Any): Token = defaultUniversal.tokenize(value)

  /** Lazily tokenize a batch of inputs. */
  def tokenizeBatch(values: Iterable[Any]): Iterator[Token] =
    defaultUniversal.tokenizeBatch(values)

  /** Convenience for converting any [[Tokenizable]] to its token string. */
  def toToken(value: Tokenizable): String = value.toToken

  /** Return a new token with additional metadata merged in. */
  def withMetadata(token: Token, extra: Map[String, Any]): Token =
    token.withMetadata(extra)
}