import unittest
from detect_headings import _reconstruct_block_lines


def glyph(c, x, y=10, size=10):
    return dict(c=c, x0=x, x1=x+4, y0=y, y1=y+size, size=size)


class SubscriptTests(unittest.TestCase):
    def test_lowered_digits_stay_inside_formula(self):
        chars = [glyph("H", 0), glyph("2", 4, 16, 6), glyph("O", 8)]
        self.assertEqual([r["text"] for r in _reconstruct_block_lines(chars)], ["H2O"])

    def test_separate_numeric_line_is_not_a_subscript(self):
        chars = [glyph("H", 0), glyph("O", 8), glyph("2", 4, 25, 6)]
        self.assertEqual([r["text"] for r in _reconstruct_block_lines(chars)], ["HO", "2"])

    def test_full_size_digit_is_not_merged(self):
        chars = [glyph("H", 0), glyph("2", 4, 16, 10)]
        self.assertEqual(len(_reconstruct_block_lines(chars)), 2)

    def test_mixed_superscript_charge_is_not_flattened(self):
        chars = [glyph("S", 0), glyph("O", 4), glyph("2", 8, 10, 6), glyph("4", 8, 16, 6)]
        self.assertNotIn("SO24", [r["text"] for r in _reconstruct_block_lines(chars)])


if __name__ == "__main__":
    unittest.main()
