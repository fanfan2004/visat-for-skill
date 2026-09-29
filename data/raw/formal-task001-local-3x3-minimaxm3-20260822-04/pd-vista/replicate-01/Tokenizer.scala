package tokenizer

import java.time.{LocalDate, LocalDateTime}
import java.util.Date

import io.circe.Json

/**
 * Scala 2.13 translation of the Python Tokenizer module.
 *
 * Provides type classes and implementations for converting heterogeneous
 * inputs into standardised [[Token]] values.
 */
sealed abstract class TokenType(val value: String)

object TokenType {
  case object STRING    extends TokenType("string")
  case object NUMERIC   extends TokenType("numeric")
  case object TEMPORAL  extends TokenType("temporal")
  case object STRUCTURED extends TokenType("structured")
  case object BINARY    extends TokenType("binary")
  case object NULL      extends TokenType("null")

  val values: Seq[TokenType] = Seq(STRING, NUMERIC, TEMPORAL, STRUCTURED, BINARY, NULL)

  def fromValue(v: String): Option[TokenType] = values.find(_.value == v)
}

/**
 * Anything that knows how to render itself as a token string. Mirrors the
 * Python `Tokenizable` protocol.
 */
trait Tokenizable {
  def toToken: String
}

/**
 * Immutable token carrying the rendered value, its [[TokenType]] and an
 * arbitrary metadata map.
 */
final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: Map[String, Any] = Map.empty
) extends Tokenizable {

  /** Return a new [[Token]] with additional metadata merged in. */
  def withMetadata(extra: (String, Any)*): Token =
    copy(metadata = metadata ++ extra)

  /** Convenience: merge an entire map. */
  def withMetadata(extra: Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)

  override def toToken: String = value
}

/**
 * Mutable batch of tokens.
 */
final class MutableTokenBatch {
  private var _tokens: List[Token] = List.empty
  private var _processed: Boolean   = false

  def tokens: List[Token] = _tokens
  def processed: Boolean  = _processed

  def add(token: Token): Unit = {
    if (_processed) throw new RuntimeException("Batch already processed")
    _tokens = _tokens :+ token
  }

  def markProcessed(): Unit = {
    _processed = true
  }
}

/**
 * Covariant container for tokens.
 */
final class TokenContainer[T](items: Seq[T]) {
  private val _items: Seq[T] = items.toVector

  def getAll: Seq[T] = _items

  /** Number of stored items. */
  def size: Int = _items.size

  def mapTokens(func: T => String): List[String] =
    _items.iterator.map(func).toList
}

/**
 * Contravariant sink that buffers received items.
 */
final class TokenSink[T] {
  private var buffer: List[T] = List.empty

  def receive(item: T): Unit = buffer = buffer :+ item

  def drain(): List[T] = {
    val out = buffer
    buffer = List.empty
    out
  }
}

/**
 * Invariant generic handler.
 */
final class BivariantHandler[T](default: T) {
  private var value: T = default

  def get: T                = value
  def set(v: T): Unit       = { value = v }
  def transform(f: T => T): T = {
    value = f(value)
    value
  }
}

/**
 * Generic abstract base tokenizer.
 */
abstract class BaseTokenizer[T] {

  /** Convert a single value of type `T` into a [[Token]]. */
  def tokenize(value: T): Token

  /** Lazily tokenize an iterable of inputs. */
  def tokenizeBatch(values: Iterable[T]): LazyList[Token] =
    values.iterator.map(tokenize).to(LazyList)
}

/**
 * Tokenizes strings or byte arrays.
 */
final class StringTokenizer(
    val encoding: String = "utf-8",
    normalizer: String => String = identity
) extends BaseTokenizer[Either[Array[Byte], String]] {

  def this(encoding: String) = this(encoding, identity)

  override def tokenize(value: Either[Array[Byte], String]): Token = {
    val strValue = value match {
      case Right(s) => s
      case Left(b)  => new String(b, encoding)
    }
    Token(normalizer(strValue), TokenType.STRING)
  }
}

object StringTokenizer {
  def apply(encoding: String, normalizer: String => String): StringTokenizer =
    new StringTokenizer(encoding, normalizer)

  def apply(encoding: String): StringTokenizer =
    new StringTokenizer(encoding, identity)

  def apply(): StringTokenizer =
    new StringTokenizer("utf-8", identity)

  def tokenize(value: String): Token =
    apply().tokenize(Right(value))

  def tokenize(value: Array[Byte]): Token =
    apply().tokenize(Left(value))
}

/**
 * Tokenizes numeric values (Int, Long, Double, Float, BigDecimal).
 */
final class NumericTokenizer(
    val precision: Int = 6,
    val formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[BigDecimal] {

  require(precision >= 0, s"precision must be non-negative, got $precision")

  override def tokenize(value: BigDecimal): Token = {
    val scaled = value.setScale(precision, BigDecimal.RoundingMode.HALF_UP)
    Token(
      value = scaled.toString,
      tokenType = TokenType.NUMERIC,
      metadata = Map("original_type" -> originalTypeName(value))
    )
  }

  private def originalTypeName(value: BigDecimal): String = value match {
    case _ if value.isValidInt  => "int"
    case _ if value.isValidLong => "long"
    case _                      => "float"
  }
}

object NumericTokenizer {
  def apply(): NumericTokenizer = new NumericTokenizer()

  def apply(precision: Int): NumericTokenizer = new NumericTokenizer(precision)

  def apply(precision: Int, formatOptions: Map[String, Any]): NumericTokenizer =
    new NumericTokenizer(precision, formatOptions)

  def tokenize(value: Int): Token       = apply().tokenize(BigDecimal(value))
  def tokenize(value: Long): Token      = apply().tokenize(BigDecimal(value))
  def tokenize(value: Double): Token    = apply().tokenize(BigDecimal(value))
  def tokenize(value: BigDecimal): Token = apply().tokenize(value)
}

/**
 * Tokenizes temporal values.
 */
sealed trait TemporalInput
object TemporalInput {
  final case class DateTime(value: LocalDateTime) extends TemporalInput
  final case class DateOnly(value: LocalDate)     extends TemporalInput
  final case class Instant(value: Date)           extends TemporalInput
}

final class TemporalTokenizer(val format: Option[String] = None)
    extends BaseTokenizer[TemporalInput] {

  def this(formatStr: String) = this(Some(formatStr))

  val IsoFormat: String  = "yyyy-MM-dd'T'HH:mm:ss"
  val DateFormat: String = "yyyy-MM-dd"

  private val formatterCache =
    scala.collection.mutable.Map.empty[String, java.time.format.DateTimeFormatter]

  private def formatter(fmt: String): java.time.format.DateTimeFormatter =
    formatterCache.getOrElseUpdate(
      fmt,
      java.time.format.DateTimeFormatter.ofPattern(fmt)
    )

  override def tokenize(value: TemporalInput): Token = value match {
    case TemporalInput.DateTime(dt) =>
      val fmt = format.getOrElse(IsoFormat)
      Token(dt.format(formatter(fmt)), TokenType.TEMPORAL)

    case TemporalInput.DateOnly(d) =>
      val fmt = format.getOrElse(DateFormat)
      Token(d.format(formatter(fmt)), TokenType.TEMPORAL)

    case TemporalInput.Instant(d) =>
      val fmt = format.getOrElse(IsoFormat)
      Token(
        d.toInstant.atZone(java.time.ZoneOffset.UTC).toLocalDateTime.format(formatter(fmt)),
        TokenType.TEMPORAL
      )
  }
}

object TemporalTokenizer {
  def apply(): TemporalTokenizer = new TemporalTokenizer(None)
  def apply(format: String): TemporalTokenizer = new TemporalTokenizer(Some(format))

  def tokenize(value: LocalDateTime): Token = apply().tokenize(TemporalInput.DateTime(value))
  def tokenize(value: LocalDate): Token     = apply().tokenize(TemporalInput.DateOnly(value))
  def tokenize(value: Date): Token         = apply().tokenize(TemporalInput.Instant(value))
}

/**
 * Dispatches tokenization based on the runtime type of the input.
 */
final class UniversalTokenizer {
  private val stringTokenizer   = new StringTokenizer()
  private val numericTokenizer  = new NumericTokenizer()
  private val temporalTokenizer = new TemporalTokenizer()

  /** Dispatch entry point. */
  def tokenize(value: Any): Token = value match {
    case null =>
      Token("NULL", TokenType.NULL)

    case t: Tokenizable =>
      Token(t.toToken, TokenType.STRUCTURED)

    case bytes: Array[Byte] =>
      stringTokenizer.tokenize(Left(bytes))

    case s: String =>
      stringTokenizer.tokenize(Right(s))

    case n: BigDecimal =>
      numericTokenizer.tokenize(n)

    case i: Int =>
      numericTokenizer.tokenize(BigDecimal(i))

    case l: Long =>
      numericTokenizer.tokenize(BigDecimal(l))

    case d: Double =>
      numericTokenizer.tokenize(BigDecimal(d))

    case f: Float =>
      numericTokenizer.tokenize(BigDecimal(f.toDouble))

    case dt: LocalDateTime =>
      temporalTokenizer.tokenize(TemporalInput.DateTime(dt))

    case ld: LocalDate =>
      temporalTokenizer.tokenize(TemporalInput.DateOnly(ld))

    case date: Date =>
      temporalTokenizer.tokenize(TemporalInput.Instant(date))

    case other =>
      Token(String.valueOf(other), TokenType.STRING, Map("fallback" -> true))
  }

  /** Typed convenience overloads. */
  def tokenize(value: String): Token        = tokenize(value: Any)
  def tokenize(value: Array[Byte]): Token   = tokenize(value: Any)
  def tokenize(value: Int): Token           = tokenize(value: Any)
  def tokenize(value: Long): Token          = tokenize(value: Any)
  def tokenize(value: Double): Token        = tokenize(value: Any)
  def tokenize(value: LocalDateTime): Token = tokenize(value: Any)
  def tokenize(value: LocalDate): Token     = tokenize(value: Any)

  /** Tokenize a circe `Json` value by delegating to the [[JsonTokenizer]]. */
  def tokenize(value: Json): Token =
    new JsonTokenizer().tokenize(CirceInterop.fromCirce(value))

  /** Explicitly tokenize `null` values. */
  def tokenizeNull: Token = Token("NULL", TokenType.NULL)
}

object UniversalTokenizer {
  def apply(): UniversalTokenizer = new UniversalTokenizer()
}

/**
 * Registry with handlers for tokens.
 */
final class TokenRegistry[T] {
  private val _registry: scala.collection.mutable.Map[String, TokenContainer[T]] =
    scala.collection.mutable.Map.empty
  private val _handlers: scala.collection.mutable.ListBuffer[T => Option[Token]] =
    scala.collection.mutable.ListBuffer.empty

  def register(key: String, container: TokenContainer[T]): Unit =
    _registry.update(key, container)

  def addHandler(handler: T => Option[Token]): Unit =
    _handlers += handler

  def process(key: String): List[Option[Token]] = {
    val container = _registry.get(key)
    if (container.isEmpty) return List.empty

    val items = container.get.getAll
    items.iterator.map { item =>
      val it = _handlers.iterator
      var found: Option[Token] = None
      while (it.hasNext && found.isEmpty) {
        found = it.next().apply(item)
      }
      found
    }.toList
  }
}

/**
 * Functor for token values.
 */
class TokenFunctor[T](initial: T) {
  protected var _value: T = initial

  def map[B](func: T => B): TokenFunctor[B] =
    new TokenFunctor(func(_value))

  def flatMap[B](func: T => TokenFunctor[B]): TokenFunctor[B] =
    func(_value)

  def getOrElse(default: T): T =
    if (_value == null) default else _value

  /** Return the wrapped value (alias of [[value]]). */
  def get: T = _value

  def value: T = _value
}

object TokenFunctor {
  def apply[T](value: T): TokenFunctor[T] = new TokenFunctor(value)
}

/**
 * Monad operations extending [[TokenFunctor]].
 */
final class TokenMonad[T](value: T) extends TokenFunctor[T](value) {
  def pure(value: T): TokenMonad[T] = new TokenMonad(value)

  def ap[B](funcWrapped: TokenMonad[T => B]): TokenMonad[B] =
    new TokenMonad(funcWrapped.value(_value))
}

object TokenMonad {
  def pure[T](value: T): TokenMonad[T] = new TokenMonad(value)
}

/**
 * Recursive JSON value type used by [[JsonTokenizer]].
 */
sealed trait JsonValue
object JsonValue {
  final case class JsString(value: String)                          extends JsonValue
  final case class JsNumber(value: BigDecimal)                      extends JsonValue
  final case class JsBoolean(value: Boolean)                        extends JsonValue
  final case class JsNull()                                         extends JsonValue
  final case class JsArray(value: List[JsonValue])                  extends JsonValue
  final case class JsObject(value: Map[String, JsonValue])          extends JsonValue
}

/**
 * Tokenizer for JSON structures.
 */
final class JsonTokenizer(val pretty: Boolean = false) {

  def this(pretty: java.lang.Boolean) = this(pretty.booleanValue())

  def tokenize(value: JsonValue): Token = {
    val jsonStr = JsonCodec.encode(value, pretty)
    Token(jsonStr, TokenType.STRUCTURED, Map("json" -> true))
  }

  /** Tokenize a circe `Json` value, converting it to [[JsonValue]] first. */
  def tokenize(value: Json): Token =
    tokenize(CirceInterop.fromCirce(value))

  def tokenizePath(value: JsonValue, path: String): Option[Token] = {
    val parts = path.split('.').toList
    JsonCodec.navigate(value, parts).map(tokenize)
  }
}

object JsonTokenizer {
  def apply(): JsonTokenizer = new JsonTokenizer(false)
  def apply(pretty: Boolean): JsonTokenizer = new JsonTokenizer(pretty)
}

/**
 * Helper that converts any [[JsonValue]] to its JSON string representation.
 */
private object JsonCodec {
  def encode(value: JsonValue, pretty: Boolean): String = {
    val indent = if (pretty) 2 else 0
    val sb     = new StringBuilder
    render(value, sb, indent, 0)
    sb.toString
  }

  private def render(value: JsonValue, sb: StringBuilder, indent: Int, depth: Int): Unit = value match {
    case JsonValue.JsString(s) =>
      sb.append('"').append(escape(s)).append('"')
    case JsonValue.JsNumber(n) =>
      sb.append(n.toString)
    case JsonValue.JsBoolean(b) =>
      sb.append(b.toString)
    case JsonValue.JsNull() =>
      sb.append("null")
    case JsonValue.JsArray(items) =>
      renderArray(items, sb, indent, depth)
    case JsonValue.JsObject(fields) =>
      renderObject(fields, sb, indent, depth)
  }

  private def renderArray(items: List[JsonValue], sb: StringBuilder, indent: Int, depth: Int): Unit = {
    if (items.isEmpty) sb.append("[]")
    else {
      sb.append('[')
      if (indent > 0) sb.append('\n')
      val it = items.iterator
      var first = true
      while (it.hasNext) {
        if (!first) {
          sb.append(',')
          if (indent > 0) sb.append('\n')
        }
        first = false
        if (indent > 0) pad(sb, indent * (depth + 1))
        render(it.next(), sb, indent, depth + 1)
      }
      if (indent > 0) {
        sb.append('\n')
        pad(sb, indent * depth)
      }
      sb.append(']')
    }
  }

  private def renderObject(
      fields: Map[String, JsonValue],
      sb: StringBuilder,
      indent: Int,
      depth: Int
  ): Unit = {
    if (fields.isEmpty) sb.append("{}")
    else {
      sb.append('{')
      if (indent > 0) sb.append('\n')
      val it = fields.iterator
      var first = true
      while (it.hasNext) {
        val (k, v) = it.next()
        if (!first) {
          sb.append(',')
          if (indent > 0) sb.append('\n')
        }
        first = false
        if (indent > 0) pad(sb, indent * (depth + 1))
        sb.append('"').append(escape(k)).append('"').append(':')
        if (indent > 0) sb.append(' ')
        render(v, sb, indent, depth + 1)
      }
      if (indent > 0) {
        sb.append('\n')
        pad(sb, indent * depth)
      }
      sb.append('}')
    }
  }

  private def pad(sb: StringBuilder, count: Int): Unit = {
    var i = 0
    while (i < count) { sb.append(' '); i += 1 }
  }

  private def escape(s: String): String = {
    val sb = new StringBuilder(s.length + 2)
    var i  = 0
    while (i < s.length) {
      s.charAt(i) match {
        case '"'  => sb.append("\\\"")
        case '\\' => sb.append("\\\\")
        case '\n' => sb.append("\\n")
        case '\r' => sb.append("\\r")
        case '\t' => sb.append("\\t")
        case c    => sb.append(c)
      }
      i += 1
    }
    sb.toString
  }

  /** Walk a JSON value along dot-separated path parts. */
  def navigate(value: JsonValue, parts: List[String]): Option[JsonValue] = parts match {
    case Nil => Some(value)
    case head :: rest =>
      value match {
        case JsonValue.JsObject(fields) =>
          fields.get(head).flatMap(child => navigate(child, rest))
        case JsonValue.JsArray(items) =>
          val idxOpt =
            if (head.forall(_.isDigit)) Some(head.toInt)
            else None
          idxOpt.flatMap { idx =>
            if (idx >= 0 && idx < items.length) navigate(items(idx), rest)
            else None
          }
        case _ => None
      }
  }
}

/**
 * Converts an [[io.circe.Json]] value into our internal [[JsonValue]] ADT.
 */
private object CirceInterop {
  def fromCirce(json: Json): JsonValue = json.fold(
    JsonValue.JsNull(),
    bool    => JsonValue.JsBoolean(bool),
    number  => JsonValue.JsNumber(BigDecimal(number.toString)),
    str     => JsonValue.JsString(str),
    arr     => JsonValue.JsArray(arr.toList.map(fromCirce)),
    obj     => JsonValue.JsObject(obj.toMap.view.mapValues(fromCirce).toMap)
  )
}

/**
 * Splits an input string by whitespace and applies optional filtering / normalisation.
 */
final class WhitespaceTokenizer(
    val lowercase: Boolean = false,
    val minLength: Int = 0,
    val maxLength: Option[Int] = None,
    val stripPunctuation: Boolean = false
) {

  require(minLength >= 0, s"minLength must be non-negative, got $minLength")
  maxLength.foreach(n => require(n >= 0, s"maxLength must be non-negative, got $n"))

  private val punctuation: Set[Char] =
    Set('.', ',', '!', '?', ';', ':', '\'', '"', '(', ')', '[', ']', '{', '}')

  /** Process a single word into a token string, or `None` if filtered. */
  private[tokenizer] def processToken(word: String): Option[String] = {
    val stripped =
      if (stripPunctuation) word.filterNot(punctuation.contains)
      else word

    val lowered =
      if (lowercase) stripped.toLowerCase
      else stripped

    if (lowered.length < minLength) None
    else {
      val bounded = maxLength match {
        case Some(n) if lowered.length > n => lowered.take(n)
        case _                             => lowered
      }
      Option(bounded).filter(_.nonEmpty)
    }
  }

  /** Split text by whitespace, returning a list of [[Token]] instances. */
  def tokenize(text: String): List[Token] = {
    val words = text.split("\\s+").toList.filter(_.nonEmpty)
    words.iterator.zipWithIndex.collect {
      case (word, idx) if processToken(word).isDefined =>
        val processed = processToken(word).get
        Token(
          value = processed,
          tokenType = TokenType.STRING,
          metadata = Map("position" -> idx, "original" -> word)
        )
    }.toList
  }

  /** Convenience: only return the string values. */
  def tokenizeToStrings(text: String): List[String] =
    tokenize(text).map(_.value)

  /** Return tokens with their character positions inside `text`. */
  def tokenizeWithPositions(text: String): List[(String, Int, Int)] = {
    val words = text.split("\\s+").toList.filter(_.nonEmpty)
    val builder = List.newBuilder[(String, Int, Int)]
    var cursor  = 0
    words.foreach { word =>
      val start = text.indexOf(word, cursor)
      if (start >= 0) {
        val end = start + word.length
        processToken(word).foreach(p => builder += ((p, start, end)))
        cursor = end
      } else {
        cursor += word.length
      }
    }
    builder.result()
  }

  /** Number of tokens after filtering. */
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

  def tokenize(text: String): List[Token] = apply().tokenize(text)
}

/**
 * Fluent builder used to assemble a tokenizer function.
 */
final class TokenizerBuilder[T] {

  private var normalizers: List[String => String] = List.empty
  private var validators: List[T => Boolean]      = List.empty
  private var sharedMetadata: Map[String, Any]    = Map.empty

  /** Register a string-to-string normalizer that runs after conversion. */
  def withNormalizer(f: String => String): this.type = {
    normalizers = normalizers :+ f
    this
  }

  /** Register a validator that must accept the input before tokenization. */
  def withValidator(f: T => Boolean): this.type = {
    validators = validators :+ f
    this
  }

  /** Attach metadata to every produced token. */
  def withMetadata(entries: (String, Any)*): this.type = {
    sharedMetadata = sharedMetadata ++ entries
    this
  }

  /** Alternative map-based metadata setter. */
  def withMetadata(entries: Map[String, Any]): this.type = {
    sharedMetadata = sharedMetadata ++ entries
    this
  }

  /** Build the tokenizer function. */
  def build(): T => Token = {
    val normalizersSnap = normalizers
    val validatorsSnap  = validators
    val metadataSnap    = sharedMetadata

    (value: T) => {
      if (!validatorsSnap.forall(_(value))) {
        throw new IllegalArgumentException(s"Validation failed for $value")
      }
      val raw       = String.valueOf(value)
      val normalized = normalizersSnap.foldLeft(raw)((acc, f) => f(acc))
      Token(normalized, TokenType.STRING, metadataSnap)
    }
  }
}

object TokenizerBuilder {
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]()
}

/**
 * Free functions mirroring the Python module-level helpers. Hosted in an
 * object because Scala 2.13 does not allow top-level `def`s.
 */
object Tokenizer {

  /** Dispatch tokenization via a fresh [[UniversalTokenizer]]. */
  def tokenize(value: Any): Token = new UniversalTokenizer().tokenize(value)

  /** Batch dispatcher. */
  def tokenizeBatch(values: Iterable[Any]): LazyList[Token] =
    LazyList.from(values.iterator).map(new UniversalTokenizer().tokenize)

  /** Render any value via the [[Tokenizable]] protocol, falling back to `toString`. */
  def toToken(value: Any): String = value match {
    case null           => "NULL"
    case t: Tokenizable => t.toToken
    case other          => String.valueOf(other)
  }
}
