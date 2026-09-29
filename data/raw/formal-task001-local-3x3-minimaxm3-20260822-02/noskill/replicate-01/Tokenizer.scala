package tokenizer

import java.time.{LocalDate, LocalDateTime}
import java.time.format.DateTimeFormatter

import scala.collection.mutable
import scala.util.Try

import io.circe.Json

// ============================================================================
// TokenType: sealed ADT representing the kind of value a Token carries
// ============================================================================

sealed trait TokenType {
  def value: String
}

object TokenType {
  case object STRING extends TokenType { override val value: String = "string"     }
  case object NUMERIC extends TokenType { override val value: String = "numeric"   }
  case object TEMPORAL extends TokenType { override val value: String = "temporal"  }
  case object STRUCTURED extends TokenType { override val value: String = "structured" }
  case object BINARY extends TokenType { override val value: String = "binary"     }
  case object NULL extends TokenType { override val value: String = "null"        }
}

// ============================================================================
// Core Token data type and mutable batch helper
// ============================================================================

final case class Token(
    value: String,
    tokenType: TokenType,
    metadata: Map[String, Any] = Map.empty
) {
  def withMetadata(extra: Map[String, Any]): Token =
    copy(metadata = metadata ++ extra)

  def withMetadata(pairs: (String, Any)*): Token =
    copy(metadata = metadata ++ pairs)
}

final class MutableTokenBatch {
  private val buffer: mutable.ListBuffer[Token] = mutable.ListBuffer.empty
  private var processedFlag: Boolean = false

  def add(token: Token): Unit =
    if (processedFlag) throw new IllegalStateException("Batch already processed")
    else buffer += token

  def markProcessed(): Unit = { processedFlag = true }

  def tokens: List[Token] = buffer.toList

  def processed: Boolean = processedFlag
}

// ============================================================================
// Structural type-classes (Protocols in Python)
// ============================================================================

trait Tokenizable {
  def toToken: String
}

trait HasLength {
  def length: Int
}

trait TokenProcessor[-T] {
  def process(item: T): Unit
}

// ============================================================================
// Generic container / sink / handler with variance
// ============================================================================

final class TokenContainer[+T](items: Seq[T]) {
  private val stored: Seq[T] = items

  def getAll: Seq[T] = stored

  def size: Int = stored.size

  def mapTokens(func: T => String): List[String] = stored.iterator.map(func).toList
}

final class TokenSink[-T] {
  private val received: mutable.ListBuffer[Any] = mutable.ListBuffer.empty

  def receive(item: T): Unit = received += item

  def drain(): List[Any] = {
    val out = received.toList
    received.clear()
    out
  }
}

final class BivariantHandler[T](default: T) {
  private var current: T = default

  def get: T = current

  def set(value: T): Unit = { current = value }

  def transform(func: T => T): T = {
    current = func(current)
    current
  }
}

// ============================================================================
// BaseTokenizer: abstract tokenization contract
// ============================================================================

abstract class BaseTokenizer[T] {
  def tokenize(value: T): Token

  def tokenizeBatch(values: Iterable[T]): Iterator[Token] =
    values.iterator.map(tokenize)
}

// ============================================================================
// StringTokenizer: handle String and Array[Byte] with optional normalization
// ============================================================================

final class StringTokenizer(
    val encoding: String = "UTF-8",
    val normalizer: String => String = identity
) extends BaseTokenizer[Any] {

  override def tokenize(value: Any): Token = {
    val asString: String = value match {
      case s: String     => s
      case b: Array[Byte] => new String(b, encoding)
      case other =>
        throw new IllegalArgumentException(
          s"Expected String or Array[Byte], got ${other.getClass.getName}"
        )
    }
    Token(normalizer(asString), TokenType.STRING)
  }
}

// ============================================================================
// NumericTokenizer: handle BigDecimal, Double, Float, Int, Long, Short, Byte
// ============================================================================

final class NumericTokenizer(
    val precision: Int = 6,
    val formatOptions: Map[String, Any] = Map.empty
) extends BaseTokenizer[Any] {

  override def tokenize(value: Any): Token = {
    val strValue: String = value match {
      case bd: BigDecimal =>
        bd.setScale(precision, BigDecimal.RoundingMode.HALF_UP).toString
      case jbd: java.math.BigDecimal =>
        Try(BigDecimal(jbd)).map(_.setScale(precision, BigDecimal.RoundingMode.HALF_UP).toString)
          .getOrElse(jbd.toString)
      case f: Float  => s"%.${precision}f".format(f.toDouble)
      case d: Double => s"%.${precision}f".format(d)
      case i: Int    => i.toString
      case l: Long   => l.toString
      case s: Short  => s.toString
      case b: Byte   => b.toString
      case n: Number => n.toString
      case other =>
        throw new IllegalArgumentException(
          s"Expected a numeric value, got ${other.getClass.getName}"
        )
    }
    Token(
      strValue,
      TokenType.NUMERIC,
      Map("original_type" -> value.getClass.getSimpleName)
    )
  }
}

// ============================================================================
// TemporalTokenizer: handle java.time.LocalDate / LocalDateTime
// ============================================================================

object TemporalTokenizer {
  final val ISO_FORMAT: String  = "yyyy-MM-dd'T'HH:mm:ss"
  final val DATE_FORMAT: String = "yyyy-MM-dd"
}

final class TemporalTokenizer(val formatStr: Option[String] = None)
    extends BaseTokenizer[Any] {

  override def tokenize(value: Any): Token = {
    val fmt: String = formatStr.getOrElse {
      value match {
        case _: LocalDateTime => TemporalTokenizer.ISO_FORMAT
        case _: LocalDate     => TemporalTokenizer.DATE_FORMAT
        case other =>
          throw new IllegalArgumentException(
            s"Expected java.time temporal type, got ${other.getClass.getName}"
          )
      }
    }
    val formatted: String = value match {
      case dt: LocalDateTime => dt.format(DateTimeFormatter.ofPattern(fmt))
      case d: LocalDate      => d.format(DateTimeFormatter.ofPattern(fmt))
      case other =>
        throw new IllegalArgumentException(
          s"Expected java.time temporal type, got ${other.getClass.getName}"
        )
    }
    Token(formatted, TokenType.TEMPORAL)
  }
}

// ============================================================================
// UniversalTokenizer: dispatch over runtime types
// ============================================================================

final class UniversalTokenizer {
  private val stringTokenizer:  StringTokenizer   = new StringTokenizer()
  private val numericTokenizer: NumericTokenizer  = new NumericTokenizer()
  private val temporalTokenizer: TemporalTokenizer = new TemporalTokenizer()

  val tokenizeNull: Token = Token("NULL", TokenType.NULL)

  def tokenize(value: Any): Token = value match {
    case null                       => Token("NULL", TokenType.NULL)
    case t: Tokenizable             => Token(t.toToken, TokenType.STRUCTURED)
    case s: String                  => stringTokenizer.tokenize(s)
    case b: Array[Byte]             => stringTokenizer.tokenize(b)
    case bd: BigDecimal             => numericTokenizer.tokenize(bd)
    case jbd: java.math.BigDecimal  => numericTokenizer.tokenize(jbd)
    case f: Float                   => numericTokenizer.tokenize(f)
    case d: Double                  => numericTokenizer.tokenize(d)
    case i: Int                     => numericTokenizer.tokenize(i)
    case l: Long                    => numericTokenizer.tokenize(l)
    case dt: LocalDateTime          => temporalTokenizer.tokenize(dt)
    case d: LocalDate               => temporalTokenizer.tokenize(d)
    case other =>
      Token(other.toString, TokenType.STRING, Map("fallback" -> true))
  }

  def tokenizeBatch(values: Iterable[Any]): Iterator[Token] =
    values.iterator.map(tokenize)
}

// ============================================================================
// TokenRegistry: keys -> TokenContainer[T] with optional handler pipeline
// ============================================================================

final class TokenRegistry[T] {
  private val registry: mutable.Map[String, TokenContainer[T]] = mutable.Map.empty
  private val handlers: mutable.ListBuffer[T => Option[Token]] = mutable.ListBuffer.empty

  def register(key: String, container: TokenContainer[T]): Unit = {
    registry(key) = container
  }

  def addHandler(handler: T => Option[Token]): Unit = {
    handlers += handler
  }

  def process(key: String): List[Option[Token]] = registry.get(key) match {
    case None => List.empty
    case Some(container) =>
      container.getAll.map { item =>
        handlers.iterator
          .map(h => h(item))
          .find(_.isDefined)
          .flatten
      }.toList
  }
}

// ============================================================================
// TokenFunctor / TokenMonad: lightweight functor/monad utilities
// ============================================================================

class TokenFunctor[T](val value: T) {
  def map[U](func: T => U): TokenFunctor[U] = new TokenFunctor(func(value))

  def flatMap[U](func: T => TokenFunctor[U]): TokenFunctor[U] = func(value)

  def get: T = value

  def getOrElse[U >: T](default: => U): U =
    if (value == null) default else value
}

class TokenMonad[T](value: T) extends TokenFunctor[T](value) {
  def ap[U](funcWrapped: TokenMonad[T => U]): TokenMonad[U] =
    new TokenMonad(funcWrapped.value(value))
}

object TokenMonad {
  def pure[T](value: T): TokenMonad[T] = new TokenMonad(value)
}

// ============================================================================
// JsonTokenizer: serialise JSON values and traverse by dotted path
// ============================================================================

final class JsonTokenizer(val pretty: Boolean = false) {

  def tokenize(value: Json): Token = {
    val rendered = if (pretty) value.spaces2 else value.noSpaces
    Token(rendered, TokenType.STRUCTURED, Map("json" -> true))
  }

  def tokenizePath(value: Json, path: String): Option[Token] = {
    val parts = path.split("\\.")
    var current: Option[Json] = Some(value)
    var keepGoing = true

    val it = parts.iterator
    while (it.hasNext && keepGoing) {
      val part = it.next()
      val next: Option[Json] = current.flatMap { node =>
        node.asObject.flatMap(_.apply(part))
          .orElse {
            node.asArray.flatMap { arr =>
              part.toIntOption.flatMap(arr.lift)
            }
          }
      }
      if (next.isEmpty) {
        keepGoing = false
        current = None
      } else {
        current = next
      }
    }
    current.map(tokenize)
  }
}

// ============================================================================
// WhitespaceTokenizer: split text on runs of whitespace
// ============================================================================

final class WhitespaceTokenizer(
    val lowercase: Boolean = false,
    val minLength: Int = 0,
    val maxLength: Option[Int] = None,
    val stripPunctuation: Boolean = false
) {

  private val punctuationChars: Set[Char] =
    Set('.', ',', '!', '?', ';', ':', '\'', '"', '(', ')', '[', ']', '{', '}')

  private val wordPattern = "\\S+".r

  private def stripEnds(s: String, chars: Set[Char]): String =
    s.dropWhile(chars.contains).reverse.dropWhile(chars.contains).reverse

  private def processToken(word: String): Option[String] = {
    var w = word
    if (stripPunctuation) w = stripEnds(w, punctuationChars)
    if (lowercase)        w = w.toLowerCase
    if (w.length < minLength) return None
    maxLength match {
      case Some(max) if w.length > max => w = w.substring(0, max)
      case _                           =>
    }
    if (w.isEmpty) None else Some(w)
  }

  def tokenize(text: String): List[Token] =
    wordPattern.findAllMatchIn(text).zipWithIndex.toList.flatMap { case (m, idx) =>
      processToken(m.matched).map { processed =>
        Token(
          processed,
          TokenType.STRING,
          Map("position" -> idx, "original" -> m.matched)
        )
      }
    }

  def tokenizeToStrings(text: String): List[String] =
    tokenize(text).map(_.value)

  def tokenizeWithPositions(text: String): List[(String, Int, Int)] =
    wordPattern.findAllMatchIn(text).toList.flatMap { m =>
      processToken(m.matched).map(p => (p, m.start, m.end))
    }

  def countTokens(text: String): Int = tokenize(text).length
}

// ============================================================================
// TokenizerBuilder: fluent builder that returns a T => Token function
// ============================================================================

final class TokenizerBuilder[T] {
  private val normalizers: mutable.ListBuffer[String => String] = mutable.ListBuffer.empty
  private val validators:  mutable.ListBuffer[T => Boolean]    = mutable.ListBuffer.empty
  private val metadata:    mutable.Map[String, Any]           = mutable.Map.empty

  def withNormalizer(normalizer: String => String): TokenizerBuilder[T] = {
    normalizers += normalizer
    this
  }

  def withValidator(validator: T => Boolean): TokenizerBuilder[T] = {
    validators += validator
    this
  }

  def withMetadata(extra: Map[String, Any]): TokenizerBuilder[T] = {
    metadata ++= extra
    this
  }

  def withMetadata(pairs: (String, Any)*): TokenizerBuilder[T] =
    withMetadata(pairs.toMap)

  def build(): T => Token = {
    val normList   = normalizers.toList
    val validList  = validators.toList
    val metaSnap   = metadata.toMap

    (value: T) => {
      if (!validList.forall(v => v(value))) {
        throw new IllegalArgumentException(s"Validation failed for $value")
      }
      var str = value.toString
      normList.foreach(n => str = n(str))
      Token(str, TokenType.STRING, metaSnap)
    }
  }
}

object TokenizerBuilder {
  def apply[T](): TokenizerBuilder[T] = new TokenizerBuilder[T]()
}

// ============================================================================
// Package object: top-level convenience functions
// ============================================================================

package object tokenizer {

  private val universalTokenizer: UniversalTokenizer = new UniversalTokenizer()

  val tokenizeNull: Token = Token("NULL", TokenType.NULL)

  def tokenize(value: Any): Token = universalTokenizer.tokenize(value)

  def tokenizeBatch(values: Iterable[Any]): Iterator[Token] =
    universalTokenizer.tokenizeBatch(values)

  def toToken(value: Any): String = value match {
    case null         => "NULL"
    case t: Tokenizable => t.toToken
    case other        => other.toString
  }

  def withMetadata(token: Token, extra: Map[String, Any]): Token =
    token.withMetadata(extra)
}