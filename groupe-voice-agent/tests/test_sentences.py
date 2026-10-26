import unittest

from voiceagent.sentences import SentenceBuffer


class SentenceTests(unittest.TestCase):
    def test_token_boundaries_and_multiple_sentences(self):
        buffer = SentenceBuffer()
        self.assertEqual(buffer.feed("Bon"), [])
        self.assertEqual(buffer.feed("jour. Comment ça va ? "), ["Bonjour.", "Comment ça va ?"])

    def test_decimal_split_across_tokens(self):
        buffer = SentenceBuffer()
        self.assertEqual(buffer.feed("La valeur est 3."), [])
        self.assertEqual(buffer.feed("14. Ensuite "), ["La valeur est 3.14."])

    def test_abbreviation(self):
        buffer = SentenceBuffer()
        self.assertEqual(buffer.feed("M. Dupont parle. "), ["M. Dupont parle."])

    def test_comma_after_eight_words(self):
        buffer = SentenceBuffer()
        text = "un deux trois quatre cinq six sept huit, ensuite"
        self.assertEqual(buffer.feed(text), ["un deux trois quatre cinq six sept huit,"])

    def test_short_comma_waits(self):
        buffer = SentenceBuffer()
        self.assertEqual(buffer.feed("Oui, bien sûr. "), ["Oui, bien sûr."])

    def test_long_unpunctuated_text_is_bounded(self):
        buffer = SentenceBuffer(max_chars=24)
        parts = buffer.feed("Bonjour " * 20)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 24 for part in parts))
        self.assertLess(len(buffer.buffer), 24)

    def test_unbroken_long_word(self):
        buffer = SentenceBuffer(max_chars=20)
        self.assertEqual(buffer.feed("x" * 45), ["x" * 20, "x" * 20])
        self.assertEqual(buffer.flush(), ["x" * 5])
        self.assertEqual(buffer.flush(), [])
