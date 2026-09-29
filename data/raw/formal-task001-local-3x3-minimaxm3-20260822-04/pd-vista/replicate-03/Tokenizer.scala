package tokenizer

import java.time._
import java.time.format.DateTimeFormatter
import java.util.Date
import scala.collection.mutable

// ============================================================================
// TokenType: type-safe enumeration via sealed trait + case objects
// ============================================================================

sealed trait TokenType {
  def value: String
}

object TokenType {
  case object STRING extends TokenType { val value: String = "string" }
  case object NUMERIC extends TokenType { val value: String = "numeric" }
  case object TEMPORAL extends TokenType { val value: String = "temporal" }
  case object STRUCTURED extends TokenType { val value: String = "structured" }
  case object BINARY extends TokenType { val value: String = "binary" }
  case object NULL extends TokenType { val value: String = "null" }
}

// ============================================================================
// Token: immutable token representation
// ============================================================================

final case class Token(value: String, tokenType: TokenType, metadata: Map[String, Any] = Map.empty) {
  def withMetadata(entries: (String, Any)*): Token =
    copy(metadata = metadata ++ entries)

  def withMetadata(extra: Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)
}

// ============================================================================
// Tokenizable: structural type for objects convertible to token strings
// ============================================================================

trait Tokenizable {
  def toToken: String
}

// ============================================================================
// BaseTokenizer: abstract generic tokenizer with batch support
// ============================================================================

abstract class BaseTokenizer[T] {
  def tokenize(value: T): Token

  def tokenizeBatch(values: Iterable[T]): Iterator[Token] =
    values.iterator.map(tokenize)
}

// ============================================================================
// StringTokenizer: handles strings and byte arrays
// ============================================================================

class StringTokenizer(
    val encoding: String = "UTF-8",
    normalizer: String => String = (s: String) => s
) extends BaseTokenizer[Any] {

  override def tokenize(value: Any): Token = {
    val str = value match {
      case bytes: Array[Byte] => new String(bytes, encoding)
      case s: String => s
      case other => other.toString
    }
    Token(normalizer(str), TokenType.STRING)
  }
}

// ============================================================================
// NumericTokenizer: handles numeric types with configurable precision
// ============================================================================

class NumericTokenizer(
    val precision: Int = 6,
    val formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[Any] {

  private def formatNumber(value: Any): String = value match {
    case bd: BigDecimal =>
      bd.bigDecimal.setScale(precision, java.math.RoundingMode.HALF_UP).toPlainString
    case n: java.lang.Number =>
      new java.math.BigDecimal(n.toString)
        .setScale(precision, java.math.RoundingMode.HALF_UP)
        .toPlainString
    case other => other.toString
  }

  override def tokenize(value: Any): Token =
    Token(
      formatNumber(value),
      TokenType.NUMERIC,
      Map("original_type" -> value.getClass.getSimpleName)
    )
}

// ============================================================================
// TemporalTokenizer: handles date/time types from java.time
// ============================================================================

object TemporalTokenizer {
  val IsoFormat: String = "yyyy-MM-dd'T'HH:mm:ss"
  val DateFormat: String = "yyyy-MM-dd"
}

class TemporalTokenizer(formatStr: Option[String] = None) extends BaseTokenizer[Any] {

  override def tokenize(value: Any): Token = {
    val fmt = formatStr.getOrElse {
      value match {
        case _: LocalDateTime | _: ZonedDateTime | _: OffsetDateTime | _: Instant =>
          TemporalTokenizer.IsoFormat
        case _: LocalDate =>
          TemporalTokenizer.DateFormat
        case _ =>
          TemporalTokenizer.IsoFormat
      }
    }
    val dtf = DateTimeFormatter.ofPattern(fmt)
    val str = value match {
      case d: LocalDateTime => d.format(dtf)
      case d: ZonedDateTime => d.format(dtf)
      case d: OffsetDateTime => d.format(dtf)
      case d: Instant => d.atZone(ZoneOffset.UTC).format(dtf)
      case d: LocalDate => d.format(dtf)
      case d: Date => d.toInstant.atZone(ZoneOffset.UTC).format(dtf)
      case other => other.toString
    }
    Token(str, TokenType.TEMPORAL)
  }
}

// ============================================================================
// UniversalTokenizer: dispatches to specialised tokenizers via runtime type
// ============================================================================

class UniversalTokenizer {
  private val stringTokenizer: StringTokenizer = new StringTokenizer()
  private val numericTokenizer: NumericTokenizer = new NumericTokenizer()
  private val temporalTokenizer: TemporalTokenizer = new TemporalTokenizer()

  def tokenizeNull: Token = Token("NULL", TokenType.NULL)

  def tokenize(value: Any): Token = value match {
    case null => tokenizeNull
    case t: Tokenizable => Token(t.toToken, TokenType.STRUCTURED)
    case b: Array[Byte] => stringTokenizer.tokenize(b)
    case s: String => stringTokenizer.tokenize(s)
    case i: Int => numericTokenizer.tokenize(i)
    case l: Long => numericTokenizer.tokenize(l)
    case d: Double => numericTokenizer.tokenize(d)
    case f: Float => numericTokenizer.tokenize(f)
    case bd: BigDecimal => numericTokenizer.tokenize(bd)
    case dt: LocalDateTime => temporalTokenizer.tokenize(dt)
    case d: LocalDate => temporalTokenizer.tokenize(d)
    case zdt: ZonedDateTime => temporalTokenizer.tokenize(zdt)
    case inst: Instant => temporalTokenizer.tokenize(inst)
    case other => Token(other.toString, TokenType.STRING, Map("fallback" -> true))
  }
}

// ============================================================================
// WhitespaceTokenizer: splits text by whitespace with optional post-processing
// ============================================================================

class WhitespaceTokenizer(
    val lowercase: Boolean = false,
    val minLength: Int = 0,
    val maxLength: Option[Int] = None,
    val stripPunctuation: Boolean = false
) {

  private val punctuation: Set[Char] = ".,!?;:'\"()[]{}".toSet

  private def processToken(word: String): Option[String] = {
    val stripped = if (stripPunctuation) word.filterNot(punctuation.contains) else word
    val cased = if (lowercase) stripped.toLowerCase else stripped
    if (cased.length < minLength) None
    else {
      val truncated = maxLength match {
        case Some(max) if cased.length > max => cased.take(max)
        case _ => cased
      }
      Option(truncated).filter(_.nonEmpty)
    }
  }

  def tokenize(text: String): List[Token] =
    text.split("\\s+").toIndexedSeq.zipWithIndex.flatMap { case (word, i) =>
      processToken(word).map(t =>
        Token(t, TokenType.STRING, Map("position" -> i, "original" -> word))
      )
    }.toList

  def tokenizeToStrings(text: String): List[String] = tokenize(text).map(_.value)

  def tokenizeWithPositions(text: String): List[(String, Int, Int)] = {
    val result = mutable.ListBuffer.empty[(String, Int, Int)]
    var currentPos = 0
    for (word <- text.split("\\s+")) {
      val start = text.indexOf(word, currentPos)
      val end = if (start >= 0) start + word.length else currentPos
      processToken(word).foreach(p => result += ((p, start, end)))
      currentPos = end
    }
    result.toList
  }

  def countTokens(text: String): Int = tokenize(text).size
}

// ============================================================================
// TokenizerBuilder: fluent builder composing validators, normalizers, metadata
// ============================================================================

class TokenizerBuilder[T] {
  private var normalizers: List[String => String] = List.empty
  private var validators: List[T => Boolean] = List.empty
  private var metadata: Map[String, Any] = Map.empty

  def withNormalizer(n: String => String): this.type = {
    normalizers = normalizers :+ n
    this
  }

  def withValidator(v: T => Boolean): this.type = {
    validators = validators :+ v
    this
  }

  def withMetadata(entries: (String, Any)*): this.type = {
    metadata = metadata ++ entries
    this
  }

  def withMetadata(extra: Map[String, Any]): this.type = {
    metadata = metadata ++ extra
    this
  }

  def build(): T => Token = {
    val norms = normalizers
    val vals = validators
    val meta = metadata

    (value: T) => {
      for (v <- vals if !v(value)) {
        throw new IllegalArgumentException(s"Validation failed for $value")
      }
      var str = value.toString
      for (n <- norms) str = n(str)
      Token(str, TokenType.STRING, meta)
    }
  }
}

object TokenizerBuilder {
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]()
}

// ============================================================================
// MutableTokenBatch: mutable batch that locks after first processing
// ============================================================================

final class MutableTokenBatch {
  val tokens: mutable.ListBuffer[Token] = mutable.ListBuffer.empty
  private var _processed: Boolean = false

  def add(token: Token): Unit = {
    if (_processed) throw new RuntimeException("Batch already processed")
    tokens += token
  }

  def markProcessed(): Unit = { _processed = true }
  def processed: Boolean = _processed
}

// ============================================================================
// TokenContainer[+T]: covariant container of typed items
// ============================================================================

class TokenContainer[+T](val items: Seq[T]) {
  def getAll: Seq[T] = items
  def size: Int = items.size
  def mapTokens(func: T => String): List[String] = items.map(func).toList
}

// ============================================================================
// TokenSink[-T]: contravariant sink accepting supertypes
// ============================================================================

class TokenSink[-T] {
  private val _received: mutable.ListBuffer[Any] = mutable.ListBuffer.empty

  def receive(item: T): Unit = { _received += item }

  def drain(): List[Any] = {
    val result = _received.toList
    _received.clear()
    result
  }
}

// ============================================================================
// BivariantHandler[T]: invariant wrapper around a single value
// ============================================================================

class BivariantHandler[T](default: T) {
  private var _value: T = default

  def get(): T = _value
  def set(value: T): Unit = { _value = value }

  def transform(func: T => T): T = {
    _value = func(_value)
    _value
  }
}

// ============================================================================
// TokenRegistry[T]: keyed registry with composable handlers
// ============================================================================

class TokenRegistry[T] {
  private val _registry: mutable.Map[String, TokenContainer[T]] = mutable.Map.empty
  private val _handlers: mutable.ListBuffer[T => Option[Token]] = mutable.ListBuffer.empty

  def register(key: String, container: TokenContainer[T]): Unit = {
    _registry(key) = container
  }

  def addHandler(handler: T => Option[Token]): Unit = {
    _handlers += handler
  }

  def process(key: String): List[Option[Token]] =
    _registry.get(key) match {
      case None => List.empty
      case Some(container) =>
        container.getAll.map { item =>
          _handlers.find(h => h(item).isDefined).flatMap(h => h(item))
        }.toList
    }
}

// ============================================================================
// TokenFunctor[T] / TokenMonad[T]: functor and monad simulations
// ============================================================================

class TokenFunctor[T](initial: T) {
  protected var _value: T = initial

  def get: T = _value
  def map[U](func: T => U): TokenFunctor[U] = new TokenFunctor(func(_value))
  def flatMap[U](func: T => TokenFunctor[U]): TokenFunctor[U] = func(_value)
  def getOrElse(default: T): T = if (_value == null) default else _value
}

object TokenMonad {
  def pure[T](value: T): TokenMonad[T] = new TokenMonad[T](value)
}

class TokenMonad[T](value: T) extends TokenFunctor[T](value) {
  def ap[U](funcWrapped: TokenMonad[T => U]): TokenMonad[U] = {
    val f = funcWrapped._value
    new TokenMonad[U](f(this._value))
  }
}

// ============================================================================
// JsonTokenizer: serialises JSON-like values to structured tokens
// ============================================================================

class JsonTokenizer(val pretty: Boolean = false) {

  def tokenize(value: Any): Token =
    Token(renderJson(value, 0), TokenType.STRUCTURED, Map("json" -> true))

  def tokenizePath(value: Any, path: String): Option[Token] = {
    val parts = path.split("\\.")
    var current: Any = value
    val it = parts.iterator
    while (it.hasNext) {
      val part = it.next()
      current match {
        case m: Map[_, _] =>
          current = m.asInstanceOf[Map[String, Any]].getOrElse(part, return None)
        case l: Seq[_] =>
          try {
            val idx = part.toInt
            if (idx >= 0 && idx < l.length) current = l(idx)
            else return None
          } catch {
            case _: NumberFormatException => return None
          }
        case _ => return None
      }
    }
    Some(tokenize(current))
  }

  private def renderJson(value: Any, depth: Int): String = {
    val indent = "  " * depth
    val itemIndent = "  " * (depth + 1)
    value match {
      case null => "null"
      case s: String => "\"" + escapeJson(s) + "\""
      case b: Boolean => b.toString
      case n: Int => n.toString
      case n: Long => n.toString
      case n: Double => n.toString
      case n: Float => n.toString
      case n: Short => n.toString
      case n: Byte => n.toString
      case bd: BigDecimal => bd.toString
      case m: Map[_, _] =>
        val pairs = m.asInstanceOf[Map[String, Any]].toSeq.map { case (k, v) =>
          val keyStr = "\"" + escapeJson(k.toString) + "\""
          val valStr = renderJson(v, depth + 1)
          if (pretty) s"$itemIndent$keyStr: $valStr" else s"$keyStr:$valStr"
        }
        if (pretty) "{\n" + pairs.mkString(",\n") + "\n" + indent + "}"
        else "{" + pairs.mkString(",") + "}"
      case l: Seq[_] =>
        val items = l.map(renderJson(_, depth + 1))
        if (pretty) "[\n" + items.map(itemIndent + _).mkString(",\n") + "\n" + indent + "]"
        else "[" + items.mkString(",") + "]"
      case other => "\"" + escapeJson(other.toString) + "\""
    }
  }

  private def escapeJson(s: String): String = {
    val sb = new StringBuilder(s.length + 2)
    var i = 0
    while (i < s.length) {
      s.charAt(i) match {
        case '\\' => sb.append("\\\\")
        case '"' => sb.append("\\\"")
        case '\n' => sb.append("\\n")
        case '\r' => sb.append("\\r")
        case '\t' => sb.append("\\t")
        case c => sb.append(c)
      }
      i += 1
    }
    sb.toString
  }
}

// ============================================================================
// Top-level functions: tokenize, tokenizeBatch, toToken, withMetadata
// ============================================================================

object Tokenizer {

  def tokenize[T](value: T): Token =
    new UniversalTokenizer().tokenize(value)

  def tokenizeBatch[T](values: Iterable[T]): Iterator[Token] = {
    val ut = new UniversalTokenizer()
    values.iterator.map(ut.tokenize)
  }

  def toToken(value: Any): String = value match {
    case null => "NULL"
    case t: Tokenizable => t.toToken
    case other => other.toString
  }

  def withMetadata(token: Token, kv: (String, Any)*): Token =
    token.copy(metadata = token.metadata ++ kv)
}