package tokenizer

import java.time.{LocalDate, LocalDateTime}
import java.time.format.DateTimeFormatter

import io.circe.{Encoder, Json, JsonNumber, JsonObject}
import io.circe.syntax._

import scala.collection.mutable
import scala.collection.mutable.{ArrayBuffer, ListBuffer}

/**
 * Tokenizer module for converting various input types to standardized string tokens.
 *
 * Demonstrates Scala 2.13 idioms:
 *  - sealed hierarchies for TokenType
 *  - case classes for immutable data
 *  - covariant / contravariant / invariant generics
 *  - structural-typing replacement via traits and runtime reflection
 *  - circe for recursive JSON encoding
 */
object Tokenizer {

  // ---------------------------------------------------------------------------
  // Public top-level convenience functions
  // ---------------------------------------------------------------------------

  /** Convert an arbitrary value into a Token using the universal dispatcher. */
  def tokenize(value: Any): Token = UniversalTokenizer().tokenize(value)

  /** Lazily tokenize a sequence of arbitrary values. */
  def tokenizeBatch(values: Iterable[_]): Iterator[Token] = valueIterator(values)

  /** Convert any [[Tokenizable]] value to its token string. */
  def toToken(t: Tokenizable): String = t.toToken

  /** Return a new Token with additional metadata merged in (varargs of pairs). */
  def withMetadata(token: Token, extra: (String, Any)*): Token =
    token.withMetadata(extra: _*)

  /** Return a new Token with additional metadata merged in (from a Map). */
  def withMetadata(token: Token, extra: Map[String, Any]): Token =
    token.copy(metadata = token.metadata ++ extra)

  private def valueIterator(values: Iterable[_]): Iterator[Token] =
    values.iterator.map(UniversalTokenizer().tokenize)
}

// ============================================================================
// TokenType sealed hierarchy
// ============================================================================

sealed trait TokenType {
  def value: String
}

object TokenType {
  case object STRING    extends TokenType { val value: String = "string"    }
  case object NUMERIC   extends TokenType { val value: String = "numeric"   }
  case object TEMPORAL  extends TokenType { val value: String = "temporal"  }
  case object STRUCTURED extends TokenType { val value: String = "structured" }
  case object BINARY    extends TokenType { val value: String = "binary"    }
  case object NULL      extends TokenType { val value: String = "null"      }

  /** Look up a [[TokenType]] from its string value. */
  def fromString(s: String): Option[TokenType] = all.find(_.value == s)

  private val all: List[TokenType] =
    List(STRING, NUMERIC, TEMPORAL, STRUCTURED, BINARY, NULL)
}

// ============================================================================
// Core Token classes
// ============================================================================

/** Immutable token representation. */
final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: Map[String, Any] = Map.empty
) {
  /** Return a new token with additional metadata merged in (varargs of pairs). */
  def withMetadata(extra: (String, Any)*): Token = {
    val merged = extra.foldLeft(metadata) { case (acc, (k, v)) => acc.updated(k, v) }
    copy(metadata = merged)
  }

  /** Return a new token with additional metadata merged in (from a Map). */
  def withMetadata(extra: Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)
}

object Token {
  def apply(value: String, tokenType: TokenType): Token =
    new Token(value, tokenType, Map.empty)
}

/**
 * Mutable batch of tokens - contrasts with the immutable [[Token]].
 *
 * Mirrors the Python MutableTokenBatch anti-pattern of mixing mutable state
 * with a "frozen" flag. Provided for parity with the original module.
 */
final class MutableTokenBatch(initial: List[Token] = Nil) {
  private val buffer: ArrayBuffer[Token] = ArrayBuffer(initial: _*)
  private var processed: Boolean = false

  def tokens: List[Token] = buffer.toList
  def isProcessed: Boolean = processed

  def add(token: Token): Unit = {
    if (processed)
      throw new IllegalStateException("Batch already processed")
    buffer += token
  }

  def markProcessed(): Unit = {
    processed = true
  }

  /** Replace contents with the given tokens and mark processed. */
  def freeze(tokens: Seq[Token]): List[Token] = {
    buffer.clear()
    buffer ++= tokens
    markProcessed()
    buffer.toList
  }
}

object MutableTokenBatch {
  def empty: MutableTokenBatch = new MutableTokenBatch(Nil)
}

// ============================================================================
// Protocol-style traits (Scala replacement for Python's structural typing)
// ============================================================================

/** Anything that can be converted into a token string. */
trait Tokenizable {
  def toToken: String
}

/** Anything that has a length. */
trait HasLength {
  def length: Int
}

/** Contravariant processor that consumes tokens. */
trait TokenProcessor[-T] {
  def process(item: T): Unit
}

// ============================================================================
// Generic container classes
// ============================================================================

/** Covariant container - the type parameter only appears in output positions. */
final class TokenContainer[+T](items: Seq[T]) {
  private val stored: Vector[T] = items.toVector

  def getAll: Vector[T] = stored

  def mapTokens(f: T => String): List[String] = stored.iterator.map(f).toList

  def size: Int = stored.length
}

object TokenContainer {
  def apply[T](items: Seq[T]): TokenContainer[T] = new TokenContainer[T](items)

  def empty[T]: TokenContainer[T] = new TokenContainer[T](Vector.empty)
}

/** Contravariant sink - the type parameter only appears in input positions. */
final class TokenSink[-T] {
  private val received: ArrayBuffer[Any] = ArrayBuffer.empty

  def receive(item: T): Unit = received += item

  def drain(): List[Any] = {
    val copy = received.toList
    received.clear()
    copy
  }
}

object TokenSink {
  def apply[T]: TokenSink[T] = new TokenSink[T]
}

/** Invariant handler - exact type matching required. */
final class BivariantHandler[T](default: T) {
  private var current: T = default

  def get: T = current

  def set(value: T): Unit = {
    current = value
  }

  def transform(f: T => T): T = {
    current = f(current)
    current
  }
}

object BivariantHandler {
  def apply[T](default: T): BivariantHandler[T] = new BivariantHandler[T](default)
}

// ============================================================================
// Tokenizer implementations
// ============================================================================

/** Abstract base tokenizer with generic input type. */
abstract class BaseTokenizer[T] {

  /** Convert a single value into a [[Token]]. */
  def tokenize(value: T): Token

  /** Lazily tokenize a sequence of values. */
  def tokenizeBatch(values: Iterable[T]): Iterator[Token] =
    values.iterator.map(tokenize)
}

/** Tokenizer for string and bytes types. */
final class StringTokenizer(
    encoding: String = "utf-8",
    normalizer: Option[String => String] = None
) extends BaseTokenizer[Either[String, Array[Byte]]] {

  private val normalizeFn: String => String = normalizer.getOrElse(identity)

  override def tokenize(value: Either[String, Array[Byte]]): Token = {
    val strValue = value match {
      case Right(bytes) => new String(bytes, encoding)
      case Left(s)      => s
    }
    Token(normalizeFn(strValue), TokenType.STRING)
  }
}

object StringTokenizer {
  def apply(encoding: String = "utf-8"): StringTokenizer =
    new StringTokenizer(encoding, None)

  def apply(encoding: String, normalizer: String => String): StringTokenizer =
    new StringTokenizer(encoding, Some(normalizer))

  /** Convenience for callers that already have a String value. */
  def fromString(value: String): Token =
    new StringTokenizer().tokenize(Left(value))

  /** Convenience for callers that already have a byte array. */
  def fromBytes(value: Array[Byte]): Token =
    new StringTokenizer().tokenize(Right(value))
}

/**
 * Tokenizer for numeric values with precision handling.
 *
 * Accepts any numeric value via Scala's [[AnyVal]] boxed numerics plus
 * [[scala.math.BigDecimal]] for arbitrary precision. The mutable default
 * argument that the Python version accidentally exposed is replaced with an
 * immutable empty map.
 */
final class NumericTokenizer(
    precision: Int = 6,
    formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[Any] {

  override def tokenize(value: Any): Token = {
    val strValue: String = value match {
      case bd: scala.math.BigDecimal =>
        val s = bd.toString
        if (s.contains('.')) s else bd.toString
      case d: Double  => formatNumeric(d.toString)
      case f: Float   => formatNumeric(f.toString)
      case n: Number  => n.toString
      case other      => throw new IllegalArgumentException(
                          s"NumericTokenizer cannot tokenize non-numeric value: $other"
                        )
    }

    val formatted = value match {
      case bd: scala.math.BigDecimal => formatBigDecimal(bd)
      case d: Double                 => formatDouble(d)
      case f: Float                  => formatDouble(f.toDouble)
      case n: Number                 => n.toString
      case other                     => strValue
    }

    val metadata = Map("original_type" -> originalTypeName(value))
    Token(formatted, TokenType.NUMERIC, metadata)
  }

  private def formatBigDecimal(bd: scala.math.BigDecimal): String =
    bd.setScale(precision, scala.math.BigDecimal.RoundingMode.HALF_UP).toString

  private def formatDouble(d: Double): String =
    BigDecimal(d).setScale(precision, scala.math.BigDecimal.RoundingMode.HALF_UP).toString

  private def formatNumeric(s: String): String = s

  private def originalTypeName(v: Any): String = v match {
    case _: scala.math.BigDecimal => "Decimal"
    case _: Double                => "float"
    case _: Float                 => "float"
    case _: Long                  => "int"
    case _: Int                   => "int"
    case _: Short                 => "int"
    case _: Byte                  => "int"
    case _: java.math.BigDecimal  => "Decimal"
    case _: java.math.BigInteger  => "int"
    case n                        => n.getClass.getSimpleName
  }
}

object NumericTokenizer {
  def apply(): NumericTokenizer = new NumericTokenizer()
  def apply(precision: Int): NumericTokenizer = new NumericTokenizer(precision)
}

/** Tokenizer for date / time types. */
final class TemporalTokenizer(
    formatStr: Option[String] = None
) extends BaseTokenizer[Any] {

  import TemporalTokenizer.{IsoFormatter, DateFormatter}

  override def tokenize(value: Any): Token = value match {
    case dt: LocalDateTime =>
      val fmt = formatStr.map(DateTimeFormatter.ofPattern).getOrElse(IsoFormatter)
      Token(dt.format(fmt), TokenType.TEMPORAL)
    case d: LocalDate =>
      val fmt = formatStr.map(DateTimeFormatter.ofPattern).getOrElse(DateFormatter)
      Token(d.format(fmt), TokenType.TEMPORAL)
    case other =>
      throw new IllegalArgumentException(
        s"TemporalTokenizer cannot tokenize value: $other"
      )
  }
}

object TemporalTokenizer {
  val IsoFormatter: DateTimeFormatter  = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss")
  val DateFormatter: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")

  def apply(): TemporalTokenizer = new TemporalTokenizer(None)
  def apply(formatStr: String): TemporalTokenizer = new TemporalTokenizer(Some(formatStr))
}

// ============================================================================
// Advanced: Universal dispatch tokenizer
// ============================================================================

/**
 * Tokenizer that handles multiple runtime types via overloading-style
 * dispatch helpers.
 *
 * Scala's static type system forces us to discriminate on [[Any]] at
 * runtime using [[scala.Any.isInstanceOf]] pattern matching rather than
 * Python-style duck typing. Each [[tokenize]] overload simply forwards
 * to [[tokenizeAny]] which centralises the dispatch logic.
 */
final class UniversalTokenizer {

  private val stringTokenizer   = StringTokenizer()
  private val numericTokenizer  = NumericTokenizer()
  private val temporalTokenizer = TemporalTokenizer()

  /** Canonical Token used to represent a null input. */
  val tokenizeNull: Token = Token("NULL", TokenType.NULL)

  def tokenize(value: String): Token = stringTokenizer.tokenize(Left(value))
  def tokenize(value: Array[Byte]): Token = stringTokenizer.tokenize(Right(value))
  def tokenize(value: Int): Token = numericTokenizer.tokenize(value)
  def tokenize(value: Long): Token = numericTokenizer.tokenize(value)
  def tokenize(value: Double): Token = numericTokenizer.tokenize(value)
  def tokenize(value: Float): Token = numericTokenizer.tokenize(value)
  def tokenize(value: scala.math.BigDecimal): Token = numericTokenizer.tokenize(value)
  def tokenize(value: LocalDateTime): Token = temporalTokenizer.tokenize(value)
  def tokenize(value: LocalDate): Token = temporalTokenizer.tokenize(value)
  def tokenize(value: Tokenizable): Token =
    Token(value.toToken, TokenType.STRUCTURED)
  def tokenize(value: Token): Token = value
  def tokenize(value: Null): Token = tokenizeNull
  def tokenize(value: Json): Token = new JsonTokenizer().tokenize(JsonValue.fromCirce(value))

  /** Fallback dispatcher used by the top-level [[Tokenizer.tokenize]] helper. */
  def tokenize(value: Any): Token = value match {
    case null                              => Token("NULL", TokenType.NULL)
    case t: Tokenizable                    => Token(t.toToken, TokenType.STRUCTURED)
    case t: Token                          => t
    case s: String                         => tokenize(s)
    case b: Array[Byte]                    => tokenize(b)
    case i: Int                            => tokenize(i)
    case l: Long                           => tokenize(l)
    case d: Double                         => tokenize(d)
    case f: Float                          => tokenize(f)
    case bd: scala.math.BigDecimal         => tokenize(bd)
    case dt: LocalDateTime                 => tokenize(dt)
    case d: LocalDate                      => tokenize(d)
    case other                             => Token(other.toString, TokenType.STRING, Map("fallback" -> true))
  }
}

object UniversalTokenizer {
  def apply(): UniversalTokenizer = new UniversalTokenizer
}

// ============================================================================
// Token registry with complex nested generics
// ============================================================================

/**
 * Registry mapping string keys to [[TokenContainer]]s and a list of handlers
 * that can convert items of type [[T]] into an optional [[Token]].
 */
final class TokenRegistry[T] {

  private val registry: mutable.Map[String, TokenContainer[T]] = mutable.Map.empty
  private val handlers: ListBuffer[T => Option[Token]] = ListBuffer.empty

  def register(key: String, container: TokenContainer[T]): Unit = {
    registry(key) = container
  }

  def addHandler(handler: T => Option[Token]): Unit = {
    handlers += handler
  }

  /** Process every item in the container registered under `key`. */
  def process(key: String): List[Option[Token]] = {
    registry.get(key) match {
      case None        => List.empty
      case Some(cont)  =>
        cont.getAll.iterator.map { item =>
          handlers.iterator.map(_.apply(item)).find(_.isDefined).flatten
        }.toList
    }
  }

  def keys: Iterable[String] = registry.keys
  def handlersSnapshot: List[T => Option[Token]] = handlers.toList
}

object TokenRegistry {
  def apply[T]: TokenRegistry[T] = new TokenRegistry[T]
}

// ============================================================================
// Higher-kinded type simulation
// ============================================================================

/** A minimal functor wrapper used to mirror Python's TokenFunctor. */
class TokenFunctor[T](value: T) {
  private val inner: T = value

  /** Unwrap the contained value (may be `null` if the original was null). */
  def get: T = inner

  def map[U](f: T => U): TokenFunctor[U] = new TokenFunctor(f(inner))
  def flatMap[U](f: T => TokenFunctor[U]): TokenFunctor[U] = f(inner)
  def getOrElse[U >: T](default: => U): U =
    if (inner == null) default else inner
  def valueOr(default: => T): T = if (inner == null) default else inner.asInstanceOf[T]
}

object TokenFunctor {
  def apply[T](value: T): TokenFunctor[T] = new TokenFunctor(value)
}

/** Monad-style operations layered on top of [[TokenFunctor]]. */
class TokenMonad[T](value: T) extends TokenFunctor[T](value) {

  def pure[U](v: U): TokenMonad[U] = new TokenMonad(v)

  def ap[U](funcWrapped: TokenMonad[T => U]): TokenMonad[U] = {
    val fn: T => U = funcWrapped.valueOr((t: T) => t.asInstanceOf[U])
    new TokenMonad(fn(valueOr(null.asInstanceOf[T])))
  }
}

object TokenMonad {
  def apply[T](value: T): TokenMonad[T] = new TokenMonad(value)
  def pure[T](value: T): TokenMonad[T] = new TokenMonad(value)
}

// ============================================================================
// JSON Structure Tokenization
// ============================================================================

/** Recursive JSON value type backed by circe's [[Json]]. */
sealed trait JsonValue {
  def toJson: Json = JsonValue.toJson(this)
}

object JsonValue {
  case class JString(value: String)                                     extends JsonValue
  case class JNumber(value: scala.math.BigDecimal)                     extends JsonValue
  case class JBool(value: Boolean)                                     extends JsonValue
  case object JNull                                                    extends JsonValue
  case class JArray(values: List[JsonValue])                           extends JsonValue
  case class JObject(fields: Map[String, JsonValue])                   extends JsonValue

  def toJson(v: JsonValue): Json = v match {
    case JString(s)     => Json.fromString(s)
    case JNumber(n)     => Json.fromBigDecimal(n)
    case JBool(b)       => Json.fromBoolean(b)
    case JNull          => Json.Null
    case JArray(items)  => Json.fromValues(items.map(toJson))
    case JObject(fields) =>
      JsonObject.fromMap(fields.map { case (k, v) => k -> toJson(v) }).asJson
  }

  /** Convert a circe [[Json]] tree into the recursive [[JsonValue]] ADT. */
  def fromCirce(j: Json): JsonValue = j.fold(
    JNull,
    JBool(_),
    n => JNumber(n.toBigDecimal.getOrElse(BigDecimal(n.toDouble))),
    JString(_),
    arr => JArray(arr.toList.map(fromCirce)),
    obj => JObject(obj.toMap.map { case (k, v) => k -> fromCirce(v) })
  )

  /** Encoder so the case-class hierarchy can be serialized via `.asJson`. */
  implicit val encoder: Encoder[JsonValue] = new Encoder[JsonValue] {
    final def apply(v: JsonValue): Json = toJson(v)
  }
}

/**
 * Tokenizer for JSON structures. The recursive [[JsonValue]] is encoded
 * recursively through [[JsonValue.toJson]].
 */
final class JsonTokenizer(val pretty: Boolean = false) {

  import JsonValue._

  def tokenize(value: JsonValue): Token = {
    val json = value.toJson
    val text = if (pretty) json.spaces2 else json.noSpaces
    Token(text, TokenType.STRUCTURED, Map("json" -> true))
  }

  /** Convenience overload that accepts a circe [[Json]] value directly. */
  def tokenize(value: Json): Token = tokenize(JsonValue.fromCirce(value))

  /** Extract and tokenize a value at the dotted JSON path. */
  def tokenizePath(value: JsonValue, path: String): Option[Token] = {
    val parts = path.split('.').toList
    navigate(value, parts).map(tokenize)
  }

  private def navigate(value: JsonValue, parts: List[String]): Option[JsonValue] =
    parts match {
      case Nil      => Some(value)
      case h :: rest =>
        value match {
          case JObject(fields) =>
            fields.get(h).flatMap(navigate(_, rest))
          case JArray(items) if h.forall(_.isDigit) =>
            val idx = h.toInt
            if (idx >= 0 && idx < items.length) navigate(items(idx), rest)
            else None
          case _ => None
        }
    }
}

object JsonTokenizer {
  def apply(): JsonTokenizer = new JsonTokenizer(false)
  def apply(pretty: Boolean): JsonTokenizer = new JsonTokenizer(pretty)
}

// ============================================================================
// Whitespace Tokenizer
// ============================================================================

/**
 * Basic tokenizer that splits text on whitespace and applies optional
 * normalisation rules. Used for NLP-style processing pipelines.
 */
final class WhitespaceTokenizer(
    val lowercase: Boolean = false,
    val minLength: Int = 0,
    val maxLength: Option[Int] = None,
    val stripPunctuation: Boolean = false
) {

  private val punctuation: Set[Char] = Set(' ', ',', '.', '!', '?', ';', ':',
    '\'', '"', '(', ')', '[', ']', '{', '}').filterNot(_.isWhitespace) -- Set(' ')
  private val punctuationString: String = punctuation.mkString

  /** Process a single word into an optional token string. */
  def processToken(word: String): Option[String] = {
    val stripped =
      if (stripPunctuation) stripPunctuationFrom(word) else word

    val normalised =
      if (lowercase) stripped.toLowerCase
      else stripped

    if (normalised.length < minLength) None
    else maxLength match {
      case Some(max) if normalised.length > max =>
        Some(normalised.take(max))
      case _ =>
        if (normalised.isEmpty) None else Some(normalised)
    }
  }

  /** Strip leading/trailing characters in [[punctuation]] from a word. */
  private def stripPunctuationFrom(word: String): String = {
    var start = 0
    var end   = word.length
    while (start < end && punctuation.contains(word.charAt(start))) start += 1
    while (end > start && punctuation.contains(word.charAt(end - 1))) end -= 1
    if (start >= end) "" else word.substring(start, end)
  }

  /** Split text by whitespace and return a list of tokens. */
  def tokenize(text: String): List[Token] = {
    val words = text.split("\\s+").toList.filter(_.nonEmpty)
    words.zipWithIndex.collect {
      case (word, i) =>
        processToken(word).map { processed =>
          Token(processed, TokenType.STRING, Map("position" -> i, "original" -> word))
        }
    }.flatten
  }

  /** Split text by whitespace and return only the token values. */
  def tokenizeToStrings(text: String): List[String] =
    tokenize(text).map(_.value)

  /** Tokenize text and return (token, start, end) character positions. */
  def tokenizeWithPositions(text: String): List[(String, Int, Int)] = {
    val words = text.split("\\s+").toList.filter(_.nonEmpty)
    var cursor = 0
    val out = ListBuffer.empty[(String, Int, Int)]
    words.foreach { word =>
      val start = text.indexOf(word, cursor)
      if (start >= 0) {
        val end = start + word.length
        processToken(word).foreach(processed => out += ((processed, start, end)))
        cursor = end
      } else {
        cursor += word.length
      }
    }
    out.toList
  }

  /** Number of tokens in the text. */
  def countTokens(text: String): Int = tokenize(text).length
}

object WhitespaceTokenizer {
  def apply(): WhitespaceTokenizer = new WhitespaceTokenizer()
  def apply(
      lowercase: Boolean,
      minLength: Int,
      maxLength: Option[Int],
      stripPunctuation: Boolean
  ): WhitespaceTokenizer =
    new WhitespaceTokenizer(lowercase, minLength, maxLength, stripPunctuation)
}

// ============================================================================
// Fluent Builder
// ============================================================================

/** Fluent builder for assembling a simple tokenizer function. */
final class TokenizerBuilder[T] {

  private val normalizers: ArrayBuffer[String => String] = ArrayBuffer.empty
  private val validators:  ArrayBuffer[T => Boolean]     = ArrayBuffer.empty
  private val meta:        mutable.Map[String, Any]     = mutable.Map.empty

  def withNormalizer(normalizer: String => String): this.type = {
    normalizers += normalizer
    this
  }

  def withValidator(validator: T => Boolean): this.type = {
    validators += validator
    this
  }

  def withMetadata(entries: (String, Any)*): this.type = {
    entries.foreach { case (k, v) => meta(k) = v }
    this
  }

  def withMetadata(entries: Map[String, Any]): this.type = {
    meta ++= entries
    this
  }

  def withMetadata(key: String, value: Any): this.type = {
    meta(key) = value
    this
  }

  /** Build the final tokenizer function. */
  def build(): T => Token = {
    val normalizerSnapshot = normalizers.toList
    val validatorSnapshot  = validators.toList
    val metadataSnapshot   = meta.toMap

    (value: T) => {
      if (!validatorSnapshot.forall(_.apply(value)))
        throw new IllegalArgumentException(s"Validation failed for $value")

      val initial = value match {
        case s: String => s
        case other     => other.toString
      }

      val normalized = normalizerSnapshot.foldLeft(initial)((acc, fn) => fn(acc))
      Token(normalized, TokenType.STRING, metadataSnapshot)
    }
  }
}

object TokenizerBuilder {
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]
}
