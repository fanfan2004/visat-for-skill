package tokenizer

import java.nio.charset.{Charset, StandardCharsets}
import java.time.{LocalDate, LocalDateTime}
import java.time.format.DateTimeFormatter
import scala.collection.mutable
import scala.util.Try

import io.circe.{Json => CirceJson}

// ============================================================================
// Protocol-style traits (structural types in Python, nominal traits in Scala)
// ============================================================================

/** Anything that can be converted to a token string. */
trait Tokenizable {
  def toToken: String
}

/** Anything that exposes a length. */
trait HasLength {
  def length: Int
}

// ============================================================================
// TokenType (sealed hierarchy mirroring the Python Enum)
// ============================================================================

/** Enumeration of the supported token categories. */
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

// ============================================================================
// Core Token case class (immutable; contrast with MutableTokenBatch below)
// ============================================================================

/** Immutable token representation. */
final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: Map[String, Any] = Map.empty
) {
  /** Return a new token with the supplied metadata merged over the existing entries. */
  def withMetadata(entries: (String, Any)*): Token =
    copy(metadata = metadata ++ entries.toMap)

  /** Variant that accepts an explicit Map of additional metadata. */
  def withMetadata(extra: Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)
}

// ============================================================================
// MutableTokenBatch (kept mutable on purpose, mirroring the Python counterpart)
// ============================================================================

/** Mutable batch of tokens - intentionally contrasting with the immutable Token. */
final class MutableTokenBatch {
  private val buffer    = mutable.ListBuffer.empty[Token]
  private var processed: Boolean = false

  /** Snapshot of the tokens currently in the batch. */
  def tokens: List[Token]   = buffer.toList
  def isProcessed: Boolean  = processed

  def add(token: Token): Unit = {
    if (processed)
      throw new IllegalStateException("Batch already processed")
    buffer += token
  }

  def markProcessed(): Unit = { processed = true }
}

// ============================================================================
// Generic container classes (covariance / contravariance / invariance demos)
// ============================================================================

/** Covariant container - can return subtypes. */
final class TokenContainer[+T](items: Seq[T]) {
  private val storage: Vector[T] = items.toVector

  def getAll: Vector[T]                = storage
  def mapTokens(f: T => String): List[String] = storage.map(f).toList

  /** Number of elements in the container. */
  def size: Int                        = storage.size
  def length: Int                      = storage.size
  def isEmpty: Boolean                 = storage.isEmpty
  def nonEmpty: Boolean                = storage.nonEmpty

  /** Element-wise equality with another TokenContainer. */
  override def equals(obj: Any): Boolean = obj match {
    case other: TokenContainer[_] =>
      this.size == other.size &&
        this.storage.zip(other.storage).forall { case (a, b) => a == b }
    case _ => false
  }

  override def hashCode(): Int = storage.hashCode()

  override def toString: String = s"TokenContainer(${storage.mkString(", ")})"
}

/** Contravariant sink - can accept supertypes. */
final class TokenSink[-T] {
  private val received = mutable.ListBuffer.empty[Any]

  def receive(item: T): Unit = { received += item }

  def drain: List[Any] = {
    val out = received.toList
    received.clear()
    out
  }
}

/** Invariant handler - exact type matching required. */
final class BivariantHandler[T](default: T) {
  private var current: T = default

  def get: T                  = current
  def set(value: T): Unit     = { current = value }
  def transform(f: T => T): T = {
    current = f(current)
    current
  }
}

// ============================================================================
// Tokenizer Implementations
// ============================================================================

/** Abstract base tokenizer with a generic input type. */
abstract class BaseTokenizer[T] {
  /** Convert a single value to a token. */
  def tokenize(value: T): Token

  /** Eagerly tokenize every value, returning a List[Token]. */
  def tokenizeBatch(values: Iterable[T]): List[Token] =
    values.iterator.map(tokenize).toList

  /** Lazy variant returning a LazyList of tokens. */
  def tokenizeStream(values: Iterable[T]): LazyList[Token] =
    values.to(LazyList).map(tokenize)
}

// ----------------------------------------------------------------------------
// StringTokenizer
// ----------------------------------------------------------------------------

/** Tokenizer for string and bytes-like input values. */
class StringTokenizer(
    encoding: String = StandardCharsets.UTF_8.name(),
    normalizer: Option[String => String] = None
) extends BaseTokenizer[Any] {

  private val charset: Charset       = Charset.forName(encoding)
  private val norm: String => String = normalizer.getOrElse(identity)

  override def tokenize(value: Any): Token = value match {
    case s: String      => Token(norm(s), TokenType.STRING)
    case b: Array[Byte] => Token(norm(new String(b, charset)), TokenType.STRING)
    case other          =>
      throw new IllegalArgumentException(
        s"StringTokenizer cannot tokenize value of type ${other.getClass.getName}"
      )
  }
}

// ----------------------------------------------------------------------------
// NumericTokenizer
// ----------------------------------------------------------------------------

/**
 * Tokenizer for numeric types (Int, Long, Float, Double, BigDecimal, java.math.BigDecimal).
 *
 * Note: the Python original uses a mutable default for `format_options`. Scala
 * does not have that pitfall; the default is a stable immutable empty map.
 */
class NumericTokenizer(
    precision: Int = 6,
    formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[Any] {

  private val p: Int = math.max(0, precision)

  override def tokenize(value: Any): Token = value match {
    case d: BigDecimal =>
      Token(formatDecimal(d, p), TokenType.NUMERIC, Map("original_type" -> "Decimal"))
    case d: java.math.BigDecimal =>
      Token(formatDecimal(BigDecimal(d.toString), p), TokenType.NUMERIC, Map("original_type" -> "Decimal"))
    case d: Double =>
      Token(formatDecimal(BigDecimal(d), p), TokenType.NUMERIC, Map("original_type" -> "float"))
    case f: Float =>
      Token(formatDecimal(BigDecimal(f.toDouble), p), TokenType.NUMERIC, Map("original_type" -> "float"))
    case n: Short =>
      Token(n.toString, TokenType.NUMERIC, Map("original_type" -> "int"))
    case n: Byte =>
      Token(n.toString, TokenType.NUMERIC, Map("original_type" -> "int"))
    case n: Int =>
      Token(n.toString, TokenType.NUMERIC, Map("original_type" -> "int"))
    case n: Long =>
      Token(n.toString, TokenType.NUMERIC, Map("original_type" -> "int"))
    case n: Number =>
      Token(n.toString, TokenType.NUMERIC, Map("original_type" -> n.getClass.getSimpleName))
    case other =>
      throw new IllegalArgumentException(
        s"NumericTokenizer cannot tokenize value of type ${other.getClass.getName}"
      )
  }

  private def formatDecimal(d: BigDecimal, precision: Int): String = {
    if (precision == 0) d.setScale(0, BigDecimal.RoundingMode.HALF_UP).toString
    else d.setScale(precision, BigDecimal.RoundingMode.HALF_UP).toString
  }
}

// ----------------------------------------------------------------------------
// TemporalTokenizer
// ----------------------------------------------------------------------------

/** Tokenizer for date / date-time types using `java.time`. */
class TemporalTokenizer(formatStr: Option[String] = None) extends BaseTokenizer[Any] {

  import TemporalTokenizer._

  override def tokenize(value: Any): Token = {
    val fmt: DateTimeFormatter = formatStr match {
      case Some(pattern) => DateTimeFormatter.ofPattern(pattern)
      case None          =>
        value match {
          case _: LocalDateTime => ISO_FORMAT
          case _: LocalDate     => DATE_FORMAT
          case _                => ISO_FORMAT
        }
    }

    val rendered: String = value match {
      case dt: LocalDateTime => dt.format(fmt)
      case d: LocalDate      => d.format(fmt)
      case other             =>
        throw new IllegalArgumentException(
          s"TemporalTokenizer cannot tokenize value of type ${other.getClass.getName}"
        )
    }

    Token(rendered, TokenType.TEMPORAL)
  }
}

object TemporalTokenizer {
  val ISO_FORMAT: DateTimeFormatter  = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss")
  val DATE_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")
}

// ----------------------------------------------------------------------------
// UniversalTokenizer (runtime-dispatched multi-type tokenizer)
// ----------------------------------------------------------------------------

/** Tokenizer that dispatches at runtime to the correct specialized tokenizer. */
class UniversalTokenizer {
  private val stringTokenizer   = new StringTokenizer()
  private val numericTokenizer  = new NumericTokenizer()
  private val temporalTokenizer = new TemporalTokenizer()

  /** Convert a value of arbitrary type into a Token. */
  def tokenize(value: Any): Token = value match {
    case null                  => Token("NULL", TokenType.NULL)
    case t: Tokenizable        => Token(t.toToken, TokenType.STRUCTURED)
    case _: String             => stringTokenizer.tokenize(value)
    case _: Array[Byte]        => stringTokenizer.tokenize(value)
    case _: Number             => numericTokenizer.tokenize(value)
    case _: LocalDateTime      => temporalTokenizer.tokenize(value)
    case _: LocalDate          => temporalTokenizer.tokenize(value)
    case other                 =>
      Token(other.toString, TokenType.STRING, Map("fallback" -> true))
  }

  /** Explicit token for a null/missing value. */
  def tokenizeNull: Token = Token("NULL", TokenType.NULL)

  /** Convert an iterable of arbitrary values into tokens. */
  def tokenizeBatch(values: Iterable[Any]): List[Token] =
    values.iterator.map(tokenize).toList

  /** Lazy stream variant. */
  def tokenizeStream(values: Iterable[Any]): LazyList[Token] =
    values.to(LazyList).map(tokenize)
}

// ============================================================================
// TokenRegistry
// ============================================================================

/** Registry of `TokenContainer`s keyed by name, plus a stack of optional producers. */
final class TokenRegistry[T] {
  private val registry = mutable.Map.empty[String, TokenContainer[T]]
  private val handlers = mutable.ListBuffer.empty[T => Option[Token]]

  def register(key: String, container: TokenContainer[T]): Unit = {
    registry(key) = container
  }

  def addHandler(handler: T => Option[Token]): Unit = {
    handlers += handler
  }

  /**
   * Apply handlers in order to every item in the registered container. The
   * first handler that returns Some is used; otherwise None is recorded.
   */
  def process(key: String): List[Option[Token]] = registry.get(key) match {
    case None       => Nil
    case Some(cont) =>
      cont.getAll.iterator.map { item =>
        handlers.iterator.flatMap(_.apply(item)).take(1).toList.headOption
      }.toList
  }
}

// ============================================================================
// TokenFunctor / TokenMonad (HKT stand-ins)
// ============================================================================

/** Minimal functor wrapper - Scala cannot model true HKTs at the value level
 *  but can express the same semantics on a single concrete type. */
class TokenFunctor[T](val value: T) {
  def map[U](f: T => U): TokenFunctor[U] = new TokenFunctor(f(value))
  def flatMap[U](f: T => TokenFunctor[U]): TokenFunctor[U] = f(value)

  /** Retrieve the wrapped value directly. */
  def get: T = value

  def getOrElse[U >: T](default: U): U =
    if (value == null) default else value
}

/** Extended monad operations on top of [[TokenFunctor]]. */
final class TokenMonad[T](value: T) extends TokenFunctor[T](value) {
  /** Applicative apply. */
  def ap[U](fWrapped: TokenMonad[T => U]): TokenMonad[U] =
    TokenMonad.pure(fWrapped.value.apply(value))
}

object TokenMonad {
  def pure[T](value: T): TokenMonad[T] = new TokenMonad(value)
}

// ============================================================================
// JsonValue + JsonTokenizer
// ============================================================================

/** Recursive JSON value type - modelled as a sealed trait hierarchy. */
sealed trait JsonValue

object JsonValue {
  case class JString(s: String)                       extends JsonValue
  case class JNumber(n: BigDecimal)                   extends JsonValue
  case class JBool(b: Boolean)                        extends JsonValue
  case object JNull                                  extends JsonValue
  case class JArray(items: List[JsonValue])           extends JsonValue
  case class JObject(fields: Map[String, JsonValue])  extends JsonValue

  /** Convert an arbitrary Scala/Java value into a [[JsonValue]] tree. */
  def fromAny(a: Any): JsonValue = a match {
    case null              => JNull
    case s: String         => JString(s)
    case b: Boolean        => JBool(b)
    case n: Int            => JNumber(BigDecimal(n))
    case n: Long           => JNumber(BigDecimal(n))
    case n: Short          => JNumber(BigDecimal(n.toInt))
    case n: Byte           => JNumber(BigDecimal(n.toInt))
    case n: Double         => JNumber(BigDecimal(n))
    case n: Float          => JNumber(BigDecimal(n.toDouble))
    case n: BigDecimal     => JNumber(n)
    case m: Map[_, _]      =>
      JObject(m.iterator.map { case (k, v) => k.toString -> fromAny(v) }.toMap)
    case xs: Iterable[_]   => JArray(xs.iterator.map(fromAny).toList)
    case other             => JString(other.toString)
  }

  /** Convert a [[io.circe.Json]] tree into a [[JsonValue]]. */
  def fromCirce(json: CirceJson): JsonValue = json.fold(
    JNull,
    JBool.apply,
    n => JNumber(BigDecimal(n.toString)),
    s => JString(s),
    arr => JArray(arr.toList.map(fromCirce)),
    obj => JObject(obj.toMap.map { case (k, v) => k -> fromCirce(v) })
  )
}

/** Tokenizer for JSON values, including dotted-path extraction. */
class JsonTokenizer(pretty: Boolean = false) {

  /** Convert a [[JsonValue]] to a [[Token]] whose value is a JSON string. */
  def tokenize(value: JsonValue): Token = {
    val rendered = JsonTokenizer.render(value, pretty)
    Token(rendered, TokenType.STRUCTURED, Map("json" -> true))
  }

  /**
   * Convenience overload that accepts a [[io.circe.Json]] value directly,
   * converting it to [[JsonValue]] internally before tokenizing.
   */
  def tokenize(json: CirceJson): Token = tokenize(JsonValue.fromCirce(json))

  /** Tokenize the value found at a dotted path inside the JSON tree. */
  def tokenizePath(value: JsonValue, path: String): Option[Token] = {
    val parts = path.split("\\.").toList
    val resolved: Option[JsonValue] =
      parts.foldLeft(Option[JsonValue](value)) {
        case (Some(JsonValue.JObject(fields)), part) if fields.contains(part) =>
          Some(fields(part))
        case (Some(JsonValue.JArray(items)), part) =>
          Try(part.toInt).toOption.flatMap { idx =>
            if (idx >= 0 && idx < items.size) Some(items(idx)) else None
          }
        case (Some(_), _) => None
        case (None, _)    => None
      }
    resolved.map(tokenize)
  }

  /** Circe-friendly overload that returns Option[Token] for a path inside a [[CirceJson]]. */
  def tokenizePath(json: CirceJson, path: String): Option[Token] =
    tokenizePath(JsonValue.fromCirce(json), path)
}

object JsonTokenizer {

  /** Render a [[JsonValue]] to a JSON-formatted String. */
  def render(value: JsonValue, pretty: Boolean): String = {
    val sb = new StringBuilder
    writeInto(sb, value, pretty, 0)
    sb.toString
  }

  private def writeInto(sb: StringBuilder, v: JsonValue, pretty: Boolean, indent: Int): Unit =
    v match {
      case JsonValue.JString(s) =>
        sb.append('"').append(escape(s)).append('"')
      case JsonValue.JNumber(n) =>
        sb.append(n.toString)
      case JsonValue.JBool(true) =>
        sb.append("true")
      case JsonValue.JBool(false) =>
        sb.append("false")
      case JsonValue.JNull =>
        sb.append("null")
      case JsonValue.JArray(items) =>
        sb.append('[')
        items.zipWithIndex.foreach { case (item, idx) =>
          if (idx > 0) sb.append(',')
          if (pretty) { sb.append('\n'); pad(sb, indent + 1) }
          writeInto(sb, item, pretty, indent + 1)
        }
        if (pretty && items.nonEmpty) { sb.append('\n'); pad(sb, indent) }
        sb.append(']')
      case JsonValue.JObject(fields) =>
        sb.append('{')
        val entries = fields.toList
        entries.zipWithIndex.foreach { case ((k, v), idx) =>
          if (idx > 0) sb.append(',')
          if (pretty) { sb.append('\n'); pad(sb, indent + 1) }
          sb.append('"').append(escape(k)).append("\":")
          if (pretty) sb.append(' ')
          writeInto(sb, v, pretty, indent + 1)
        }
        if (pretty && entries.nonEmpty) { sb.append('\n'); pad(sb, indent) }
        sb.append('}')
    }

  private def pad(sb: StringBuilder, level: Int): Unit = {
    var i = 0
    while (i < level) { sb.append("  "); i += 1 }
  }

  private def escape(s: String): String = {
    val sb = new StringBuilder(s.length + 2)
    s.foreach {
      case '"'  => sb.append("\\\"")
      case '\\' => sb.append("\\\\")
      case '\n' => sb.append("\\n")
      case '\r' => sb.append("\\r")
      case '\t' => sb.append("\\t")
      case c if c < ' ' => sb.append(f"\\u${c.toInt}%04x")
      case c    => sb.append(c)
    }
    sb.toString
  }
}

// ============================================================================
// WhitespaceTokenizer
// ============================================================================

/**
 * Splits text by whitespace and produces a list of [[Token]]s. Optionally
 * lowercases, strips punctuation, truncates by max length, and filters
 * tokens shorter than `minLength`.
 */
class WhitespaceTokenizer(
    lowercase: Boolean = false,
    minLength: Int = 0,
    maxLength: Option[Int] = None,
    stripPunctuation: Boolean = false
) {

  private val punctuation: Set[Char] =
    Set('.', ',', '!', '?', ';', ':', '\'', '"', '(', ')', '[', ']', '{', '}')

  private def processToken(word: String): Option[String] = {
    val cleaned   = if (stripPunctuation) word.filterNot(punctuation.contains) else word
    val normalized = if (lowercase) cleaned.toLowerCase else cleaned

    if (normalized.length < minLength) None
    else if (normalized.isEmpty) None
    else maxLength match {
      case Some(max) if normalized.length > max => Some(normalized.take(max))
      case _                                    => Some(normalized)
    }
  }

  /** Split the input text on whitespace and return one Token per surviving word. */
  def tokenize(text: String): List[Token] =
    text.split("\\s+").iterator.zipWithIndex
      .flatMap { case (word, idx) =>
        processToken(word).map { processed =>
          Token(processed, TokenType.STRING, Map("position" -> idx, "original" -> word))
        }
      }
      .toList

  /** Convenience that returns only the string values of the produced tokens. */
  def tokenizeToStrings(text: String): List[String] = tokenize(text).map(_.value)

  /**
   * Tokenize text and report the (token-string, start-character, end-character)
   * of every surviving token.
   */
  def tokenizeWithPositions(text: String): List[(String, Int, Int)] = {
    var currentPos = 0
    val out        = mutable.ListBuffer.empty[(String, Int, Int)]
    text.split("\\s+").foreach { word =>
      val start = text.indexOf(word, currentPos)
      val end   = if (start < 0) currentPos else start + word.length
      processToken(word).foreach { processed => out += ((processed, start, end)) }
      currentPos = end
    }
    out.toList
  }

  /** The number of tokens the text would produce. */
  def countTokens(text: String): Int = tokenize(text).length
}

// ============================================================================
// TokenizerBuilder (fluent builder)
// ============================================================================

/**
 * Fluent builder for composing a tokenizer function from normalizers,
 * validators, and static metadata.
 */
final class TokenizerBuilder[T] {

  private val normalizers = mutable.ListBuffer.empty[String => String]
  private val validators  = mutable.ListBuffer.empty[T => Boolean]
  private val metadata    = mutable.Map.empty[String, Any]

  def withNormalizer(normalizer: String => String): this.type = {
    normalizers += normalizer
    this
  }

  def withValidator(validator: T => Boolean): this.type = {
    validators += validator
    this
  }

  def withMetadata(entries: (String, Any)*): this.type = {
    metadata ++= entries
    this
  }

  /** Produce the final `T => Token` function from the accumulated configuration. */
  def build(): T => Token = {
    val norms = normalizers.toList
    val vals  = validators.toList
    val meta  = metadata.toMap

    (value: T) => {
      vals.foreach { v =>
        if (!v(value))
          throw new IllegalArgumentException(s"Validation failed for $value")
      }
      val raw        = if (value == null) "null" else value.toString
      val normalized = norms.foldLeft(raw)((acc, n) => n(acc))
      Token(normalized, TokenType.STRING, meta)
    }
  }
}

object TokenizerBuilder {
  /** Construct an empty [[TokenizerBuilder]] for the given type parameter. */
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]()
}

// ============================================================================
// Top-level helper API
// ============================================================================

/** Default shared [[UniversalTokenizer]] plus convenience package-level helpers. */
object TokenizerDefaults {
  val universal: UniversalTokenizer = new UniversalTokenizer()

  /** Tokenize a single value using a shared [[UniversalTokenizer]]. */
  def tokenize(value: Any): Token = universal.tokenize(value)

  /** Tokenize an iterable of values using a shared [[UniversalTokenizer]]. */
  def tokenizeBatch(values: Iterable[Any]): List[Token] =
    values.iterator.map(universal.tokenize).toList

  /** Convenience alias that mirrors [[Tokenizable.toToken]] for any value. */
  def toToken(value: Any): Token = universal.tokenize(value)
}

/**
 * Companion-style facade that exposes the package-level helper functions
 * under both `Tokenizer.tokenize(...)` and `TokenizerDefaults.tokenize(...)`.
 */
object Tokenizer {
  /** Tokenize a single value using a shared [[UniversalTokenizer]]. */
  def tokenize(value: Any): Token = TokenizerDefaults.tokenize(value)

  /** Tokenize an iterable of values using a shared [[UniversalTokenizer]]. */
  def tokenizeBatch(values: Iterable[Any]): List[Token] =
    TokenizerDefaults.tokenizeBatch(values)

  /** Convenience alias that mirrors [[Tokenizable.toToken]] for any value. */
  def toToken(value: Any): Token = TokenizerDefaults.toToken(value)
}

/**
 * Package-level helper functions.  Members defined here are exposed as
 * top-level methods inside the `tokenizer` package (importable via
 * `import tokenizer._`) because Scala 2.13 places package-object members in
 * the package namespace.
 */
package object tokenizer {
  /** Tokenize a single value using a shared [[UniversalTokenizer]]. */
  def tokenize(value: Any): Token = TokenizerDefaults.tokenize(value)

  /** Tokenize an iterable of values using a shared [[UniversalTokenizer]]. */
  def tokenizeBatch(values: Iterable[Any]): List[Token] =
    TokenizerDefaults.tokenizeBatch(values)

  /** Convenience alias that mirrors [[Tokenizable.toToken]] for any value. */
  def toToken(value: Any): Token = TokenizerDefaults.toToken(value)
}
